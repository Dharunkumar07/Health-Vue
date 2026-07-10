import { useEffect, useRef, useState } from 'react';

const GRBL_SETTINGS = [
  '$0=10', '$1=25', '$2=0', '$3=6', '$4=0', '$5=0', '$10=1', '$11=0.010', '$12=0.002',
  '$20=1', '$21=1', '$22=1', '$23=3', '$24=100.000', '$25=1500.000', '$26=250', '$27=2.000',
  '$100=800.000', '$101=800.000', '$102=1600.000', '$110=2000.000', '$111=2000.000', '$112=800.000',
  '$120=100.000', '$121=100.000', '$122=50.000', '$130=26.000', '$131=76.000', '$132=4.000',
];

const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

let lineId = 0;
function nextLineId() {
  lineId += 1;
  return lineId;
}

export default function ConsoleView({ active }) {
  const grbl = useRef({
    sim: true,
    connected: true,
    poll: true,
    state: 'Idle',
    mpos: { x: 0, y: 0, z: 0 },
    feed: 0,
    spindle: 0,
    absolute: true,
    hist: [],
    hix: -1,
  });
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  const [lines, setLines] = useState([]);
  const [machine, setMachine] = useState({ x: 0, y: 0, z: 0, feed: 0, spindle: 0 });
  const [stateLabel, setStateLabel] = useState('Idle');
  const [connLabel, setConnLabel] = useState('virtual · /dev/sim0 · 115200');
  const [connDot, setConnDot] = useState('');
  const [simOn, setSimOn] = useState(true);
  const [pollOn, setPollOn] = useState(true);
  const [termInput, setTermInput] = useState('');

  const termRef = useRef(null);

  useEffect(() => {
    const el = termRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lines]);

  function termLine(text, cls) {
    const ts = new Date().toTimeString().slice(0, 8);
    setLines((prev) => [...prev, { id: nextLineId(), text, cls: cls || 'tx-sys', ts }]);
  }

  function setGState(s) {
    grbl.current.state = s;
    setStateLabel(s);
  }

  function paintMachine() {
    setMachine({ x: grbl.current.mpos.x, y: grbl.current.mpos.y, z: grbl.current.mpos.z, feed: grbl.current.feed, spindle: grbl.current.spindle });
  }

  function statusReport() {
    const p = grbl.current.mpos;
    return (
      '<' + grbl.current.state + '|MPos:' + p.x.toFixed(3) + ',' + p.y.toFixed(3) + ',' + p.z.toFixed(3) +
      '|FS:' + grbl.current.feed + ',' + grbl.current.spindle + '|WCO:0.000,0.000,0.000>'
    );
  }

  function moveTo(nx, ny, nz, feed) {
    if (grbl.current.state === 'Alarm') {
      termLine('error:9 · G-code locked out during alarm or jog', 'tx-err');
      return;
    }
    const from = { ...grbl.current.mpos };
    const to = { x: clamp(nx, 0, 26), y: clamp(ny, 0, 76), z: clamp(nz, 0, 4) };
    grbl.current.feed = feed || 1000;
    setGState(grbl.current.state === 'Jog' ? 'Jog' : 'Run');
    const t0 = performance.now();
    const dur = 450;
    function step(now) {
      const k = Math.min(1, (now - t0) / dur);
      grbl.current.mpos.x = from.x + (to.x - from.x) * k;
      grbl.current.mpos.y = from.y + (to.y - from.y) * k;
      grbl.current.mpos.z = from.z + (to.z - from.z) * k;
      paintMachine();
      if (k < 1) {
        requestAnimationFrame(step);
      } else {
        grbl.current.mpos = { ...to };
        grbl.current.feed = 0;
        setGState('Idle');
        paintMachine();
      }
    }
    requestAnimationFrame(step);
  }

  function parseAxes(s) {
    const out = {};
    const gx = /X(-?\d+\.?\d*)/.exec(s); if (gx) out.x = parseFloat(gx[1]);
    const gy = /Y(-?\d+\.?\d*)/.exec(s); if (gy) out.y = parseFloat(gy[1]);
    const gz = /Z(-?\d+\.?\d*)/.exec(s); if (gz) out.z = parseFloat(gz[1]);
    const gf = /F(\d+\.?\d*)/.exec(s); if (gf) out.f = parseFloat(gf[1]);
    out.abs = /G90/.test(s);
    return out;
  }

  function processCmd(raw) {
    const cmd = raw.trim();
    if (!grbl.current.connected && cmd !== '\\x18') {
      termLine('error: no device · port closed. Toggle Simulator or reconnect.', 'tx-err');
      return;
    }

    if (cmd === '?') { termLine(statusReport(), 'tx-status'); return; }
    if (cmd === '!') { setGState('Hold'); termLine('[MSG:Feed hold]', 'tx-msg'); return; }
    if (cmd === '~') { if (grbl.current.state === 'Hold') { setGState('Idle'); termLine('[MSG:Resuming]', 'tx-msg'); } return; }
    if (cmd === '\\x18' || cmd.toLowerCase() === 'ctrl-x') {
      grbl.current.mpos = { x: 0, y: 0, z: 0 }; grbl.current.feed = 0; setGState('Idle'); paintMachine();
      termLine("Grbl 1.1h ['$' for help]", 'tx-sys'); termLine('[MSG:Reset]', 'tx-msg'); return;
    }

    const c = cmd.toUpperCase();
    if (c === '$H') {
      setGState('Home'); termLine('[MSG:Homing cycle]', 'tx-msg');
      setTimeout(() => { grbl.current.mpos = { x: 0, y: 0, z: 0 }; paintMachine(); setGState('Idle'); termLine('ok', 'tx-ok'); }, 700);
      return;
    }
    if (c === '$X') { if (grbl.current.state === 'Alarm') setGState('Idle'); termLine('[MSG:Caution: Unlocked]', 'tx-msg'); termLine('ok', 'tx-ok'); return; }
    if (c === '$$') { GRBL_SETTINGS.forEach((s) => termLine(s, 'tx-status')); termLine('ok', 'tx-ok'); return; }
    if (c === '$G') { termLine('[GC:G0 G54 G17 ' + (grbl.current.absolute ? 'G90' : 'G91') + ' G21 G94 M5 M9 T0 F0 S0]', 'tx-status'); termLine('ok', 'tx-ok'); return; }
    if (c === '$I') { termLine('[VER:1.1h.20250120:MICRA]', 'tx-status'); termLine('[OPT:VNMZ,15,128]', 'tx-status'); termLine('ok', 'tx-ok'); return; }
    if (c === '$' || c === '$$H' || c === 'HELP') { termLine('[HLP:$$ $# $G $I $N $x=val $Nx=line $J=line $C $X $H ~ ! ? ctrl-x]', 'tx-status'); termLine('ok', 'tx-ok'); return; }

    if (grbl.current.state === 'Alarm') { termLine('error:9 · G-code locked out during alarm. Send $X to unlock.', 'tx-err'); return; }

    if (/^G90/.test(c)) grbl.current.absolute = true;
    if (/^G91/.test(c)) grbl.current.absolute = false;

    if (c.startsWith('$J=')) {
      const m = parseAxes(c);
      const nx = grbl.current.mpos.x + (m.x || 0), ny = grbl.current.mpos.y + (m.y || 0), nz = grbl.current.mpos.z + (m.z || 0);
      setGState('Jog');
      moveTo(m.abs ? (m.x ?? grbl.current.mpos.x) : nx, m.abs ? (m.y ?? grbl.current.mpos.y) : ny, m.abs ? (m.z ?? grbl.current.mpos.z) : nz, m.f || 1000);
      termLine('ok', 'tx-ok'); return;
    }
    if (/^G0|^G1|^X|^Y|^Z/.test(c)) {
      const m = parseAxes(c);
      const nx = m.x != null ? (grbl.current.absolute ? m.x : grbl.current.mpos.x + m.x) : grbl.current.mpos.x;
      const ny = m.y != null ? (grbl.current.absolute ? m.y : grbl.current.mpos.y + m.y) : grbl.current.mpos.y;
      const nz = m.z != null ? (grbl.current.absolute ? m.z : grbl.current.mpos.z + m.z) : grbl.current.mpos.z;
      moveTo(nx, ny, nz, m.f || 1000); termLine('ok', 'tx-ok'); return;
    }
    if (c === '') { return; }
    if (/^[GM]\d/.test(c)) { termLine('ok', 'tx-ok'); return; }
    termLine('error:1 · unsupported or invalid command', 'tx-err');
  }

  function sendCmd() {
    const v = termInput;
    if (!v.trim()) return;
    termLine('› ' + v, 'tx-out');
    grbl.current.hist.push(v);
    grbl.current.hix = grbl.current.hist.length;
    setTermInput('');
    processCmd(v);
  }

  function quick(c) {
    termLine('› ' + c, 'tx-out');
    processCmd(c);
  }

  function clearTerm() {
    setLines([]);
    termLine('cleared', 'tx-sys');
  }

  function histUp(e) {
    if (grbl.current.hix > 0) {
      grbl.current.hix -= 1;
      setTermInput(grbl.current.hist[grbl.current.hix]);
      e.preventDefault();
    }
  }
  function histDown(e) {
    if (grbl.current.hix < grbl.current.hist.length - 1) {
      grbl.current.hix += 1;
      setTermInput(grbl.current.hist[grbl.current.hix]);
    } else {
      grbl.current.hix = grbl.current.hist.length;
      setTermInput('');
    }
  }

  function toggleSim() {
    const next = !grbl.current.sim;
    grbl.current.sim = next;
    setSimOn(next);
    setConnLabel(next ? 'virtual · /dev/sim0 · 115200' : 'hardware · /dev/ttyUSB0 · 115200');
    termLine(next ? '[sys] switched to VIRTUAL device (simulator)' : '[sys] simulator OFF — production would open Web Serial to /dev/ttyUSB0', 'tx-msg');
  }

  function pollToggle() {
    const next = !grbl.current.poll;
    grbl.current.poll = next;
    setPollOn(next);
  }

  function fault(kind) {
    if (kind === 'limit') {
      setGState('Alarm'); setConnDot('amber');
      termLine('ALARM:1', 'tx-alarm'); termLine('[MSG:Reset to continue] Hard limit triggered — machine position lost.', 'tx-err');
    }
    if (kind === 'lost') {
      grbl.current.connected = false; setConnDot('red');
      setConnLabel('disconnected · port closed');
      termLine('[sys] serial link dropped — no response from motion controller', 'tx-err');
    }
    if (kind === 'stall') {
      setGState('Alarm'); termLine('ALARM:1', 'tx-alarm');
      termLine('[MSG:Z axis lost steps — expected 2.140 measured 1.980] Re-home Z.', 'tx-err');
    }
    if (kind === 'recover') {
      grbl.current.connected = true; setConnDot('');
      setConnLabel(grbl.current.sim ? 'virtual · /dev/sim0 · 115200' : 'hardware · /dev/ttyUSB0 · 115200');
      setGState('Home'); termLine('[sys] reconnected · $X · $H', 'tx-msg');
      setTimeout(() => { grbl.current.mpos = { x: 0, y: 0, z: 0 }; paintMachine(); setGState('Idle'); termLine('ok · faults cleared, homed', 'tx-ok'); }, 700);
    }
  }

  useEffect(() => {
    const id = setInterval(() => {
      if (grbl.current.poll && grbl.current.connected && activeRef.current) {
        if (grbl.current.state === 'Run' || grbl.current.state === 'Jog' || grbl.current.state === 'Home') {
          termLine(statusReport(), 'tx-status');
        }
        paintMachine();
      }
    }, 900);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const t = setTimeout(() => {
      termLine("Grbl 1.1h ['$' for help]", 'tx-sys');
      termLine('[sys] Micra virtual motion controller ready · type ? for status', 'tx-msg');
      paintMachine();
    }, 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stateClass = 'con-badge state' + (stateLabel === 'Alarm' ? ' alarm' : (stateLabel === 'Run' || stateLabel === 'Jog' ? ' run' : ''));

  return (
    <div className="con-wrap">
      <div className="con-main">
        <div className="con-topbar">
          <div className={'sim-toggle' + (simOn ? ' on' : '')} onClick={toggleSim}>
            <span>Simulator</span>
            <span className="switch"></span>
          </div>
          <div className="con-conn">
            <span className={'dot' + (connDot ? ' ' + connDot : '')}></span>
            <span>{connLabel}</span>
          </div>
          {simOn && <span className="con-badge sim">SIM MODE</span>}
          <span className={stateClass}>{stateLabel}</span>
          <div style={{ marginLeft: 'auto', fontFamily: 'var(--mono)', fontSize: '10px', color: '#4a5666' }}>GRBL 1.1h · Micra motion FW</div>
        </div>

        <div className="terminal">
          <div className="term-head">
            <span className="tdot" style={{ background: 'var(--amber)' }}></span>
            <span>serial monitor — X/Y/Z motion controller</span>
            <div className="th-actions">
              <button onClick={pollToggle}>status poll: {pollOn ? 'on' : 'off'}</button>
              <button onClick={clearTerm}>clear</button>
            </div>
          </div>
          <div className="term-body" ref={termRef}>
            {lines.map((l) => (
              <div className={'tl ' + l.cls} key={l.id}>
                <span className="ts">{l.ts}</span>
                {l.text}
              </div>
            ))}
          </div>
          <div className="term-input">
            <span className="prompt">›</span>
            <input
              value={termInput}
              placeholder="send G-code or $ command…"
              autoComplete="off"
              onChange={(e) => setTermInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') sendCmd();
                if (e.key === 'ArrowUp') histUp(e);
                if (e.key === 'ArrowDown') histDown(e);
              }}
            />
            <button className="send" onClick={sendCmd}>SEND</button>
          </div>
        </div>

        <div className="quickcmds">
          <button className="qcmd rt" onClick={() => quick('?')}>? status</button>
          <button className="qcmd" onClick={() => quick('$H')}>$H home</button>
          <button className="qcmd" onClick={() => quick('$X')}>$X unlock</button>
          <button className="qcmd" onClick={() => quick('$$')}>$$ settings</button>
          <button className="qcmd" onClick={() => quick('$G')}>$G state</button>
          <button className="qcmd" onClick={() => quick('$J=G91 X5 F1000')}>jog +X5</button>
          <button className="qcmd" onClick={() => quick('G90 G0 X12 Y8')}>goto 12,8</button>
          <button className="qcmd rt" onClick={() => quick('!')}>! hold</button>
          <button className="qcmd rt" onClick={() => quick('~')}>~ resume</button>
          <button className="qcmd danger" onClick={() => quick('\\x18')}>⌫X soft-reset</button>
        </div>
      </div>

      <div className="con-side">
        <div>
          <div className="cs-h" style={{ marginBottom: '9px' }}>Live machine state</div>
          <div className="live-coords">
            <div className="lc-row"><span className="k">MPos X</span><span className="v">{machine.x.toFixed(3)}<span className="m"> mm</span></span></div>
            <div className="lc-row"><span className="k">MPos Y</span><span className="v">{machine.y.toFixed(3)}<span className="m"> mm</span></span></div>
            <div className="lc-row"><span className="k">MPos Z</span><span className="v">{machine.z.toFixed(3)}<span className="m"> mm</span></span></div>
            <div className="lc-row"><span className="k">Feed/Spd</span><span className="v">{machine.feed} · {machine.spindle}</span></div>
            <div className="lc-row"><span className="k">Buffer</span><span className="v">15,128</span></div>
          </div>
        </div>

        <div>
          <div className="cs-h" style={{ marginBottom: '9px' }}>
            Inject fault <span style={{ fontWeight: 400, textTransform: 'none', letterSpacing: 0, color: '#4a5666' }}>— field-test the UI</span>
          </div>
          <div className="fault-btns">
            <button className="fault-btn" onClick={() => fault('limit')}><span className="fi">ALARM:1</span> Trigger hard limit (X endstop)</button>
            <button className="fault-btn" onClick={() => fault('lost')}><span className="fi">DISC</span> Drop serial connection</button>
            <button className="fault-btn" onClick={() => fault('stall')}><span className="fi">STALL</span> Z stepper stall / lost steps</button>
            <button className="fault-btn" onClick={() => fault('recover')} style={{ borderColor: 'rgba(39,165,103,.35)', color: '#7fd0a5' }}>
              <span className="fi" style={{ color: 'var(--green)' }}>RESET</span> Clear faults &amp; re-home
            </button>
          </div>
        </div>

        <div>
          <div className="cs-h" style={{ marginBottom: '9px' }}>Key settings ($$)</div>
          <div className="settings-tbl">
            <div className="st-row"><span className="sk">$100</span><span className="sd">X steps/mm</span><span className="sv">800.0</span></div>
            <div className="st-row"><span className="sk">$101</span><span className="sd">Y steps/mm</span><span className="sv">800.0</span></div>
            <div className="st-row"><span className="sk">$102</span><span className="sd">Z steps/mm</span><span className="sv">1600.0</span></div>
            <div className="st-row"><span className="sk">$110</span><span className="sd">X max rate</span><span className="sv">2000</span></div>
            <div className="st-row"><span className="sk">$130</span><span className="sd">X travel</span><span className="sv">26.0</span></div>
            <div className="st-row"><span className="sk">$131</span><span className="sd">Y travel</span><span className="sv">76.0</span></div>
          </div>
        </div>

        <div>
          <div className="cs-h" style={{ marginBottom: '9px' }}>Subsystem log</div>
          <div className="sublog">{'[cam ] IMX sensor 20MP @30fps · OK\n[ai  ] Orin engine warm · 42°C\n[stor] 318/512 GB · encrypted\n[net ] cloud sync OFF · local only'}</div>
        </div>
      </div>
    </div>
  );
}
