"""
Regression tests for the GRBL bridge, run against a fake serial port instead
of real hardware — the point is to prove the request/response logic (all
three axes wired the same way, GRBL errors surfacing instead of being
swallowed, status polls not hogging the lock) is correct without needing a
board plugged in.
"""
import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import main as backend_main  # noqa: E402
from main import GRBL  # noqa: E402


class FakeSerial:
    """Stand-in for serial.Serial. Lines are queued up front (as plain text,
    no trailing newline) and handed back one per readline() call, exactly
    like a real port would after each command GRBL responds to."""

    def __init__(self):
        self.is_open = True
        self._to_read = []
        self.written = []

    def queue(self, *lines):
        for line in lines:
            self._to_read.append((line + "\n").encode())
        return self

    def write(self, data):
        self.written.append(data)

    def flush(self):
        pass

    def readline(self):
        if self._to_read:
            return self._to_read.pop(0)
        return b""

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def close(self):
        self.is_open = False


@pytest.fixture
def fake_grbl():
    """Puts the module-level `grbl` singleton into a 'connected' state backed
    by a FakeSerial, and cleans it back up afterwards so tests don't leak
    state into each other."""
    fake = FakeSerial()
    backend_main.grbl.ser = fake
    backend_main.grbl.port = "TEST"
    backend_main.grbl.baud = 115200
    yield fake
    backend_main.grbl.ser = None


@pytest.fixture
def client():
    return TestClient(backend_main.app)


def written_text(fake):
    return b"".join(fake.written).decode()


# ---------------------------------------------------------------------------
# /api/jog — all three axes must behave identically. This is the direct
# regression test for "only Y worked" — it proves X/Y/Z share one code path
# with no axis-specific branching, by asserting the exact command sent.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("axis,dx,feed", [("X", 5, 400), ("Y", -2.5, 400), ("Z", 0.05, 200)])
def test_jog_sends_correct_command_for_every_axis(client, fake_grbl, axis, dx, feed):
    fake_grbl.queue("ok")
    r = client.post("/api/jog", json={"axis": axis, "dx_mm": dx, "feed": feed})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    expected = f"$J=G91 G21 {axis}{dx:.3f} F{feed:.1f}"
    assert body["sent"] == expected
    assert expected.encode() in written_text(fake_grbl).encode()


def test_jog_rejects_unknown_axis(client, fake_grbl):
    r = client.post("/api/jog", json={"axis": "A", "dx_mm": 1})
    assert r.status_code == 400


def test_jog_surfaces_grbl_error_instead_of_silently_succeeding(client, fake_grbl):
    """Regression test for the bug where a GRBL-rejected jog (e.g. an
    alarm-locked axis) still came back as {"ok": true} because the error
    line was captured but never inspected."""
    fake_grbl.queue("error:9")
    r = client.post("/api/jog", json={"axis": "Z", "dx_mm": 0.05, "feed": 200})
    assert r.status_code == 400
    assert "error:9" in r.json()["detail"]


def test_home_success(client, fake_grbl):
    fake_grbl.queue("ok")
    r = client.post("/api/home")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_home_surfaces_alarm_instead_of_silently_succeeding(client, fake_grbl):
    """Regression test: a failed homing cycle (e.g. a switch that never
    triggers) used to come back as ok:true just like a real success."""
    fake_grbl.queue("ALARM:1")
    r = client.post("/api/home")
    assert r.status_code == 400
    assert "ALARM:1" in r.json()["detail"]


def test_jog_stop_sends_jog_cancel_byte(client, fake_grbl):
    r = client.post("/api/jog-stop")
    assert r.status_code == 200
    assert b"\x85" in fake_grbl.written


def test_abort_sends_ctrl_x(client, fake_grbl):
    r = client.post("/api/abort")
    assert r.status_code == 200
    assert r.json()["requires_rehome"] is True
    assert b"\x18" in fake_grbl.written


def test_feed_hold_sends_bang(client, fake_grbl):
    r = client.post("/api/stop")
    assert r.status_code == 200
    assert b"!" in fake_grbl.written


def test_connect_raises_x_and_z_max_feed(client, monkeypatch):
    """Both max-feed settings must be raised on connect, not just X's — a Z
    max-feed left at a low stock default would cap/reject Z jogs."""
    fake = FakeSerial().queue("ok", "ok", "ok")
    monkeypatch.setattr(backend_main.serial, "Serial", lambda **kwargs: fake)
    monkeypatch.setattr(backend_main.time, "sleep", lambda s: None)
    backend_main.grbl.ser = None
    try:
        r = client.post("/api/connect", json={"port": "TEST", "baud": 115200})
        assert r.status_code == 200
        sent = written_text(fake)
        assert "$110=1000" in sent
        assert "$112=1000" in sent
    finally:
        backend_main.grbl.ser = None


# ---------------------------------------------------------------------------
# Status-poll latency — regression test for the actual root cause of the
# reported "jog feels laggy" symptom: every /api/status call used to burn
# its *entire* read window even when GRBL answered instantly, while holding
# the same lock a jog/home request needs.
# ---------------------------------------------------------------------------
def test_read_lines_exits_early_when_frame_already_matched():
    g = GRBL()
    g.ser = FakeSerial().queue("<Idle|MPos:1.000,2.000,3.000|FS:0,0>")
    start = time.time()
    lines = g._read_lines(0.5, until=lambda l: l.startswith("<") and l.endswith(">"))
    elapsed = time.time() - start
    assert lines == ["<Idle|MPos:1.000,2.000,3.000|FS:0,0>"]
    assert elapsed < 0.1, f"expected an early return, took {elapsed:.3f}s of the 0.5s window"


def test_read_lines_without_until_still_drains_the_window():
    """The full-window behavior is intentionally kept as the default (used
    for one-shot banner reads on connect) — only status() opts into early
    exit. This locks that distinction in."""
    g = GRBL()
    g.ser = FakeSerial().queue("<Idle|MPos:1.000,2.000,3.000|FS:0,0>")
    start = time.time()
    lines = g._read_lines(0.2)
    elapsed = time.time() - start
    assert lines == ["<Idle|MPos:1.000,2.000,3.000|FS:0,0>"]
    assert elapsed >= 0.2


def test_status_endpoint_returns_promptly(client, fake_grbl):
    fake_grbl.queue("<Idle|MPos:1.000,2.000,3.000|FS:0,0>")
    start = time.time()
    r = client.get("/api/status")
    elapsed = time.time() - start
    assert r.status_code == 200
    body = r.json()
    assert body["x"] == 1.0 and body["y"] == 2.0 and body["z"] == 3.0
    assert elapsed < 0.1, f"status endpoint took {elapsed:.3f}s — should return as soon as GRBL answers"
