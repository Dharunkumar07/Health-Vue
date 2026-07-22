# GRBL Backend

FastAPI service that bridges the HealthVue frontend to a GRBL 1.1 motion controller over USB serial — typically an Arduino Uno + CNC Shield driving TMC2209/A4988 stepper drivers on the microscope's X/Y/Z stage.

## Running the backend — step by step

1. **Prerequisite**: Python 3.9+ installed (`python --version` or `python3 --version` to check).
2. Open a terminal and go into the `backend/` folder:
   ```
   cd backend
   ```
3. (Recommended) Create and activate a virtual environment, so these dependencies don't mix with system Python packages:
   ```
   python -m venv .venv
   # Linux/Jetson/macOS:
   source .venv/bin/activate
   # Windows (PowerShell):
   .venv\Scripts\Activate.ps1
   ```
4. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
5. If you don't have real GRBL hardware plugged in yet, skip to step 8 — the server still starts fine, `/api/connect` will just fail until a controller is attached.
6. Plug in the GRBL controller (Arduino Uno/CNC Shield) via USB and find which port it enumerated as:
   - **Linux / Jetson**: run `ls /dev/tty*` before and after plugging in and compare (look for a new `/dev/ttyACM0` or `/dev/ttyUSB0`), or check `dmesg | tail` right after plugging in.
   - **Windows**: Device Manager → Ports (COM & LPT) — look for something like "USB-SERIAL CH340" or "Arduino Uno".
   - **Linux only — serial permissions**: opening the port will fail with `PermissionError: [Errno 13] Permission denied: '/dev/ttyACM0'` unless your user is in the `dialout` group (Jetson's L4T is Ubuntu-based, same fix applies). Fix this **permanently, once**:
     ```
     bash backend/scripts/fix-serial-permissions.sh
     ```
     This adds your user to `dialout` *and* installs a udev rule (`backend/scripts/99-grbl-serial.rules` → `/etc/udev/rules.d/`) that keeps the port group-accessible on every future reboot/replug, with no dependency on whichever distro image you're running. The only manual step left is a **one-time** log out/in (or reboot) afterward, so your current session picks up the new group — required once, not on every connect. Restart the backend after that. Script is idempotent; safe to re-run any time.
7. Edit `backend/.env` with that port:
   ```
   GRBL_PORT=/dev/ttyACM0    # or COM3, etc. on Windows
   GRBL_BAUD=115200
   GRBL_TIMEOUT=1.0
   ```
   This is loaded automatically on startup via `python-dotenv` — no need to `export` these manually.
8. Start the server:
   ```
   uvicorn main:app --reload --port 8000
   ```
9. Confirm it's up by visiting `http://localhost:8000/api/health` in a browser, or:
   ```
   curl http://localhost:8000/api/health
   ```
   You should get back `{"ok":true,"connected":false,...}` (`connected` becomes `true` once real hardware is attached and reachable on the configured port).
10. To stop the server, press `Ctrl+C` in that terminal.

The frontend expects this at `http://localhost:8000` by default (see the root `.env`'s `VITE_GRBL_API`).

## Running the tests

No real GRBL hardware needed — `tests/test_main.py` swaps in a fake serial port that plays back scripted GRBL responses, so it exercises the actual FastAPI endpoints (jog for all three axes, home, jog-stop, abort, connect's setup commands, and the status-poll latency behavior) without a board attached.

```
pip install -r requirements-dev.txt
pytest
```

## API

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/health` | — | `{ok, connected, port, baud}` — always responds, even if serial isn't open |
| POST | `/api/connect` | `{port, baud}` | Opens the serial port; also sends `$20=0`, `$110=1000`, and `$112=1000` (see Safety notes) |
| POST | `/api/disconnect` | — | Closes the serial port |
| GET | `/api/status` | — | `{ok, status, state, x, y, z}` — `status` is the raw GRBL frame, the rest are parsed from it |
| POST | `/api/jog` | `{axis, dx_mm, feed}` | `axis` is `"X"`/`"Y"`/`"Z"`; sends `$J=G91 G21 <axis><dx_mm> F<feed>` |
| POST | `/api/home` | — | Sends `$H` (homing cycle); can take several seconds |
| POST | `/api/unlock` | — | Sends `$X` (clears an alarm lock) |
| POST | `/api/stop` | — | Realtime feed hold (`!`) — pauses a running **feed move**, resumable with `/api/resume`. Ignored by GRBL during a homing cycle, and not the right tool for stopping a jog — use `/api/jog-stop`. |
| POST | `/api/resume` | — | Realtime resume (`~`) |
| POST | `/api/jog-stop` | — | Realtime jog cancel (`0x85`) — stops an in-progress `$J=` jog cleanly, no resume needed afterward. |
| POST | `/api/abort` | — | Ctrl-X soft reset (`0x18`) — the only thing that interrupts a homing cycle. Not resumable; position is lost and a re-home (`$H`) is required afterward. |
| POST | `/api/setup` | `{soft_limits, max_feed_x}` | Manually re-apply the `$20`/`$110` settings with custom values |

`/api/jog` and `/api/home` now raise an error (visible to the client, e.g. as a toast in the UI) whenever GRBL's own response contains an `error:`/`ALARM:` line — previously that line was captured but never inspected, so a command GRBL rejected (out of range, axis alarm-locked, failed homing switch, ...) was reported back to the frontend as a plain success.

CORS is allowlisted to `localhost`/`127.0.0.1` on ports `3000` and `5173`. If you access the frontend from another device on the network, add that origin to `CORS_ORIGINS` in `main.py`.

## Safety notes

- **`/api/connect` disables GRBL's hardware soft limits** (`$20=0`) and raises the X and Z max feed (`$110=1000`, `$112=1000`) every time it connects. GRBL persists `$`-settings to EEPROM, so this is a lasting change to the controller, not session-only. It was written for convenient jog testing — worth reconsidering once real hardware with real travel limits is involved.
- The frontend's `TRAVEL_LIMITS` guess in `src/views/LiveView.jsx` (`x:[0,26] y:[0,76] z:[0,4]`) is a **simulated-stage placeholder only** — it's applied when no real GRBL hardware is connected. It is deliberately *not* applied once real hardware is live, because gating jog buttons against a guessed range disabled them outright (no click, no error) whenever the real machine's actual position fell outside that guess — this is what made Z (and potentially X/Y) jogging look broken. On real hardware there is currently no software travel-limit guard at all; physical limit switches, or re-enabling `$20=1` with real `$130`/`$131`/`$132` travel values measured from your actual stage, is the real safety layer.

## Calibrating steps/mm for your stepper motors

```
steps/mm = (motor_steps_per_rev × microsteps) / mm_per_revolution
```

- `motor_steps_per_rev` — from the motor's datasheet: 200 for a 1.8°/step motor, 400 for 0.9°/step.
- `microsteps` — set by the CNC Shield's MS1/MS2/MS3 jumpers or the driver's default.
- `mm_per_revolution` — the linear stage's leadscrew lead (mm traveled per full screw turn) — a property of the stage, not the motor.

Send the result as a raw `$`-command over serial (`$100=<value>` for X, `$101=` for Y, `$102=` for Z) — this is a one-time controller setting, not something this codebase sends automatically. A serial terminal (e.g. the Arduino IDE's serial monitor, `screen /dev/ttyACM0 115200`, or PuTTY) works fine for this.
