import { useState } from 'react';
import HChat from '../components/HChat';

const MAG_SCALE = { 4: 0.6, 10: 1, 40: 1.9, 100: 3.2 };

export default function ViewerView({ onNavigate }) {
  const [detOn, setDetOn] = useState(true);
  const [mag, setMag] = useState(40);

  return (
    <div className="body-split">
      <div className="vw-main">
        <div className="vw-canvas">
          <div
            className="vw-tissue"
            id="vwTissue"
            style={{ transform: `scale(${MAG_SCALE[mag]})` }}
          ></div>
          {detOn && (
            <div className="det-layer" id="detLayer">
              <div className="detbox" style={{ left: '28%', top: '34%', width: '70px', height: '60px' }}>
                <span className="dl">Blast · 0.91</span>
              </div>
              <div className="detbox warn" style={{ left: '54%', top: '52%', width: '58px', height: '52px' }}>
                <span className="dl">Atypical · 0.78</span>
              </div>
              <div className="detbox" style={{ left: '64%', top: '30%', width: '50px', height: '46px' }}>
                <span className="dl">Lymphocyte · 0.88</span>
              </div>
            </div>
          )}
          <div className="vw-toolbar">
            <div
              className={'vw-tool' + (detOn ? ' on' : '')}
              onClick={() => setDetOn((v) => !v)}
            >
              <svg viewBox="0 0 24 24">
                <rect x="4" y="4" width="7" height="7" rx="1" />
                <rect x="13" y="13" width="7" height="7" rx="1" />
              </svg>
              AI overlay
            </div>
            <div className="vw-tool">
              <svg viewBox="0 0 24 24">
                <path d="M4 7V4h3M17 4h3v3M20 17v3h-3M7 20H4v-3" />
              </svg>
              Measure
            </div>
            <div className="vw-tool">
              <svg viewBox="0 0 24 24">
                <path d="M12 5v14M5 12h14" />
              </svg>
              Annotate
            </div>
          </div>
          <div className="vw-nav">
            <div className="nav-tissue"></div>
            <div className="nav-box"></div>
          </div>
          <div className="mag-bar">
            {[4, 10, 40, 100].map((m) => (
              <button
                key={m}
                className={mag === m ? 'on' : ''}
                onClick={() => setMag(m)}
              >
                {m}×
              </button>
            ))}
          </div>
        </div>
      </div>
      <div className="vw-side">
        <HChat
          caption="Reading PBS-2261"
          initialMessages={[
            {
              who: 'h',
              text:
                "This peripheral smear shows normochromic normocytic RBCs. I count 3 fields with cells morphologically consistent with blasts — highest confidence in the upper-left region I've boxed.",
              cite: '3 detections · avg 0.86',
            },
            { who: 'u', text: "What's the WBC differential estimate?" },
            {
              who: 'h',
              text:
                "On this field: ~62% neutrophils, 24% lymphocytes, 8% monocytes, plus the atypical population. This is a field estimate — I'd confirm across 8–10 fields before it goes in the report.",
            },
          ]}
          replies={[
            'Jumping to the upper-left field now — that’s the highest-confidence blast cluster at 0.91.',
            "I've pre-filled the microscopic description and impression. Head to the report to review and edit before sign-out.",
          ]}
          chips={[
            { label: 'Flagged fields', text: 'Jump to the flagged fields' },
            { label: 'Draft report', text: 'Draft the report' },
          ]}
          placeholder="Ask about this slide…"
          footer={
            <button className="btn blue" style={{ marginTop: '12px' }} onClick={() => onNavigate('report')}>
              <svg viewBox="0 0 24 24">
                <path d="M6 2h9l5 5v15H6z" />
                <path d="M9 13h7M9 17h7" />
              </svg>
              Generate report
            </button>
          }
        />
      </div>
    </div>
  );
}
