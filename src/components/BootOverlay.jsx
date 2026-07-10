import { useEffect, useState } from 'react';

const CHECKS = [
  'Optical camera · Sony IMX sensor',
  'Motion controller · GRBL / ATmega link',
  'Stage homing · X / Y / Z endstops',
  'Edge AI engine · Jetson Orin Nano',
  'Local storage · 512 GB · encrypted',
  'Cloud sync · standby (opt-in)',
];

export default function BootOverlay({ onDismiss }) {
  const [visible, setVisible] = useState(true);
  const [rowState, setRowState] = useState(() => CHECKS.map(() => 'pending'));
  const [readyIn, setReadyIn] = useState(false);

  useEffect(() => {
    const timers = [];
    let i = 0;
    const reveal = () => {
      if (i < CHECKS.length) {
        const idx = i;
        setRowState((prev) => {
          const next = [...prev];
          next[idx] = 'in';
          return next;
        });
        timers.push(
          setTimeout(() => {
            setRowState((prev) => {
              const next = [...prev];
              next[idx] = 'done';
              return next;
            });
          }, 380)
        );
        i += 1;
        timers.push(setTimeout(reveal, 300));
      }
    };
    timers.push(setTimeout(reveal, 400));
    timers.push(setTimeout(() => setReadyIn(true), 2900));
    timers.push(
      setTimeout(() => {
        setVisible(false);
        onDismiss?.();
      }, 6000)
    );
    return () => timers.forEach(clearTimeout);
  }, [onDismiss]);

  function dismiss() {
    setVisible(false);
    onDismiss?.();
  }

  return (
    <div
      id="boot"
      className={visible ? 'show' : ''}
      onClick={dismiss}
    >
      <div className="boot-logo">
        <svg width="150" height="46" viewBox="0 0 300 92" xmlns="http://www.w3.org/2000/svg">
          <rect x="18" y="10" width="12" height="72" rx="6" fill="#fff" />
          <rect x="58" y="10" width="12" height="72" rx="6" fill="#fff" />
          <circle cx="24" cy="24" r="2.4" fill="#8fa0b4" />
          <circle cx="24" cy="68" r="2.4" fill="#8fa0b4" />
          <circle cx="64" cy="24" r="2.4" fill="#8fa0b4" />
          <circle cx="64" cy="68" r="2.4" fill="#8fa0b4" />
          <rect x="26" y="36" width="36" height="20" rx="4" fill="none" stroke="#9fb0c4" strokeWidth="2.5" />
          <circle cx="44" cy="46" r="8" fill="#e0455e" />
          <circle cx="41" cy="43" r="2.6" fill="#ff9aab" />
          <text x="86" y="62" fontFamily="Space Grotesk, sans-serif" fontWeight="700" fontSize="52" fill="#fff">ealth</text>
          <text x="210" y="62" fontFamily="Space Grotesk, sans-serif" fontWeight="700" fontSize="52" fill="#5da3e6">Vue</text>
        </svg>
      </div>
      <div className="boot-checks">
        {CHECKS.map((label, idx) => {
          const st = rowState[idx];
          return (
            <div className={'chk' + (st === 'pending' ? '' : ' in') + (st === 'done' ? ' done' : '')} key={label}>
              <span className="ci">
                {st === 'done' ? <span className="tick">✓</span> : <span className="spin"></span>}
              </span>
              <span>{label}</span>
            </div>
          );
        })}
      </div>
      <div className={'boot-ready' + (readyIn ? ' in' : '')}>
        Micra ready
        <small>Tap anywhere to begin</small>
      </div>
    </div>
  );
}
