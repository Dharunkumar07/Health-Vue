# #!/usr/bin/env python3
# """
# Arducam Jetson MIPI camera — headless streamer.

# Same capture path as arducam_displayer_2mp.py, but instead of cv2.imshow()
# the frames are JPEG-encoded and served as MJPEG over HTTP, so the live view
# lands in a browser on your laptop through the SSH tunnel / Tailscale.

#     cd ~/Desktop/jetson_files/MIPI_Camera/Jetson/Jetvariety/example
#     /usr/bin/python3 arducam_stream_2mp.py --fps

# Then open http://localhost:8000 on your laptop (VS Code forwards the port
# automatically; otherwise: ssh -L 8000:localhost:8000 healthvue@<jetson>).

# Keyboard shortcuts are replaced by the web UI:
#     's' to save  ->  the Save frame button (or GET /snap)
#     'q' to quit  ->  Ctrl-C in the terminal
# """

# import argparse
# import os
# import sys
# import threading
# import time
# from datetime import datetime
# from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
# from urllib.parse import urlparse, parse_qs

# try:
#     import cv2
# except ImportError:
#     print("Start to install opencv...")
#     os.system('sudo apt-get update')
#     os.system('sudo apt install nvidia-opencv-dev')
#     import cv2

# import numpy as np
from stain_smear import enhance as stain_enhance

# from utils import ArducamUtils


# # ---------------------------------------------------------------------------
# # White balance
# # ---------------------------------------------------------------------------

# def build_wb_lut(b_gain, g_gain, r_gain):
#     """256-entry saturating lookup table, one column per BGR channel.

#     Replaces the per-frame float64 multiply. cv2.LUT is a single pass over
#     uint8 data and is roughly an order of magnitude cheaper at 2MP.
#     """
#     ramp = np.arange(256, dtype=np.float32)
#     lut = np.empty((256, 1, 3), dtype=np.uint8)
#     for i, gain in enumerate((b_gain, g_gain, r_gain)):
#         lut[:, 0, i] = np.clip(ramp * gain, 0, 255).astype(np.uint8)
#     return lut


# # ---------------------------------------------------------------------------
# # Capture
# # ---------------------------------------------------------------------------

# class CameraThread:
#     def __init__(self, cap, arducam_utils, gains, report_fps=False):
#         self.cap = cap
#         self.arducam_utils = arducam_utils
#         self.report_fps = report_fps

#         # Cached once — the original called cap.get() on every single frame.
#         self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
#         self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
#         self.needs_reshape = (arducam_utils.convert2rgb == 0)

#         self.b_gain, self.g_gain, self.r_gain = gains
#         self.lut = build_wb_lut(self.b_gain, self.g_gain, self.r_gain)

#         self.frame = None
#         self.fps = 0.0
#         self.frames_seen = 0
#         self.lock = threading.Lock()
#         self.running = True
#         self.thread = threading.Thread(target=self._loop, daemon=True)
#         self.thread.start()

#     def set_gains(self, b_gain, g_gain, r_gain):
#         lut = build_wb_lut(b_gain, g_gain, r_gain)
#         with self.lock:
#             self.b_gain, self.g_gain, self.r_gain = b_gain, g_gain, r_gain
#             self.lut = lut

#     def _loop(self):
#         last_report = time.time()
#         frame_count = 0
#         last = time.monotonic()
#         smoothed = 0.0

#         while self.running:
#             ret, frame = self.cap.read()
#             if not ret:
#                 time.sleep(0.005)
#                 continue

#             if self.needs_reshape:
#                 frame = frame.reshape(self.height, self.width)

#             frame = self.arducam_utils.convert(frame)

#             if frame.dtype != np.uint8:
#                 frame = np.clip(frame, 0, 255).astype(np.uint8)

#             with self.lock:
#                 lut = self.lut
#             frame = cv2.LUT(frame, lut)

#             now = time.monotonic()
#             dt = now - last
#             last = now
#             if dt > 0:
#                 inst = 1.0 / dt
#                 smoothed = inst if smoothed == 0 else 0.9 * smoothed + 0.1 * inst

#             with self.lock:
#                 self.frame = frame
#                 self.fps = smoothed
#                 self.frames_seen += 1

#             frame_count += 1
#             if self.report_fps and time.time() - last_report >= 1:
#                 print("fps: {}".format(frame_count), end='\r')
#                 sys.stdout.flush()
#                 last_report = time.time()
#                 frame_count = 0

#     def read(self):
#         with self.lock:
#             if self.frame is None:
#                 return None, 0.0
#             return self.frame, self.fps

#     def gains(self):
#         with self.lock:
#             return self.b_gain, self.g_gain, self.r_gain

#     def stop(self):
#         self.running = False
#         self.thread.join(timeout=1.0)


# # ---------------------------------------------------------------------------
# # Web UI
# # ---------------------------------------------------------------------------

# PAGE = """<!doctype html>
# <meta charset="utf-8">
# <meta name="viewport" content="width=device-width, initial-scale=1">
# <title>Arducam live</title>
# <style>
#   :root { color-scheme: dark; }
#   * { box-sizing: border-box; }
#   body { margin:0; height:100vh; display:flex; flex-direction:column;
#          background:#0E1822; color:#c9d6e2;
#          font:13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
#   header { display:flex; align-items:baseline; gap:18px; flex-wrap:wrap;
#            padding:10px 16px; border-bottom:1px solid #1d2c3b; }
#   h1 { margin:0; font-size:12px; letter-spacing:.16em; text-transform:uppercase;
#        font-weight:600; color:#5DA3E6; }
#   .meta { color:#5f7488; }
#   main { flex:1; min-height:0; display:grid; place-items:center; padding:14px; }
#   img { max-width:100%; max-height:100%; object-fit:contain;
#         border:1px solid #1d2c3b; background:#000; }
#   footer { display:flex; align-items:center; gap:22px; flex-wrap:wrap;
#            padding:10px 16px; border-top:1px solid #1d2c3b; }
#   label { display:flex; align-items:center; gap:8px; }
#   label span { width:52px; color:#5f7488; }
#   input[type=range] { width:130px; accent-color:#5DA3E6; }
#   output { width:34px; text-align:right; }
#   button { font:inherit; color:#0E1822; background:#D4A248; border:0;
#            padding:6px 14px; cursor:pointer; }
#   button:hover { background:#e0b463; }
#   button:focus-visible, input:focus-visible { outline:2px solid #5DA3E6;
#            outline-offset:2px; }
#   #status { color:#5f7488; }
# </style>
# <header>
#   <h1>Arducam live</h1>
#   <span class="meta">__META__</span>
# </header>
# <main><img src="/stream" alt="Live camera feed"></main>
# <footer>
#   <label><span>Red</span><input type="range" id="r" min="0.5" max="4" step="0.01" value="__R__"><output id="ro"></output></label>
#   <label><span>Green</span><input type="range" id="g" min="0.5" max="4" step="0.01" value="__G__"><output id="go"></output></label>
#   <label><span>Blue</span><input type="range" id="b" min="0.5" max="4" step="0.01" value="__B__"><output id="bo"></output></label>
#   <button id="save">Save frame</button>
#   <span id="status"></span>
# </footer>
# <script>
#   const ids = ['r','g','b'];
#   const show = () => ids.forEach(i =>
#     document.getElementById(i+'o').textContent =
#       Number(document.getElementById(i).value).toFixed(2));
#   const push = () => {
#     const q = ids.map(i => i+'='+document.getElementById(i).value).join('&');
#     fetch('/wb?'+q);
#   };
#   ids.forEach(i => document.getElementById(i)
#     .addEventListener('input', () => { show(); push(); }));
#   show();
#   document.getElementById('save').addEventListener('click', async () => {
#     const s = document.getElementById('status');
#     s.textContent = 'Saving...';
#     const res = await fetch('/snap');
#     s.textContent = res.ok ? 'Saved ' + (await res.text()) : 'Save failed';
#   });
# </script>
# """


# class Handler(BaseHTTPRequestHandler):
#     camera = None
#     quality = 80
#     save_dir = "."
#     info = ""

#     def log_message(self, *args):
#         pass

#     def _text(self, body, code=200, ctype="text/plain; charset=utf-8"):
#         data = body.encode()
#         self.send_response(code)
#         self.send_header("Content-Type", ctype)
#         self.send_header("Content-Length", str(len(data)))
#         self.send_header("Cache-Control", "no-store")
#         self.end_headers()
#         self.wfile.write(data)

#     def do_GET(self):
#         parsed = urlparse(self.path)
#         route = parsed.path

#         if route in ("/", "/index.html"):
#             b, g, r = self.camera.gains()
#             page = (PAGE.replace("__META__", self.info)
#                         .replace("__R__", f"{r:.2f}")
#                         .replace("__G__", f"{g:.2f}")
#                         .replace("__B__", f"{b:.2f}"))
#             self._text(page, ctype="text/html; charset=utf-8")
#             return

#         if route == "/wb":
#             q = parse_qs(parsed.query)
#             b, g, r = self.camera.gains()
#             try:
#                 r = float(q.get("r", [r])[0])
#                 g = float(q.get("g", [g])[0])
#                 b = float(q.get("b", [b])[0])
#             except ValueError:
#                 self._text("gains must be numbers", 400)
#                 return
#             self.camera.set_gains(b, g, r)
#             self._text(f"{r:.2f} {g:.2f} {b:.2f}")
#             return

#         if route == "/snap":
#             frame, _ = self.camera.read()
#             if frame is None:
#                 self._text("no frame yet", 503)
#                 return
#             name = "capture_{}.png".format(
#                 datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3])
#             path = os.path.join(self.save_dir, name)
#             cv2.imwrite(path, frame)
#             print("Saved {}".format(path))
#             self._text(name)
#             return

#         if route == "/stream":
#             self.send_response(200)
#             self.send_header("Content-Type",
#                              "multipart/x-mixed-replace; boundary=frame")
#             self.send_header("Cache-Control", "no-store")
#             self.end_headers()
#             encode_params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
#             last_seen = -1
#             try:
#                 while True:
#                     with self.camera.lock:
#                         seen = self.camera.frames_seen
#                     if seen == last_seen:
#                         time.sleep(0.002)
#                         continue
#                     last_seen = seen

#                     frame, fps = self.camera.read()
#                     if frame is None:
#                         time.sleep(0.01)
#                         continue

#                     ok, jpg = cv2.imencode(".jpg", frame, encode_params)
#                     if not ok:
#                         continue
#                     payload = jpg.tobytes()
#                     self.wfile.write(
#                         b"--frame\r\nContent-Type: image/jpeg\r\n"
#                         b"Content-Length: " + str(len(payload)).encode() +
#                         b"\r\n\r\n")
#                     self.wfile.write(payload)
#                     self.wfile.write(b"\r\n")
#             except (BrokenPipeError, ConnectionResetError):
#                 return
#             return

#         self.send_error(404)


# # ---------------------------------------------------------------------------
# # Setup
# # ---------------------------------------------------------------------------

# def fourcc(a, b, c, d):
#     return ord(a) | (ord(b) << 8) | (ord(c) << 16) | (ord(d) << 24)


# def pixelformat(string):
#     if len(string) not in (3, 4):
#         raise argparse.ArgumentTypeError(
#             "{} is not a pixel format".format(string))
#     if len(string) == 3:
#         return fourcc(string[0], string[1], string[2], ' ')
#     return fourcc(string[0], string[1], string[2], string[3])


# def show_info(arducam_utils):
#     _, firmware_version = arducam_utils.read_dev(ArducamUtils.FIRMWARE_VERSION_REG)
#     _, sensor_id = arducam_utils.read_dev(ArducamUtils.FIRMWARE_SENSOR_ID_REG)
#     _, serial_number = arducam_utils.read_dev(ArducamUtils.SERIAL_NUMBER_REG)
#     print("Firmware Version: {}".format(firmware_version))
#     print("Sensor ID: 0x{:04X}".format(sensor_id))
#     print("Serial Number: 0x{:08X}".format(serial_number))
#     return sensor_id


# def main():
#     parser = argparse.ArgumentParser(
#         description='Arducam Jetson MIPI camera streamer.')
#     parser.add_argument('-d', '--device', default=0, type=int, nargs='?',
#                         help='/dev/videoX default is 0')
#     parser.add_argument('-f', '--pixelformat', type=pixelformat,
#                         help="set pixelformat")
#     parser.add_argument('--width', type=lambda x: int(x, 0),
#                         help="set width of image")
#     parser.add_argument('--height', type=lambda x: int(x, 0),
#                         help="set height of image")
#     parser.add_argument('--fps', action='store_true', help="print fps")
#     parser.add_argument('--channel', type=int, default=-1, nargs='?',
#                         help="Camarray single-channel switch")
#     parser.add_argument('-p', '--port', type=int, default=8000,
#                         help="HTTP port (default 8000)")
#     parser.add_argument('-q', '--quality', type=int, default=80,
#                         help="JPEG quality 1-100; lower for slower links")
#     parser.add_argument('--save-dir', default=".",
#                         help="where Save frame writes PNGs")
#     parser.add_argument('--gains', default="2.14,1.55,2.14",
#                         help="starting R,G,B gains")
#     args = parser.parse_args()

#     r_gain, g_gain, b_gain = [float(x) for x in args.gains.split(",")]

#     print("opening {}".format(args.device))
#     cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
#     if not cap.isOpened():
#         sys.exit("could not open /dev/video{}".format(args.device))

#     if args.pixelformat is not None:
#         if not cap.set(cv2.CAP_PROP_FOURCC, args.pixelformat):
#             print("Failed to set pixel format.")

#     arducam_utils = ArducamUtils(args.device)
#     sensor_id = show_info(arducam_utils)

#     if arducam_utils.convert2rgb == 0:
#         cap.set(cv2.CAP_PROP_CONVERT_RGB, arducam_utils.convert2rgb)
#     if args.width is not None:
#         cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
#     if args.height is not None:
#         cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
#     if args.channel in range(0, 4):
#         arducam_utils.write_dev(ArducamUtils.CHANNEL_SWITCH_REG, args.channel)

#     os.makedirs(args.save_dir, exist_ok=True)

#     camera = CameraThread(cap, arducam_utils,
#                           (b_gain, g_gain, r_gain), args.fps)

#     Handler.camera = camera
#     Handler.quality = args.quality
#     Handler.save_dir = args.save_dir
#     Handler.info = "{}x{} &middot; sensor 0x{:04X}".format(
#         camera.width, camera.height, sensor_id)

#     server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
#     print("streaming on http://0.0.0.0:{}  (Ctrl-C to stop)".format(args.port))
#     try:
#         server.serve_forever()
#     except KeyboardInterrupt:
#         print("\nstopping")
#     finally:
#         camera.stop()
#         server.server_close()
#         cap.release()


# if __name__ == "__main__":
#     main()

#!/usr/bin/env python3
"""
Arducam Jetson MIPI camera — headless streamer with brightfield colour
calibration for peripheral blood smear microscopy.

Pipeline (all in linear light, in this order):

    demosaic -> subtract dark -> multiply gain map -> CCM -> sRGB encode

The gain map is target / (flat - dark), computed per pixel per channel, so
it performs white balance and flat-field (shading) correction in one pass.
This replaces the fixed 2.14/1.55/2.14 channel gains, which cannot correct
illumination falloff and drift with every lamp voltage change.

    cd ~/Desktop/jetson_files/MIPI_Camera/Jetson/Jetvariety/example
    /usr/bin/python3 arducam_pbs_stream.py --fps

Open http://localhost:8000 on your laptop, then run the calibration in the
order printed on the page. Calibration persists to pbs_calib.npz.
"""

import argparse
import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import cv2
import numpy as np

from utils import ArducamUtils


CALIB_FILE = "pbs_calib.npz"

# Rec.709 luma weights, BGR order, for the saturation matrix.
LUMA_BGR = np.array([0.0722, 0.7152, 0.2126], dtype=np.float32)


# ---------------------------------------------------------------------------
# Colour maths
# ---------------------------------------------------------------------------

def srgb_lut(levels=4096):
    """Linear -> sRGB encode, as a lookup table indexed by 12-bit linear."""
    x = np.linspace(0.0, 1.0, levels, dtype=np.float64)
    y = np.where(x <= 0.0031308, 12.92 * x, 1.055 * np.power(x, 1 / 2.4) - 0.055)
    return np.clip(y * 255.0 + 0.5, 0, 255).astype(np.uint8)


def saturation_matrix(s):
    """Symmetric linear-light saturation matrix in BGR order.

    s = 1.0 leaves the sensor's native response alone. Values above 1.0
    partially compensate for the broad, overlapping colour filter array
    response that makes uncalibrated microscope sensors read flat. This is a
    stand-in for a measured CCM, not a substitute for one.
    """
    return (np.eye(3, dtype=np.float32) * s
            + (1.0 - s) * np.tile(LUMA_BGR, (3, 1)))


def build_gain_map(flat, dark, bg_target, max_gain=8.0):
    """Per-pixel per-channel gain that maps the blank field to bg_target."""
    denom = flat.astype(np.float32) - dark
    # Guard against dead pixels and fully vignetted corners turning into
    # noise amplifiers.
    denom = np.maximum(denom, 1.0)
    gain = bg_target / denom
    return np.clip(gain, 0.0, max_gain).astype(np.float32)


# ---------------------------------------------------------------------------
# Calibration state
# ---------------------------------------------------------------------------

class Calibration:
    def __init__(self, shape, bg_target=0.85, saturation=1.0, max_gain=8.0):
        self.shape = shape
        self.bg_target = bg_target
        self.max_gain = max_gain
        self.dark = None          # float32 HxWx3, sensor + stray light floor
        self.flat = None          # float32 HxWx3, blank illuminated field
        self.gain_map = None      # float32 HxWx3
        self.ccm = None           # float32 3x3 in BGR, or None for identity
        self.saturation = saturation
        self.gamma = srgb_lut()
        self.note = ""
        self.set_saturation(saturation)

    # -- state transitions ---------------------------------------------

    def set_saturation(self, s):
        self.saturation = float(s)
        self.ccm = None if abs(s - 1.0) < 1e-3 else saturation_matrix(s)

    def set_dark(self, dark):
        self.dark = dark.astype(np.float32)
        self._recompute()

    def set_flat(self, flat):
        self.flat = flat.astype(np.float32)
        self._recompute()

    def clear(self):
        self.dark = self.flat = self.gain_map = None
        self.note = ""

    def _recompute(self):
        if self.flat is None:
            self.gain_map = None
            return
        dark = self.dark if self.dark is not None else np.zeros_like(self.flat)
        self.gain_map = build_gain_map(self.flat, dark,
                                       self.bg_target, self.max_gain)

    @property
    def ready(self):
        return self.gain_map is not None

    # -- persistence ---------------------------------------------------

    def save(self, path=CALIB_FILE):
        np.savez_compressed(
            path,
            dark=self.dark if self.dark is not None else np.zeros(0),
            flat=self.flat if self.flat is not None else np.zeros(0),
            bg_target=self.bg_target,
            saturation=self.saturation,
            captured=datetime.now().isoformat(timespec="seconds"))

    def load(self, path=CALIB_FILE):
        if not os.path.exists(path):
            return False
        data = np.load(path)
        flat = data["flat"]
        if flat.size == 0 or flat.shape != self.shape:
            print("calibration in {} does not match current frame size — "
                  "ignoring".format(path))
            return False
        dark = data["dark"]
        self.dark = dark.astype(np.float32) if dark.size else None
        self.flat = flat.astype(np.float32)
        self.bg_target = float(data["bg_target"])
        self.set_saturation(float(data["saturation"]))
        self._recompute()
        print("loaded calibration from {} ({})".format(
            path, str(data["captured"])))
        return True

    # -- the hot path --------------------------------------------------

    def apply(self, frame):
        """frame: uint8/uint16 BGR, linear. Returns display-ready uint8 BGR."""
        if not self.ready:
            return frame if frame.dtype == np.uint8 else \
                cv2.convertScaleAbs(frame)

        x = frame.astype(np.float32)
        if self.dark is not None:
            cv2.subtract(x, self.dark, dst=x)
        cv2.multiply(x, self.gain_map, dst=x)

        if self.ccm is not None:
            x = cv2.transform(x, self.ccm)

        # Index the gamma table with 12-bit linear.
        idx = np.clip(x * 4095.0, 0, 4095).astype(np.uint16)
        return self.gamma[idx]


def measure_patch(frame, frac=0.25):
    """Mean BGR of the central region, for background neutrality checks."""
    h, w = frame.shape[:2]
    dh, dw = int(h * frac / 2), int(w * frac / 2)
    patch = frame[h // 2 - dh:h // 2 + dh, w // 2 - dw:w // 2 + dw]
    return patch.reshape(-1, 3).mean(axis=0)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

class CameraThread:
    def __init__(self, cap, arducam_utils, report_fps=False):
        self.cap = cap
        self.arducam_utils = arducam_utils
        self.report_fps = report_fps

        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.needs_reshape = (arducam_utils.convert2rgb == 0)
        if not self.needs_reshape:
            print("WARNING: the camera is delivering ISP-processed frames. "
                  "Flat-field correction assumes linear sensor data — disable "
                  "the ISP or expect the calibration to be approximate.")

        self.raw = None        # pre-correction, for calibration capture
        self.fps = 0.0
        self.frames_seen = 0
        self.lock = threading.Lock()
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        last_report = time.time()
        frame_count = 0
        last = time.monotonic()
        smoothed = 0.0

        while self.running:
            ret, frame = self.cap.read()
            if not ret:
                time.sleep(0.005)
                continue

            if self.needs_reshape:
                frame = frame.reshape(self.height, self.width)
            frame = self.arducam_utils.convert(frame)

            now = time.monotonic()
            dt = now - last
            last = now
            if dt > 0:
                inst = 1.0 / dt
                smoothed = inst if smoothed == 0 else 0.9 * smoothed + 0.1 * inst

            with self.lock:
                self.raw = frame
                self.fps = smoothed
                self.frames_seen += 1

            frame_count += 1
            if self.report_fps and time.time() - last_report >= 1:
                print("fps: {}".format(frame_count), end='\r')
                sys.stdout.flush()
                last_report = time.time()
                frame_count = 0

    def read_raw(self):
        with self.lock:
            return (None, 0.0) if self.raw is None else (self.raw, self.fps)

    def average(self, n=48, timeout=20.0):
        """Average n consecutive distinct frames. Returns float32 or None."""
        acc = None
        seen = -1
        got = 0
        deadline = time.time() + timeout
        while got < n and time.time() < deadline:
            with self.lock:
                if self.frames_seen == seen or self.raw is None:
                    frame = None
                else:
                    seen = self.frames_seen
                    frame = self.raw.astype(np.float32)
            if frame is None:
                time.sleep(0.002)
                continue
            acc = frame if acc is None else acc + frame
            got += 1
        return None if acc is None else acc / got

    def stop(self):
        self.running = False
        self.thread.join(timeout=1.0)


# ---------------------------------------------------------------------------
# Web UI
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PBS live view</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; height:100vh; display:flex; flex-direction:column;
         background:#0E1822; color:#c9d6e2;
         font:13px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { display:flex; align-items:baseline; gap:18px; flex-wrap:wrap;
           padding:10px 16px; border-bottom:1px solid #1d2c3b; }
  h1 { margin:0; font-size:12px; letter-spacing:.16em; text-transform:uppercase;
       font-weight:600; color:#5DA3E6; }
  .meta { color:#5f7488; }
  main { flex:1; min-height:0; display:grid; place-items:center; padding:14px; }
  img { max-width:100%; max-height:100%; object-fit:contain;
        border:1px solid #1d2c3b; background:#000; }
  footer { border-top:1px solid #1d2c3b; padding:10px 16px;
           display:flex; flex-direction:column; gap:10px; }
  .row { display:flex; align-items:center; gap:14px; flex-wrap:wrap; }
  .step { color:#5f7488; width:14px; }
  button { font:inherit; color:#c9d6e2; background:transparent;
           border:1px solid #2b3f52; padding:6px 12px; cursor:pointer; }
  button:hover { border-color:#5DA3E6; color:#fff; }
  button.primary { background:#D4A248; border-color:#D4A248; color:#0E1822; }
  button.primary:hover { background:#e0b463; }
  button:focus-visible, input:focus-visible { outline:2px solid #5DA3E6;
           outline-offset:2px; }
  label { display:flex; align-items:center; gap:8px; color:#5f7488; }
  input[type=range] { width:120px; accent-color:#5DA3E6; }
  output { width:36px; text-align:right; color:#c9d6e2; }
  #status { color:#5f7488; }
  .swatch { display:inline-block; width:11px; height:11px; vertical-align:-1px;
            border:1px solid #2b3f52; }
</style>
<header>
  <h1>PBS live view</h1>
  <span class="meta">__META__</span>
  <span class="meta" id="calstate"></span>
</header>
<main><img src="/stream" alt="Live camera feed"></main>
<footer>
  <div class="row">
    <span class="step">1</span>
    <button id="dark">Capture dark frame</button>
    <span class="meta">block the light path first</span>
  </div>
  <div class="row">
    <span class="step">2</span>
    <button id="flat" class="primary">Capture flat field</button>
    <span class="meta">blank area of the slide, in focus, stage moving</span>
  </div>
  <div class="row">
    <label>Saturation<input type="range" id="sat" min="0.6" max="2" step="0.01" value="__SAT__"><output id="sato"></output></label>
    <button id="save">Save frame</button>
    <button id="clear">Clear calibration</button>
    <span id="status"></span>
  </div>
  <div class="row">
    <span class="meta">background <span class="swatch" id="sw"></span> <span id="bg"></span></span>
  </div>
</footer>
<script>
  const $ = id => document.getElementById(id);
  const status = t => $('status').textContent = t;

  const sat = $('sat');
  const showSat = () => $('sato').textContent = Number(sat.value).toFixed(2);
  sat.addEventListener('input', () => {
    showSat();
    fetch('/sat?s=' + sat.value);
  });
  showSat();

  const hit = async (url, msg) => {
    status(msg);
    const res = await fetch(url);
    status(await res.text());
    refresh();
  };
  $('dark').addEventListener('click', () => hit('/cal/dark', 'Averaging dark frames...'));
  $('flat').addEventListener('click', () => hit('/cal/flat', 'Averaging flat field...'));
  $('clear').addEventListener('click', () => hit('/cal/clear', 'Clearing...'));
  $('save').addEventListener('click', () => hit('/snap', 'Saving...'));

  async function refresh() {
    try {
      const s = await (await fetch('/state')).json();
      $('calstate').textContent = s.calibrated
        ? 'calibrated' : 'uncalibrated — run steps 1 and 2';
      const [b, g, r] = s.background.map(v => Math.round(v));
      $('bg').textContent = `R ${r}  G ${g}  B ${b}   spread ${s.spread}`;
      $('sw').style.background = `rgb(${r},${g},${b})`;
    } catch (e) { /* stream still starting */ }
  }
  refresh();
  setInterval(refresh, 1500);
</script>
"""


class Handler(BaseHTTPRequestHandler):
    camera = None
    calib = None
    quality = 92
    save_dir = "."
    info = ""
    n_avg = 48

    def log_message(self, *args):
        pass

    def _send(self, body, code=200, ctype="text/plain; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        route, query = parsed.path, parse_qs(parsed.query)
        cam, cal = self.camera, self.calib

        if route in ("/", "/index.html"):
            self._send(PAGE.replace("__META__", self.info)
                           .replace("__SAT__", "{:.2f}".format(cal.saturation)),
                       ctype="text/html; charset=utf-8")
            return

        if route == "/state":
            raw, fps = cam.read_raw()
            if raw is None:
                bg, spread = [0, 0, 0], 0
            else:
                mean = measure_patch(cal.apply(raw))
                bg = [float(v) for v in mean]
                spread = int(round(mean.max() - mean.min()))
            self._send(json.dumps({
                "calibrated": cal.ready,
                "background": bg,
                "spread": spread,
                "fps": round(fps, 1),
            }), ctype="application/json")
            return

        if route == "/cal/dark":
            avg = cam.average(self.n_avg)
            if avg is None:
                self._send("no frames", 503)
                return
            cal.set_dark(avg)
            cal.save()
            peak = float(avg.max())
            msg = "Dark captured, peak {:.0f}".format(peak)
            if peak > 24:
                msg += " — high. Check for a light leak or sensor black level."
            self._send(msg)
            return

        if route == "/cal/flat":
            avg = cam.average(self.n_avg)
            if avg is None:
                self._send("no frames", 503)
                return
            peak, mean = float(avg.max()), float(avg.mean())
            if peak >= 253:
                self._send("Flat field is clipping (peak {:.0f}). Reduce "
                           "exposure or lamp intensity and retry.".format(peak))
                return
            if mean < 60:
                self._send("Flat field is dark (mean {:.0f}). Raise exposure "
                           "so the blank field sits near 200.".format(mean))
                return
            cal.set_flat(avg)
            cal.save()
            self._send("Flat field captured, peak {:.0f}, mean {:.0f}. "
                       "Calibration saved.".format(peak, mean))
            return

        if route == "/cal/clear":
            cal.clear()
            if os.path.exists(CALIB_FILE):
                os.remove(CALIB_FILE)
            self._send("Calibration cleared")
            return

        if route == "/sat":
            try:
                cal.set_saturation(float(query.get("s", ["1.0"])[0]))
            except ValueError:
                self._send("saturation must be a number", 400)
                return
            self._send("saturation {:.2f}".format(cal.saturation))
            return

        if route == "/snap":
            raw, _ = cam.read_raw()
            if raw is None:
                self._send("no frame yet", 503)
                return
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
            path = os.path.join(self.save_dir, "pbs_{}.png".format(stamp))
            cv2.imwrite(path, stain_enhance(cal.apply(raw)))
            print("Saved {}".format(path))
            self._send("Saved {}".format(os.path.basename(path)))
            return

        if route == "/stream":
            self.send_response(200)
            self.send_header("Content-Type",
                             "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            # 4:4:4 chroma. The default 4:2:0 subsampling destroys exactly the
            # fine chromatic detail that matters here — eosinophil granules,
            # polychromasia, platelet granularity.
            params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality,
                      int(cv2.IMWRITE_JPEG_SAMPLING_FACTOR),
                      int(cv2.IMWRITE_JPEG_SAMPLING_FACTOR_444)]
            last_seen = -1
            try:
                while True:
                    with cam.lock:
                        seen = cam.frames_seen
                    if seen == last_seen:
                        time.sleep(0.002)
                        continue
                    last_seen = seen
                    raw, _ = cam.read_raw()
                    if raw is None:
                        time.sleep(0.01)
                        continue
                    ok, jpg = cv2.imencode(".jpg", stain_enhance(cal.apply(raw)), params)
                    if not ok:
                        continue
                    payload = jpg.tobytes()
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\n"
                        b"Content-Length: " + str(len(payload)).encode() +
                        b"\r\n\r\n")
                    self.wfile.write(payload)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                return
            return

        self.send_error(404)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

def fourcc(a, b, c, d):
    return ord(a) | (ord(b) << 8) | (ord(c) << 16) | (ord(d) << 24)


def pixelformat(string):
    if len(string) not in (3, 4):
        raise argparse.ArgumentTypeError(
            "{} is not a pixel format".format(string))
    padded = string if len(string) == 4 else string + ' '
    return fourcc(*padded)


def show_info(arducam_utils):
    _, fw = arducam_utils.read_dev(ArducamUtils.FIRMWARE_VERSION_REG)
    _, sid = arducam_utils.read_dev(ArducamUtils.FIRMWARE_SENSOR_ID_REG)
    _, sn = arducam_utils.read_dev(ArducamUtils.SERIAL_NUMBER_REG)
    print("Firmware Version: {}".format(fw))
    print("Sensor ID: 0x{:04X}".format(sid))
    print("Serial Number: 0x{:08X}".format(sn))
    return sid


def main():
    p = argparse.ArgumentParser(
        description='Arducam MIPI streamer with PBS colour calibration.')
    p.add_argument('-d', '--device', default=0, type=int, nargs='?')
    p.add_argument('-f', '--pixelformat', type=pixelformat)
    p.add_argument('--width', type=lambda x: int(x, 0))
    p.add_argument('--height', type=lambda x: int(x, 0))
    p.add_argument('--fps', action='store_true', help="print fps")
    p.add_argument('--channel', type=int, default=-1, nargs='?')
    p.add_argument('-p', '--port', type=int, default=8000)
    p.add_argument('-q', '--quality', type=int, default=92,
                   help="stream JPEG quality; keep high, this is diagnostic")
    p.add_argument('--save-dir', default=".")
    p.add_argument('--bg-target', type=float, default=0.85,
                   help="linear level the blank field is mapped to (0-1). "
                        "0.85 lands near 240 after sRGB encoding, leaving "
                        "headroom so thin plasma does not clip")
    p.add_argument('--saturation', type=float, default=1.0,
                   help="linear-light saturation; 1.0 = sensor native")
    p.add_argument('--avg-frames', type=int, default=48,
                   help="frames averaged per calibration capture")
    args = p.parse_args()

    print("opening {}".format(args.device))
    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2)
    if not cap.isOpened():
        sys.exit("could not open /dev/video{}".format(args.device))

    if args.pixelformat is not None:
        if not cap.set(cv2.CAP_PROP_FOURCC, args.pixelformat):
            print("Failed to set pixel format.")

    arducam_utils = ArducamUtils(args.device)
    sensor_id = show_info(arducam_utils)

    if arducam_utils.convert2rgb == 0:
        cap.set(cv2.CAP_PROP_CONVERT_RGB, arducam_utils.convert2rgb)
    if args.width is not None:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    if args.height is not None:
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if args.channel in range(0, 4):
        arducam_utils.write_dev(ArducamUtils.CHANNEL_SWITCH_REG, args.channel)

    os.makedirs(args.save_dir, exist_ok=True)

    camera = CameraThread(cap, arducam_utils, args.fps)

    # Wait for the first frame so the calibration knows the frame shape.
    for _ in range(200):
        raw, _ = camera.read_raw()
        if raw is not None:
            break
        time.sleep(0.05)
    if raw is None:
        camera.stop()
        cap.release()
        sys.exit("no frames from the sensor")

    calib = Calibration(raw.shape, args.bg_target, args.saturation)
    calib.load()

    Handler.camera = camera
    Handler.calib = calib
    Handler.quality = args.quality
    Handler.save_dir = args.save_dir
    Handler.n_avg = args.avg_frames
    Handler.info = "{}x{} &middot; sensor 0x{:04X}".format(
        camera.width, camera.height, sensor_id)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print("streaming on http://0.0.0.0:{}  (Ctrl-C to stop)".format(args.port))
    if not calib.ready:
        print("no calibration loaded — run the dark and flat captures "
              "from the web page before reading slides")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        camera.stop()
        server.server_close()
        cap.release()


if __name__ == "__main__":
    main()