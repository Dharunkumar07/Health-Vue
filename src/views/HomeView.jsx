import HChat from '../components/HChat';

const RECENT = [
  { acc: 'PBS-2261', sub: 'Peripheral smear · Giemsa', fill: 'blood', badge: 'ai' },
  { acc: 'HPE-1180', sub: 'Biopsy · H&E', fill: 'tissue', badge: 'ai' },
  { acc: 'CYT-0442', sub: 'Cytology · Pap', fill: 'tissue', badge: 'rev' },
  { acc: 'PBS-2260', sub: 'Peripheral smear · Giemsa', fill: 'blood', badge: null },
  { acc: 'URN-0091', sub: 'Urine · wet mount', fill: 'tissue', badge: null },
];

export default function HomeView({ onNavigate }) {
  return (
    <div className="pad">
      <div className="greet">Good morning, Dr. Bairavi</div>
      <div className="greet-sub">Bench 01 · 6 slides in queue · Stage ready · Data staying on-device</div>

      <div className="home-grid">
        <div>
          <div className="quick">
            <button className="qbtn primary" onClick={() => onNavigate('live')}>
              <div className="qi">
                <svg viewBox="0 0 24 24">
                  <circle cx="12" cy="12" r="3.2" />
                  <path d="M12 2v3M12 19v3M2 12h3M19 12h3" />
                </svg>
              </div>
              <div className="qt">Start Live Mode</div>
              <div className="qd">Look through the scope on-screen. Drive the stage, ask H what it sees.</div>
            </button>
            <button className="qbtn" onClick={() => onNavigate('repository')}>
              <div className="qi">
                <svg viewBox="0 0 24 24">
                  <rect x="3" y="4" width="18" height="5" rx="1.5" />
                  <rect x="3" y="11" width="18" height="5" rx="1.5" />
                </svg>
              </div>
              <div className="qt">Review Slides</div>
              <div className="qd">Open scanned whole-slide images with AI pre-read.</div>
            </button>
          </div>

          <div className="card">
            <div className="card-h">
              Recent slides <span className="lab">last 24h</span>
            </div>
            <div className="recent-strip">
              {RECENT.map((r) => (
                <div className="rslide" key={r.acc} onClick={() => onNavigate('viewer')}>
                  <div className="thumb">
                    <div className={r.fill + '-fill'}></div>
                    {r.badge && <span className={'badge ' + r.badge}>{r.badge.toUpperCase()}</span>}
                  </div>
                  <div className="meta">
                    <div className="acc">{r.acc}</div>
                    <div className="sub">{r.sub}</div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <div className="card">
            <div className="card-h">Device status</div>
            <div className="status-row">
              <span className="sn">Motion controller</span>
              <span className="sv ok">GRBL online</span>
            </div>
            <div className="status-row">
              <span className="sn">Camera</span>
              <span className="sv ok">20 MP · 30 fps</span>
            </div>
            <div className="status-row">
              <span className="sn">Edge AI</span>
              <span className="sv ok">Orin · warm</span>
            </div>
            <div className="status-row">
              <span className="sn">Storage</span>
              <span className="sv">318 / 512 GB</span>
            </div>
            <div className="status-row">
              <span className="sn">Cloud</span>
              <span className="sv">Off · local only</span>
            </div>
          </div>
          <div className="card" style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
            <HChat
              caption="Assistant · on-device"
              initialMessages={[
                {
                  who: 'h',
                  text: 'Morning. 6 slides finished scanning overnight. PBS-2261 flagged 3 fields worth a look. Want the summary?',
                },
              ]}
              replies={[
                '6 slides done overnight. PBS-2261 has 3 flagged fields; the rest read clean. HPE-1180 and 3 signed-outs are archived.',
                'PBS-2261 and CYT-0442 are the two waiting on you. Everything else is signed.',
              ]}
              chips={[
                { label: 'Overnight queue', text: 'Summarize the overnight queue' },
                { label: 'Needs review', text: 'Which slides need review?' },
              ]}
              placeholder="Ask H anything…"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
