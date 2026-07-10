import { useEffect, useRef, useState } from 'react';

const WIFI_NETWORKS = ['Bench-01-5G', 'Lab-Guest', 'IONOS-Office', 'Pixel-7a'];

export default function StatusBar({ title, crumb, cloudOn, onToggleCloud }) {
  const [now, setNow] = useState(() => new Date());
  const [wifiOn, setWifiOn] = useState(false);
  const [wifiOpen, setWifiOpen] = useState(false);
  const [wifiNetwork, setWifiNetwork] = useState(null);
  const wifiRef = useRef(null);

  function toggleWifiOn(e) {
    e.stopPropagation();
    setWifiOn((v) => {
      const next = !v;
      if (!next) {
        setWifiNetwork(null);
        setWifiOpen(false);
      }
      return next;
    });
  }

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    function onDocClick(e) {
      if (wifiRef.current && !wifiRef.current.contains(e.target)) setWifiOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, []);

  const time = now.toTimeString().slice(0, 5);
  const date = now.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });

  return (
    <div className="statusbar">
      <div>
        <div className="sb-title">{title}</div>
      </div>
      <div className="sb-crumb">{crumb}</div>
      <div className="sb-right">
        <div className="pill">
          <span className="dot"></span>Stage homed
        </div>
        <div className="pill">
          <span className="dot"></span>AI engine
        </div>
        <div className="wifi-wrap" ref={wifiRef}>
          <div
            className={'pill wifi-pill' + (wifiOn ? ' on' : '') + (wifiNetwork ? ' connected' : '')}
          >
            <svg viewBox="0 0 24 24" className="wifi-icon">
              <path d="M2 8.5a16 16 0 0 1 20 0" />
              <path d="M5 12a11 11 0 0 1 14 0" />
              <path d="M8.5 15.5a6 6 0 0 1 7 0" />
              <circle cx="12" cy="19" r="1.2" fill="currentColor" stroke="none" />
            </svg>
            <span
              className="wifi-label"
              onClick={() => wifiOn && setWifiOpen((v) => !v)}
            >
              {wifiOn ? wifiNetwork || 'Not connected' : 'Wi-Fi off'}
            </span>
            <span className="switch wifi-switch" onClick={toggleWifiOn}></span>
          </div>
          {wifiOpen && wifiOn && (
            <div className="wifi-menu">
              <div className="wifi-menu-title">Networks</div>
              {WIFI_NETWORKS.map((n) => (
                <div
                  key={n}
                  className={'wifi-item' + (wifiNetwork === n ? ' active' : '')}
                  onClick={() => {
                    setWifiNetwork(n);
                    setWifiOpen(false);
                  }}
                >
                  <span>{n}</span>
                  {wifiNetwork === n && <span className="wifi-connected">Connected</span>}
                </div>
              ))}
              {wifiNetwork && (
                <div
                  className="wifi-item disconnect"
                  onClick={() => {
                    setWifiNetwork(null);
                    setWifiOpen(false);
                  }}
                >
                  Disconnect
                </div>
              )}
            </div>
          )}
        </div>
        <div
          className={'cloud-toggle' + (cloudOn ? ' on' : '')}
          onClick={onToggleCloud}
        >
          <span>Cloud sync</span>
          <span className="switch"></span>
        </div>
        <div className="time-block">
          <div className="date">{date}</div>
          <div className="time">{time}</div>
        </div>
      </div>
    </div>
  );
}
