# HealthVue Micra — Embedded Software

On-device UI for a motorized digital-pathology microscope: live camera feed, GRBL-driven X/Y/Z stage control, slide review, and reporting.

The project has two parts that run as separate processes:

- **Frontend** (this folder) — React 19 + Vite. The kiosk UI shown on the device's touchscreen (or any browser pointed at it).
- **Backend** (`backend/`) — FastAPI + pyserial. Bridges the frontend to a GRBL 1.1 motion controller over USB serial.

```
Browser (React UI + camera via getUserMedia)
        │  HTTP (fetch, polled every 700ms)
        ▼
FastAPI backend (backend/main.py)
        │  USB serial, GRBL g-code / $-commands
        ▼
GRBL controller (Arduino Uno + CNC Shield)
        │  STEP/DIR
        ▼
TMC2209 / A4988 driver → stepper motor → linear stage (X, Y, Z/focus)
```

## Status: what's real vs. simulated

| Feature | State |
|---|---|
| Live Mode → Stage panel (X/Y/Z jog, home, unlock, position) | **Real**, via the backend/GRBL — but falls back to a fully simulated stage if the backend isn't running or no GRBL is connected, so the UI still works standalone |
| Live Mode → camera feed, Snapshot, Record | **Real**, via the browser's own camera API (`getUserMedia`/`MediaRecorder`). Requires a USB/UVC camera and browser permission |
| Live Mode → Capture to WSI | Captures the **current single field only**. Full whole-slide capture (driving the stage across a tile grid, stitching overlapping fields into one image) is not implemented |
| Home, Slides, Slide Viewer, Reports, Developer Console | UI mockups with hardcoded/simulated data — not wired to any backend |

## Running the frontend — step by step

1. **Prerequisite**: Node.js 18+ installed (`node -v` to check).
2. Open a terminal in the project root (the folder containing this `README.md`).
3. Install dependencies (first time only, or whenever `package.json` changes):
   ```
   npm install
   ```
4. Check `.env` in the project root and set it to match your backend (see table below). The defaults work as-is if you're running the backend on the same machine on port 8000.
5. Start the dev server:
   ```
   npm run dev
   ```
6. Open `http://localhost:3000` in a browser. The UI loads and works even with no backend/hardware running — Live Mode falls back to a simulated stage automatically.
7. To stop the server, press `Ctrl+C` in that terminal.

`.env` (project root):

| Var | Meaning | Default |
|---|---|---|
| `VITE_GRBL_API` | Backend base URL | `http://localhost:8000` |
| `VITE_GRBL_PORT` | Serial port the backend should open on connect | `COM3` (placeholder — set to your real port) |
| `VITE_GRBL_BAUD` | Serial baud rate | `115200` |

Other useful commands:

| Command | Does |
|---|---|
| `npm run build` | Production build to `dist/` |
| `npm run preview` | Serve the production build locally |
| `npm run lint` | Run Oxlint |

## Running the backend — step by step

See [`backend/README.md`](backend/README.md) for the full walkthrough (Python setup, finding your serial port, Linux permissions). Short version once it's configured:

```
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

## Running both together

1. Terminal 1: start the backend (`cd backend && uvicorn main:app --reload --port 8000`).
2. Terminal 2: start the frontend (`npm run dev` from the project root).
3. Open `http://localhost:3000`, go to **Live**. If the backend is reachable, it auto-attempts to connect to the GRBL controller on `VITE_GRBL_PORT` every few seconds until it succeeds; once connected, the Stage panel switches from simulated to real position/status.

## Project layout

```
src/
  views/          One component per screen (Home, Live, Repository, Viewer, Report, Console)
  components/      NavRail, StatusBar, BootOverlay, HChat, ToastStack
  hooks/
    useGrblStage.js   Polls the backend, exposes jog()/home()/unlock(), falls back to simulated
                       state when hardware isn't reachable
    useCamera.js       Browser camera capture, snapshot, recording
    useToast.js        Small toast-notification queue
backend/
  main.py           FastAPI service, talks to GRBL over serial
  requirements.txt
  .env              GRBL_PORT / GRBL_BAUD / GRBL_TIMEOUT
```

## Known placeholders — check before relying on these for real hardware

- **Stage travel limits** (`TRAVEL_LIMITS` in `src/views/LiveView.jsx`): `x: 0–26mm, y: 0–76mm, z: 0–4mm`. These are placeholders sized to a standard 25×75mm microscope slide, **not derived from your actual stage's leadscrew travel**. Update this constant once the real hardware's travel range is known.
- **GRBL soft limits**: `backend/main.py`'s `/api/connect` sends `$20=0` (disables GRBL's own hardware soft-limit checking) on every connect — this persists to the controller's EEPROM. The frontend travel-limit clamp above is the only software guard against over-travel; it's not a substitute for physical limit switches.
- **Steps/mm calibration** (GRBL `$100`/`$101`/`$102`): must be set on the controller itself based on your specific motor steps/rev, microstepping, and leadscrew pitch — `steps/mm = (motor_steps_per_rev × microsteps) / mm_per_revolution`. This isn't part of this codebase; send it as a raw `$`-command over serial once, it persists on the controller.
