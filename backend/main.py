import os
import re
import time
import threading
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

import serial
import serial.tools.list_ports
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


def parse_grbl_status(frame: Optional[str]):
    """Parse a raw GRBL status frame like '<Idle|MPos:12.480,8.220,2.140|FS:0,0>'."""
    if not frame:
        return {"state": None, "x": None, "y": None, "z": None}

    m = re.match(r"<([^|>]+)\|?(.*)>", frame)
    if not m:
        return {"state": None, "x": None, "y": None, "z": None}

    state = m.group(1)
    x = y = z = None
    for part in m.group(2).split("|"):
        if part.startswith("MPos:") or part.startswith("WPos:"):
            coords = part.split(":", 1)[1].split(",")
            if len(coords) >= 3:
                try:
                    x, y, z = float(coords[0]), float(coords[1]), float(coords[2])
                except ValueError:
                    pass
            break

    return {"state": state, "x": x, "y": y, "z": z}


# ======================
# Config
# ======================
DEFAULT_PORT = os.getenv("GRBL_PORT", "/dev/ttyACM0")
DEFAULT_BAUD = int(os.getenv("GRBL_BAUD", "115200"))
SER_TIMEOUT = float(os.getenv("GRBL_TIMEOUT", "1.0"))

CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]


# ======================
# Serial Manager
# ======================
class GRBL:
    def __init__(self):
        self.ser: Optional[serial.Serial] = None
        self.lock = threading.Lock()
        self.port = DEFAULT_PORT
        self.baud = DEFAULT_BAUD

    def connected(self) -> bool:
        return self.ser is not None and self.ser.is_open

    def connect(self, port: str, baud: int):
        with self.lock:
            if self.connected():
                return

            self.port = port
            self.baud = baud
            try:
                self.ser = serial.Serial(
                    port=port,
                    baudrate=baud,
                    timeout=SER_TIMEOUT,
                    write_timeout=SER_TIMEOUT,
                )
            except Exception as e:
                self.ser = None
                raise RuntimeError(f"Cannot open {port}: {e}")

            # Many Arduinos reset on serial open
            time.sleep(2.0)
            self._flush()

    def disconnect(self):
        with self.lock:
            if self.ser:
                try:
                    self.ser.close()
                finally:
                    self.ser = None

    def _flush(self):
        if not self.ser:
            return
        try:
            self.ser.reset_input_buffer()
            self.ser.reset_output_buffer()
        except Exception:
            pass

    def _read_lines(self, seconds: float = 0.8):
        if not self.ser:
            return []
        out = []
        end = time.time() + seconds
        while time.time() < end:
            try:
                line = self.ser.readline().decode(errors="ignore").strip()
            except Exception:
                line = ""
            if line:
                out.append(line)
        return out

    def send_line(self, line: str, read_window: float = 1.0):
        with self.lock:
            if not self.connected():
                raise RuntimeError("GRBL not connected")

            payload = (line.strip() + "\n").encode()
            try:
                self.ser.write(payload)
                self.ser.flush()
            except Exception as e:
                raise RuntimeError(f"Write failed: {e}")

            return self._read_lines(read_window)

    def realtime(self, ch: str):
        with self.lock:
            if not self.connected():
                raise RuntimeError("GRBL not connected")
            try:
                self.ser.write(ch.encode())  # '!' or '~'
                self.ser.flush()
            except Exception as e:
                raise RuntimeError(f"Realtime write failed: {e}")

    def status(self):
        with self.lock:
            if not self.connected():
                raise RuntimeError("GRBL not connected")
            try:
                self.ser.write(b"?")
                self.ser.flush()
            except Exception as e:
                raise RuntimeError(f"Status query failed: {e}")

            lines = self._read_lines(0.5)
            frames = [l for l in lines if l.startswith("<") and l.endswith(">")]
            return frames[-1] if frames else None


grbl = GRBL()


# ======================
# API
# ======================
app = FastAPI(title="GRBL Live Mode API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=86400,
)


# ======================
# Models
# ======================
class ConnectReq(BaseModel):
    port: str = DEFAULT_PORT
    baud: int = DEFAULT_BAUD


class JogReq(BaseModel):
    dx_mm: float        # + forward, - backward (applied along `axis`)
    feed: float = 300   # mm/min
    axis: str = "X"     # X, Y, or Z


class SetupReq(BaseModel):
    soft_limits: int = 0     # $20 (0=off, 1=on)
    max_feed_x: int = 1000   # $110 (mm/min)


# ======================
# Routes
# ======================
@app.get("/api/health")
def health():
    return {
        "ok": True,
        "connected": grbl.connected(),
        "port": grbl.port,
        "baud": grbl.baud,
    }

@app.get("/api/ports")
def list_ports():
    """Enumerate serial ports Windows/pyserial currently sees, so the UI can
    offer a real picker instead of guessing a hardcoded COM port."""
    ports = [
        {"device": p.device, "description": p.description, "hwid": p.hwid}
        for p in serial.tools.list_ports.comports()
    ]
    return {"ok": True, "ports": ports}


@app.post("/api/connect")
def connect(req: ConnectReq):
    try:
        grbl.connect(req.port, req.baud)

        # ---- AUTO SETUP FOR LIVE MODE (ONE TIME PER CONNECT) ----
        setup_out = []
        setup_out += grbl.send_line("$20=0", 1.0)    # disable soft limits
        setup_out += grbl.send_line("$110=1000", 1.0)  # raise X max feed
        # --------------------------------------------------------

        banner = grbl._read_lines(0.6)

        return {
            "ok": True,
            "connected": True,
            "banner": banner,
            "setup": setup_out,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/setup")
def setup(req: SetupReq):
    """
    Dev-friendly defaults so JOG won't be blocked:
      - disable soft limits ($20=0)
      - raise X max feed ($110=1000)
    """
    try:
        out = []
        out += grbl.send_line(f"$20={int(req.soft_limits)}", 1.0)
        out += grbl.send_line(f"$110={int(req.max_feed_x)}", 1.0)
        return {"ok": True, "response": out}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/unlock")
def unlock():
    try:
        lines = grbl.send_line("$X", 1.0)
        return {"ok": True, "response": lines}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/status")
def status():
    try:
        frame = grbl.status()
        parsed = parse_grbl_status(frame)
        return {"ok": True, "status": frame, **parsed}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/jog")
def jog(req: JogReq):
    """
    Robust GRBL 1.1 jog:
      - G91 incremental
      - G21 millimeters (IMPORTANT)
      - <axis>... F...
    """
    axis = req.axis.strip().upper()
    if axis not in ("X", "Y", "Z"):
        raise HTTPException(status_code=400, detail=f"Invalid axis: {req.axis!r}")
    try:
        cmd = f"$J=G91 G21 {axis}{req.dx_mm:.3f} F{req.feed:.1f}"
        lines = grbl.send_line(cmd, 1.2)
        return {"ok": True, "sent": cmd, "response": lines}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/home")
def home():
    """GRBL homing cycle ($H). Can take several seconds on real hardware."""
    try:
        lines = grbl.send_line("$H", 8.0)
        return {"ok": True, "response": lines}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/stop")
def stop():
    try:
        grbl.realtime("!")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/resume")
def resume():
    try:
        grbl.realtime("~")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/disconnect")
def disconnect():
    grbl.disconnect()
    return {"ok": True, "connected": False}
