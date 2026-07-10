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
  const lastConnectAttempt = useRef(0);

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
              body: JSON.stringify({ port: GRBL_PORT, baud: GRBL_BAUD }),
            });
            setConnected(true);
          } catch (e) {
            setError(e.message);
          }
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
  }, [refreshStatus]);

  const jog = useCallback(
    async (axis, distanceMm, feed = 300) => {
      try {
        await callApi('/api/jog', {
          method: 'POST',
          body: JSON.stringify({ axis, dx_mm: distanceMm, feed }),
        });
        setError('');
      } catch (e) {
        setError(e.message);
      }
    },
    []
  );

  const home = useCallback(async () => {
    try {
      await callApi('/api/home', { method: 'POST', body: '{}' });
      setError('');
    } catch (e) {
      setError(e.message);
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

  const label = labelFor(reachable ? state : null);

  return { reachable, connected, pos, state, error, label, jog, home, unlock };
}
