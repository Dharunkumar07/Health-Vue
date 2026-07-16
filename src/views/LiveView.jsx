import { useEffect, useRef, useState } from 'react';
import HChat from '../components/HChat';
import ToastStack from '../components/ToastStack';
import useGrblStage from '../hooks/useGrblStage';
import useCamera from '../hooks/useCamera';
import useToast from '../hooks/useToast';

const MICRONS_PER_PX = { '4×': '1.6', '10×': '0.64', '40×': '0.16', '100×': '0.065' };

function formatElapsed(sec) {
  const m = Math.floor(sec / 60).toString().padStart(2, '0');
  const s = Math.floor(sec % 60).toString().padStart(2, '0');
  return `${m}:${s}`;
}
const JOG_FEED = 400; // mm/min for XY jog
const FOCUS_FEED = 200; // mm/min for Z focus
const FOCUS_STEP_MM = 0.05; // mm per focus tap, independent of the XY step size

// Software travel limits per axis, [min, max] mm. Matches the placeholder
// $130/$131/$132 travel figures already used in ConsoleView's simulated
// settings (sized to a 25x75mm slide). Replace with your real linear
// stage's actual travel span once known — this is NOT derived from the
// stepper motor's physical size, which has no bearing on travel range.
const TRAVEL_LIMITS = { x: [0, 26], y: [0, 76], z: [0, 4] };

function checkTravel(axis, from, delta) {
  const [min, max] = TRAVEL_LIMITS[axis];
  const next = from + delta;
  if (next < min) return { blocked: true, bound: min };
  if (next > max) return { blocked: true, bound: max };
  return { blocked: false, next };
}

export default function LiveView({ onNavigate }) {
  const hw = useGrblStage();
  const hardwareLive = hw.reachable && hw.connected;
  const cam = useCamera();
  const camLive = cam.status === 'live';
  const toast = useToast();

  const [pos, setPos] = useState({ x: 12.48, y: 8.22, z: 2.14 });
  const [step, setStep] = useState(0.1);
  const [zpct, setZpct] = useState(60);
  const [focusFlag, setFocusFlag] = useState({ text: 'Sharp · contrast peak locked', color: 'var(--green)' });
  const [objective, setObjective] = useState('40×');
  const [simStatus, setSimStatus] = useState('Idle');
  const [snapFlash, setSnapFlash] = useState(false);
  const [chatOpen, setChatOpen] = useState(false);
  const [connectPort, setConnectPort] = useState('');
  const [connecting, setConnecting] = useState(false);

  const feedRef = useRef(null);
  const focusTimer = useRef(null);
  const lastErrorRef = useRef('');

  const displayPos = hardwareLive ? hw.pos : pos;
  const statusInfo = hardwareLive
    ? hw.label
    : hw.reachable
    ? { text: 'Hardware disconnected', dot: 'red' }
    : { text: `${simStatus} · simulated`, dot: 'red' };
  const statusText = statusInfo.text;
  const statusDot = statusInfo.dot;
  const canUnlock = hardwareLive && hw.state === 'Alarm';

  // Surface real backend/serial errors instead of dropping them silently.
  useEffect(() => {
    if (hw.error && hw.error !== lastErrorRef.current) {
      lastErrorRef.current = hw.error;
      toast.show(`Stage: ${hw.error}`, 4000);
    }
    if (!hw.error) lastErrorRef.current = '';
  }, [hw.error, toast]);

  useEffect(() => {
    if (hw.ports.length && !connectPort) {
      setConnectPort(hw.ports[0].device);
    }
  }, [hw.ports, connectPort]);

  async function handleConnect() {
    if (!connectPort) return;
    setConnecting(true);
    await hw.connectTo(connectPort);
    setConnecting(false);
  }

  function fmt(n) {
    return n.toFixed(3);
  }

  function atLimit(axis, dir) {
    return checkTravel(axis, displayPos[axis], dir * step).blocked;
  }

  function atFocusLimit(dir) {
    return checkTravel('z', displayPos.z, dir * FOCUS_STEP_MM).blocked;
  }

  function jog(axis, dir) {
    const travel = checkTravel(axis, displayPos[axis], dir * step);
    if (travel.blocked) {
      toast.show(`${axis.toUpperCase()} axis at ${travel.bound}mm travel limit — reverse direction only`);
      return;
    }

    if (hardwareLive) {
      hw.jog(axis.toUpperCase(), dir * step, JOG_FEED);
    } else {
      setPos((p) => ({ ...p, [axis]: travel.next }));
    }
    const feed = feedRef.current;
    if (feed) {
      const dx = (axis === 'x' ? dir : 0) * 4;
      const dy = (axis === 'y' ? -dir : 0) * 4;
      feed.style.transition = 'transform .18s';
      feed.style.transform = `translate(${dx}px,${dy}px)`;
      setTimeout(() => {
        feed.style.transform = 'translate(0,0)';
      }, 180);
    }
  }

  function homeStage() {
    if (hardwareLive) {
      hw.home();
      return;
    }
    setPos({ x: 0, y: 0, z: 2.14 });
    setSimStatus('Home');
    setTimeout(() => setSimStatus('Idle'), 1200);
  }

  function focusZ(dir) {
    const travel = checkTravel('z', displayPos.z, dir * FOCUS_STEP_MM);
    if (travel.blocked) {
      toast.show(`Z axis at ${travel.bound}mm focus limit — reverse direction only`);
      return;
    }

    if (hardwareLive) {
      hw.jog('Z', dir * FOCUS_STEP_MM, FOCUS_FEED);
    }
    setZpct((prev) => {
      const next = Math.min(100, Math.max(0, prev + dir * 6));
      if (!hardwareLive) setPos((p) => ({ ...p, z: travel.next }));
      return next;
    });
    setFocusFlag({ text: 'Adjusting…', color: 'var(--amber)' });
    clearTimeout(focusTimer.current);
    focusTimer.current = setTimeout(() => {
      setFocusFlag({ text: 'Sharp · contrast peak locked', color: 'var(--green)' });
    }, 500);
  }

  function autofocus() {
    setFocusFlag({ text: 'Auto-focusing…', color: 'var(--amber)' });
    setZpct(64);
    clearTimeout(focusTimer.current);
    focusTimer.current = setTimeout(() => {
      setFocusFlag({ text: 'Locked · sharpest plane', color: 'var(--green)' });
    }, 900);
  }

  function snap() {
    setSnapFlash(true);
    setTimeout(() => setSnapFlash(false), 180);
    cam.downloadSnapshot();
  }

  function toggleRecord() {
    if (cam.recording) cam.stopRecording();
    else cam.startRecording();
  }

  async function captureToWsi() {
    await cam.downloadSnapshot();
    onNavigate('viewer');
  }

  return (
    <div className="live-wrap">
      <ToastStack toasts={toast.toasts} />
      <div className="live-center">
        <div className="cam">
          <div className="cam-feed" ref={feedRef}>
            <video
              ref={cam.videoRef}
              className="cam-video"
              autoPlay
              muted
              playsInline
              style={{ display: camLive ? 'block' : 'none' }}
            />
            {!camLive && (
              <div className="cam-offline">
                {cam.status === 'requesting' && 'Requesting camera access…'}
                {cam.status === 'error' && `Camera unavailable — ${cam.error}`}
              </div>
            )}
          </div>
          <div className="cam-vignette"></div>
          <div className="reticle"></div>
          {camLive && (
            <div className="live-det" style={{ left: '44%', top: '42%', width: '64px', height: '60px' }}>
              <span className="ll">RBC cluster · 0.84</span>
            </div>
          )}
          <div className="cam-badge">
            <div className="cb">
              <span className={'dot' + (camLive ? '' : cam.status === 'error' ? ' red' : ' amber')}></span>
              {camLive ? `LIVE · ${cam.frameRate ?? '--'} fps` : cam.status === 'error' ? 'Camera offline' : 'Connecting…'}
            </div>
            <div className="cb">
              {objective} · {MICRONS_PER_PX[objective]} µm/px
            </div>
            {cam.recording && (
              <div className="cb rec">
                <span className="dot red"></span>REC {formatElapsed(cam.elapsedSec)}
              </div>
            )}
          </div>
          <div className="scalebar">
            50 µm
            <div className="bar"></div>
          </div>
        </div>

        <div className="cam-actions">
          <button
            className="cam-btn"
            onClick={snap}
            disabled={!camLive}
            style={{ background: snapFlash ? 'var(--blue)' : '' }}
          >
            <svg viewBox="0 0 24 24">
              <path d="M4 8l2-3h12l2 3M4 8h16v11H4z" />
              <circle cx="12" cy="13" r="3.2" />
            </svg>
            Snapshot
          </button>
          <button
            className={'cam-btn rec' + (cam.recording ? ' active' : '')}
            onClick={toggleRecord}
            disabled={!camLive}
          >
            <svg viewBox="0 0 24 24">
              <circle cx="12" cy="12" r="7" />
            </svg>
            Record
          </button>
          <button className="cam-btn capture" onClick={captureToWsi} disabled={!camLive}>
            <svg viewBox="0 0 24 24">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M3 9h18" />
            </svg>
            Capture to WSI
          </button>
        </div>
      </div>

      <div className="stage-panel">
        <div className="stage-block">
          <div className={'h-fab inline' + (chatOpen ? ' show' : '')} onClick={() => setChatOpen((v) => !v)}>
            <span className="cell"></span>
            <div className="ft">
              Ask H<small>about this field</small>
            </div>
          </div>
          <div className={'live-hchat inline' + (chatOpen ? ' show' : '')}>
            <HChat
              caption="Watching live feed"
              headerExtra={
                <button className="lh-close" onClick={() => setChatOpen(false)}>
                  ×
                </button>
              }
              initialMessages={[
                {
                  who: 'h',
                  text:
                    "I'm looking at the current field. Dense RBC cluster centre-frame, staining looks slightly thick here — try nudging +Y to reach the smear's feathered edge for a cleaner monolayer.",
                },
              ]}
              replies={[
                'Centre-frame is a dense cluster of red cells with overlapping membranes — this is a thick zone of the smear. Move toward the feathered edge for a countable monolayer.',
                'Nudge +Y about 2–3 mm. The smear thins toward the top edge from where you are now.',
              ]}
              chips={[
                { label: 'What is this?', text: 'What am I looking at?' },
                { label: 'Where to move?', text: 'Where should I move?' },
              ]}
              placeholder="Ask about this field…"
            />
          </div>

          <div className="sp-h">
            Stage{' '}
            <span
              className={'grbl-status' + (statusDot ? ' ' + statusDot : '')}
              onClick={canUnlock ? () => hw.unlock() : undefined}
              style={canUnlock ? { cursor: 'pointer' } : undefined}
              title={canUnlock ? 'Click to unlock ($X)' : undefined}
            >
              <span className={'dot' + (statusDot ? ' ' + statusDot : '')}></span>
              {statusText}
            </span>
          </div>
          <div className="coords">
            <div className="coord-row">
              <span className="ax">X</span>
              <span className="val">{fmt(displayPos.x)}</span>
              <span className="un">mm</span>
            </div>
            <div className="coord-row">
              <span className="ax">Y</span>
              <span className="val">{fmt(displayPos.y)}</span>
              <span className="un">mm</span>
            </div>
            <div className="coord-row">
              <span className="ax">Z</span>
              <span className="val">{fmt(displayPos.z)}</span>
              <span className="un">mm</span>
            </div>
          </div>

          {!hardwareLive && (
            <div className="grbl-connect">
              {!hw.reachable ? (
                <div className="grbl-connect-msg">
                  Backend unreachable at {import.meta.env.VITE_GRBL_API || 'http://localhost:8000'} —
                  start the bridge service (see backend/README.md). Showing simulated stage.
                </div>
              ) : (
                <>
                  <div className="grbl-connect-msg">
                    Serial port not open. {hw.ports.length === 0 && 'No serial ports detected on this machine.'}
                  </div>
                  <div className="grbl-connect-row">
                    <select
                      value={connectPort}
                      onChange={(e) => setConnectPort(e.target.value)}
                      disabled={hw.ports.length === 0}
                    >
                      {hw.ports.length === 0 && <option value="">No ports found</option>}
                      {hw.ports.map((p) => (
                        <option key={p.device} value={p.device}>
                          {p.device}
                          {p.description && p.description !== 'n/a' ? ` — ${p.description}` : ''}
                        </option>
                      ))}
                    </select>
                    <button onClick={() => hw.refreshPorts()} title="Rescan ports" type="button">
                      ↻
                    </button>
                    <button onClick={handleConnect} disabled={!connectPort || connecting} type="button">
                      {connecting ? 'Connecting…' : 'Connect'}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        <div>
          <div className="lab-small" style={{ marginBottom: '7px' }}>Step size</div>
          <div className="step-seg">
            {[
              { v: 0.01, label: '0.01' },
              { v: 0.1, label: '0.1' },
              { v: 1, label: '1.0' },
              { v: 5, label: '5.0' },
            ].map((s) => (
              <button key={s.v} className={step === s.v ? 'on' : ''} onClick={() => setStep(s.v)}>
                {s.label}
              </button>
            ))}
          </div>
        </div>

        <div>
          <div className="lab-small" style={{ marginBottom: '7px' }}>XY jog</div>
          <div className="jog">
            <button className="mid"></button>
            <button onClick={() => jog('y', 1)} disabled={atLimit('y', 1)}>
              <svg viewBox="0 0 24 24">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
            </button>
            <button className="home" onClick={homeStage}>
              <svg viewBox="0 0 24 24">
                <path d="M3 11l9-8 9 8M5 10v10h14V10" />
              </svg>
            </button>
            <button onClick={() => jog('x', -1)} disabled={atLimit('x', -1)}>
              <svg viewBox="0 0 24 24">
                <path d="M19 12H5M12 5l-7 7 7 7" />
              </svg>
            </button>
            <button className="mid">
              <svg viewBox="0 0 24 24" style={{ opacity: 0.4 }}>
                <circle cx="12" cy="12" r="2.5" />
              </svg>
            </button>
            <button onClick={() => jog('x', 1)} disabled={atLimit('x', 1)}>
              <svg viewBox="0 0 24 24">
                <path d="M5 12h14M12 5l7 7-7 7" />
              </svg>
            </button>
            <button className="mid"></button>
            <button onClick={() => jog('y', -1)} disabled={atLimit('y', -1)}>
              <svg viewBox="0 0 24 24">
                <path d="M12 5v14M5 12l7 7 7-7" />
              </svg>
            </button>
            <button className="mid"></button>
          </div>
        </div>

        <div>
          <div className="lab-small" style={{ marginBottom: '7px' }}>Focus (Z)</div>
          <div className="zfocus">
            <div className="zrow">
              <button onClick={() => focusZ(1)} disabled={atFocusLimit(1)}>▲ up</button>
              <button onClick={autofocus} style={{ background: 'rgba(74,144,217,.14)', color: 'var(--blue-bright)' }}>
                Auto
              </button>
              <button onClick={() => focusZ(-1)} disabled={atFocusLimit(-1)}>▼ down</button>
            </div>
            <div className="zbar">
              <div className="zbar-fill" style={{ width: zpct + '%' }}></div>
            </div>
            <div className="focus-flag" style={{ color: focusFlag.color }}>{focusFlag.text}</div>
          </div>
        </div>

        <div>
          <div className="lab-small" style={{ marginBottom: '7px' }}>Objective</div>
          <div className="obj-seg">
            {['4×', '10×', '40×', '100×'].map((o) => (
              <button key={o} className={objective === o ? 'on' : ''} onClick={() => setObjective(o)}>
                {o}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
