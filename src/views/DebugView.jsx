import { useState } from 'react';
import ToastStack from '../components/ToastStack';
import useGrblStage from '../hooks/useGrblStage';
import useToast from '../hooks/useToast';

const MM_PER_IN = 25.4;
const STEP_MIN = 0.001;

export default function DebugView() {
  const hw = useGrblStage();
  const hardwareLive = hw.reachable && hw.connected;
  const toast = useToast();

  // Kept as raw text, not parsed numbers, so a value like "0.5" isn't fought
  // over mid-keystroke (typing "0" would otherwise get clamped away before
  // "0.5" is finished). Whatever is in the box at the moment a jog button is
  // clicked is what gets parsed and sent — that IS the user-input connection.
  const [stepXY, setStepXY] = useState('1');
  const [stepZ, setStepZ] = useState('1');
  const [feed, setFeed] = useState('500');
  const [unit, setUnit] = useState('mm'); // 'mm' | 'in'
  const [connectPort, setConnectPort] = useState('');
  const [connecting, setConnecting] = useState(false);
  const [busy, setBusy] = useState('');

  function fmt(n) {
    return (n ?? 0).toFixed(3);
  }

  function num(v, fallback) {
    const n = parseFloat(v);
    return Number.isFinite(n) && n > 0 ? n : fallback;
  }

  function toMm(v) {
    return unit === 'in' ? v * MM_PER_IN : v;
  }

  async function jogAxis(axis, dir) {
    if (!hardwareLive) {
      toast.show('Not connected to GRBL — open a port below first');
      return;
    }
    const stepDisplay = num(axis === 'z' ? stepZ : stepXY, STEP_MIN);
    const distanceMm = toMm(stepDisplay) * dir;
    const feedMm = toMm(num(feed, 1));
    await hw.jog(axis.toUpperCase(), distanceMm, feedMm);
  }

  async function runAction(name, fn) {
    setBusy(name);
    await fn();
    setBusy('');
  }

  function scaleSteps(factor) {
    setStepXY((v) => String(Math.max(STEP_MIN, +(num(v, STEP_MIN) * factor).toFixed(4))));
    setStepZ((v) => String(Math.max(STEP_MIN, +(num(v, STEP_MIN) * factor).toFixed(4))));
  }

  async function handleConnect() {
    if (!connectPort) return;
    setConnecting(true);
    await hw.connectTo(connectPort);
    setConnecting(false);
  }

  const statusInfo = hardwareLive
    ? hw.label
    : hw.reachable
    ? { text: 'Port not open', dot: 'red' }
    : { text: 'Backend unreachable', dot: 'red' };
  const canUnlock = hardwareLive && hw.state === 'Alarm';

  return (
    <div className="dbg-wrap">
      <ToastStack toasts={toast.toasts} />

      <div className="dbg-main">
        <div className="con-topbar">
          <div className="con-conn">
            <span className={'dot' + (statusInfo.dot ? ' ' + statusInfo.dot : '')}></span>
            <span
              onClick={canUnlock ? () => hw.unlock() : undefined}
              style={canUnlock ? { cursor: 'pointer' } : undefined}
              title={canUnlock ? 'Click to unlock ($X)' : undefined}
            >
              {statusInfo.text}
            </span>
          </div>
          {hw.error && <span className="con-badge state alarm">{hw.error}</span>}
          <div style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: '10px', color: '#4a5666' }}>
            Debug · raw GRBL axis test
          </div>
        </div>

        {!hardwareLive && (
          <div className="grbl-connect" style={{ marginTop: 0 }}>
            {!hw.reachable ? (
              <div className="grbl-connect-msg">
                Backend unreachable at {import.meta.env.VITE_GRBL_API || 'http://localhost:8000'} — start the
                bridge service (see backend/README.md).
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

        <div className="coords dbg-dro">
          <div className="coord-row">
            <span className="ax">X</span>
            <span className="val">{fmt(hw.pos.x)}</span>
            <span className="un">mm</span>
          </div>
          <div className="coord-row">
            <span className="ax">Y</span>
            <span className="val">{fmt(hw.pos.y)}</span>
            <span className="un">mm</span>
          </div>
          <div className="coord-row">
            <span className="ax">Z</span>
            <span className="val">{fmt(hw.pos.z)}</span>
            <span className="un">mm</span>
          </div>
        </div>

        <div className="dbg-jogarea">
          <div className="dbg-xy">
            <div className="jog-head">
              <div className="lab-small">XY axis</div>
              <div className="jog-stop">
                <span className="jog-stop-label">Stop</span>
                <button
                  className="jog-stop-btn"
                  onClick={() => runAction('stop', hw.jogStop)}
                  disabled={busy === 'stop'}
                  title="Stop — cancels the current jog move. No re-home needed."
                  type="button"
                ></button>
              </div>
            </div>
            <div className="jog">
              <button className="mid"></button>
              <button onClick={() => jogAxis('y', 1)} disabled={!hardwareLive}>
                <svg viewBox="0 0 24 24">
                  <path d="M12 19V5M5 12l7-7 7 7" />
                </svg>
              </button>
              <button className="home" onClick={() => runAction('home', hw.home)} disabled={busy === 'home'}>
                <svg viewBox="0 0 24 24">
                  <path d="M3 11l9-8 9 8M5 10v10h14V10" />
                </svg>
              </button>
              <button onClick={() => jogAxis('x', -1)} disabled={!hardwareLive}>
                <svg viewBox="0 0 24 24">
                  <path d="M19 12H5M12 5l-7 7 7 7" />
                </svg>
              </button>
              <button className="mid">
                <svg viewBox="0 0 24 24" style={{ opacity: 0.4 }}>
                  <circle cx="12" cy="12" r="2.5" />
                </svg>
              </button>
              <button onClick={() => jogAxis('x', 1)} disabled={!hardwareLive}>
                <svg viewBox="0 0 24 24">
                  <path d="M5 12h14M12 5l7 7-7 7" />
                </svg>
              </button>
              <button className="mid"></button>
              <button onClick={() => jogAxis('y', -1)} disabled={!hardwareLive}>
                <svg viewBox="0 0 24 24">
                  <path d="M12 5v14M5 12l7 7 7-7" />
                </svg>
              </button>
              <button className="mid"></button>
            </div>
          </div>

          <div className="dbg-z">
            <div className="lab-small" style={{ marginBottom: '7px' }}>Z axis</div>
            <div className="dbg-zpad">
              <button onClick={() => jogAxis('z', 1)} disabled={!hardwareLive}>
                <svg viewBox="0 0 24 24">
                  <path d="M12 19V5M5 12l7-7 7 7" />
                </svg>
                Z+
              </button>
              <button onClick={() => jogAxis('z', -1)} disabled={!hardwareLive}>
                <svg viewBox="0 0 24 24">
                  <path d="M12 5v14M5 12l7 7 7-7" />
                </svg>
                Z-
              </button>
            </div>
          </div>
        </div>

        <div className="quickcmds">
          <button className="qcmd" onClick={() => runAction('home', hw.home)} disabled={busy === 'home'}>
            $H home
          </button>
          <button className="qcmd" onClick={() => runAction('unlock', hw.unlock)} disabled={busy === 'unlock'}>
            $X unlock
          </button>
          <button
            className="qcmd danger"
            onClick={() => runAction('abort', hw.abort)}
            disabled={busy === 'abort'}
          >
            ⌫X soft-reset
          </button>
          <button
            className="qcmd"
            onClick={() => runAction('disconnect', hw.disconnect)}
            disabled={!hardwareLive || busy === 'disconnect'}
          >
            Disconnect
          </button>
        </div>
      </div>

      <div className="dbg-side">
        <div className="cs-h" style={{ marginBottom: '11px' }}>Step &amp; feed</div>

        <div className="dbg-field-row">
          <label>Step size XY</label>
          <input
            type="number"
            step="any"
            value={stepXY}
            onChange={(e) => setStepXY(e.target.value)}
          />
          <button type="button" onClick={() => setUnit((u) => (u === 'mm' ? 'in' : 'mm'))}>
            {unit === 'mm' ? 'Millimeters' : 'Inches'}
          </button>
        </div>

        <div className="dbg-field-row">
          <label>Step size Z</label>
          <input
            type="number"
            step="any"
            value={stepZ}
            onChange={(e) => setStepZ(e.target.value)}
          />
          <button type="button" onClick={() => scaleSteps(2)}>
            Larger
          </button>
        </div>

        <div className="dbg-field-row">
          <label>Feed rate</label>
          <input
            type="number"
            step="any"
            value={feed}
            onChange={(e) => setFeed(e.target.value)}
          />
          <button type="button" onClick={() => scaleSteps(0.5)}>
            Smaller
          </button>
        </div>

        <div className="cs-h" style={{ marginTop: '6px', marginBottom: '9px' }}>Sent to GRBL</div>
        <div className="sublog">
          {'XY step: ' + toMm(num(stepXY, STEP_MIN)).toFixed(3) + ' mm/click\n' +
            'Z step: ' + toMm(num(stepZ, STEP_MIN)).toFixed(3) + ' mm/click\n' +
            'Feed: ' + toMm(num(feed, 1)).toFixed(1) + ' mm/min'}
        </div>

        <div className="cs-h" style={{ marginTop: '10px', marginBottom: '9px' }}>Notes</div>
        <div className="sublog">
          {'GRBL state: ' + (hw.state || '—') + '\n' +
            'Backend: ' + (hw.reachable ? 'reachable' : 'unreachable') + '\n' +
            'Port open: ' + (hw.connected ? 'yes' : 'no') + '\n' +
            (hw.error ? 'Last error: ' + hw.error : 'No errors reported')}
        </div>
      </div>
    </div>
  );
}
