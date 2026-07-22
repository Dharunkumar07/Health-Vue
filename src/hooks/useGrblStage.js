import { useCallback, useEffect, useRef, useState } from 'react';

const API_BASE =
  (import.meta?.env?.VITE_GRBL_API && String(import.meta.env.VITE_GRBL_API)) ||
  'http://localhost:8000';
const GRBL_PORT = (import.meta?.env?.VITE_GRBL_PORT && String(import.meta.env.VITE_GRBL_PORT)) || 'COM3';
const GRBL_BAUD = Number(import.meta?.env?.VITE_GRBL_BAUD) || 115200;

const POLL_MS = 700;
const RECONNECT_COOLDOWN_MS = 5000;

async function callApi(path, options = {}) {
  const hasBody = options.body !== undefined && options.body !== null;
  const headers = {
    ...(hasBody ? { 'Content-Type': 'application/json' } : {}),
    ...(options.headers || {}),
  };
  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });

  const contentType = res.headers.get('content-type') || '';
  let payload = null;
  try {
    payload = contentType.includes('application/json') ? await res.json() : { raw: await res.text() };
  } catch {
    payload = { raw: '' };
  }

  if (!res.ok) {
    throw new Error(payload?.detail || payload?.message || payload?.raw || `HTTP ${res.status}`);
  }
  return payload;
}

// GRBL state -> Stage panel label + dot color
function labelFor(state) {
  switch (state) {
    case 'Idle':
      return { text: 'GRBL Idle', dot: '' };
    case 'Run':
    case 'Jog':
      return { text: 'GRBL ' + state, dot: '' };
    case 'Home':
      return { text: 'GRBL Homing', dot: 'amber' };
    case 'Hold':
      return { text: 'GRBL Hold', dot: 'amber' };
    case 'Alarm':
      return { text: 'GRBL Alarm', dot: 'red' };
    case null:
    case undefined:
      return { text: 'GRBL Offline', dot: 'red' };
    default:
      return { text: 'GRBL ' + state, dot: 'amber' };
  }
}

export default function useGrblStage() {
  const [reachable, setReachable] = useState(false); // backend process reachable at all
  const [connected, setConnected] = useState(false); // serial port open on backend
  const [pos, setPos] = useState({ x: 0, y: 0, z: 0 });
  const [state, setState] = useState(null); // raw GRBL state e.g. "Idle"
  const [error, setError] = useState('');
  const [ports, setPorts] = useState([]);
  const [activePort, setActivePort] = useState(GRBL_PORT);
  const lastConnectAttempt = useRef(0);

  const refreshPorts = useCallback(async () => {
    try {
      const r = await callApi('/api/ports');
      setPorts(r.ports || []);
      return r.ports || [];
    } catch {
      return [];
    }
  }, []);

  // Manually connect to a chosen port, bypassing the auto-connect cooldown.
  const connectTo = useCallback(async (port, baud = GRBL_BAUD) => {
    lastConnectAttempt.current = Date.now();
    setActivePort(port);
    try {
      await callApi('/api/connect', {
        method: 'POST',
        body: JSON.stringify({ port, baud }),
      });
      setConnected(true);
      setError('');
      return true;
    } catch (e) {
      setConnected(false);
      setError(e.message);
      return false;
    }
  }, []);

  const refreshStatus = useCallback(async () => {
    const s = await callApi('/api/status');
    if (s.x != null && s.y != null && s.z != null) {
      setPos({ x: s.x, y: s.y, z: s.z });
    }
    setState(s.state ?? null);
  }, []);

  // Poll backend health/status, and best-effort auto-connect once.
  useEffect(() => {
    let cancelled = false;
    let timer = null;

    const tick = async () => {
      try {
        const h = await callApi('/api/health');
        if (cancelled) return;
        setReachable(true);
        setConnected(!!h.connected);
        setError('');

        if (!h.connected && Date.now() - lastConnectAttempt.current > RECONNECT_COOLDOWN_MS) {
          lastConnectAttempt.current = Date.now();
          try {
            await callApi('/api/connect', {
              method: 'POST',
              body: JSON.stringify({ port: activePort, baud: GRBL_BAUD }),
            });
            setConnected(true);
            setError('');
          } catch (e) {
            setError(e.message);
          }
          refreshPorts();
        }

        if (h.connected) await refreshStatus();
      } catch {
        if (cancelled) return;
        setReachable(false);
        setConnected(false);
        setState(null);
      }
    };

    tick();
    timer = setInterval(tick, POLL_MS);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
    };
  }, [refreshStatus, refreshPorts, activePort]);

  // Enumerate ports once up front so a picker has data before the user asks.
  useEffect(() => {
    refreshPorts();
  }, [refreshPorts]);

  const jog = useCallback(
    async (axis, distanceMm, feed = 300) => {
      try {
        // Returns GRBL's actual reply ({sent, response}) rather than just a
        // boolean — GRBL has no closed-loop position feedback, so an axis
        // that physically can't turn (dead driver, unplugged motor) still
        // answers "ok". Seeing the exact command + reply is what tells that
        // case apart from a real rejection (error:/ALARM:), which now throws.
        const r = await callApi('/api/jog', {
          method: 'POST',
          body: JSON.stringify({ axis, dx_mm: distanceMm, feed }),
        });
        setError('');
        return r;
      } catch (e) {
        setError(e.message);
        return null;
      }
    },
    []
  );

  const home = useCallback(async () => {
    try {
      await callApi('/api/home', { method: 'POST', body: '{}' });
      setError('');
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  }, []);

  const disconnect = useCallback(async () => {
    try {
      await callApi('/api/disconnect', { method: 'POST', body: '{}' });
      // Hold off the poll loop's auto-reconnect for one cooldown window so an
      // intentional disconnect doesn't get silently re-opened a few ticks later.
      lastConnectAttempt.current = Date.now();
      setConnected(false);
      setError('');
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  }, []);

  const unlock = useCallback(async () => {
    try {
      await callApi('/api/unlock', { method: 'POST', body: '{}' });
      setError('');
    } catch (e) {
      setError(e.message);
    }
  }, []);

  // Ctrl-X soft reset — the only thing that interrupts a homing cycle.
  // Not resumable: caller must treat this as "unhomed, re-home required".
  const abort = useCallback(async () => {
    try {
      await callApi('/api/abort', { method: 'POST', body: '{}' });
      setError('');
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  }, []);

  // Jog cancel (0x85) — stops an in-progress $J= move and returns straight to
  // Idle, no re-home required. This is what a "Stop" button should send;
  // abort() (Ctrl-X) is the heavier, resumable-only-via-rehome emergency stop.
  const jogStop = useCallback(async () => {
    try {
      await callApi('/api/jog-stop', { method: 'POST', body: '{}' });
      setError('');
      return true;
    } catch (e) {
      setError(e.message);
      return false;
    }
  }, []);

  const label = labelFor(reachable ? state : null);

  return {
    reachable,
    connected,
    pos,
    state,
    error,
    label,
    jog,
    home,
    unlock,
    abort,
    jogStop,
    disconnect,
    ports,
    activePort,
    refreshPorts,
    connectTo,
  };
}
