export default function ReportView({ cloudOn }) {
  return (
    <div className="rep-split">
      <div className="rep-main">
        <div className="rep-doc">
          <div className="rep-dochead">
            <div>
              <div className="rt">Peripheral Smear Report</div>
              <div className="rmeta">PBS-2261 · Giemsa · captured 08:14</div>
            </div>
            <div className="ai-flag">
              <span
                className="cell"
                style={{ width: '7px', height: '7px', borderRadius: '50%', background: 'radial-gradient(circle at 35% 30%,#ff8095,#e0455e)' }}
              ></span>
              H drafted · unsigned
            </div>
          </div>

          <div className="rep-sec">
            <div className="rl">Specimen</div>
            <div className="rep-grid2">
              <div className="rep-kv">
                <div className="k">Accession</div>
                <div className="v">PBS-2261</div>
              </div>
              <div className="rep-kv">
                <div className="k">Stain</div>
                <div className="v">Giemsa</div>
              </div>
              <div className="rep-kv">
                <div className="k">Fields reviewed</div>
                <div className="v">10</div>
              </div>
              <div className="rep-kv">
                <div className="k">Magnification</div>
                <div className="v">40× / 100× oil</div>
              </div>
            </div>
          </div>

          <div className="rep-sec">
            <div className="rl">Microscopic description</div>
            <div className="rep-field ai">
              <span className="ai-mark">H DRAFT · editable</span>
              RBCs are predominantly normochromic and normocytic with mild anisocytosis. WBC differential
              estimate: neutrophils 62%, lymphocytes 24%, monocytes 8%. A population of atypical mononuclear
              cells with high nuclear-to-cytoplasmic ratio and open chromatin is noted in 3 of 10 fields,
              morphologically consistent with blasts. Platelets appear adequate on smear.
            </div>
          </div>

          <div className="rep-sec">
            <div className="rl">Impression</div>
            <div className="rep-field ai">
              <span className="ai-mark">H DRAFT · editable</span>
              Peripheral smear with atypical mononuclear cells suspicious for a blast population. Correlation
              with flow cytometry and bone marrow examination is recommended.
            </div>
          </div>

          <div className="rep-sec" style={{ borderBottom: 'none' }}>
            <div className="rl">Pathologist note</div>
            <div className="rep-field">Tap to add your note before sign-out…</div>
          </div>
        </div>
      </div>

      <div className="rep-side">
        <div className="lab-small">Reference field</div>
        <div className="rep-thumb">
          <div className="blood-fill"></div>
          <span className="badge ai" style={{ top: '6px', right: '6px' }}>AI</span>
        </div>
        <div className="rep-kv">
          <div className="k">AI confidence</div>
          <div className="v" style={{ color: 'var(--amber)' }}>Review advised</div>
        </div>
        <div className="rep-kv">
          <div className="k">Model</div>
          <div className="v" style={{ fontSize: '11px' }}>H-Heme v2 · on-device</div>
        </div>
        <div style={{ flex: 1 }}></div>
        <button className="btn ghost">
          <svg viewBox="0 0 24 24">
            <path d="M12 5v14M5 12h14" />
          </svg>
          Add finding
        </button>
        <button className="btn blue">
          {cloudOn ? (
            <svg viewBox="0 0 24 24">
              <path d="M7 18a5 5 0 010-10 6 6 0 0111.3 2A4 4 0 0117 18z" />
              <path d="M12 17v-5M9 14l3-2 3 2" />
            </svg>
          ) : (
            <svg viewBox="0 0 24 24">
              <path d="M7 18a5 5 0 010-10 6 6 0 0111.3 2A4 4 0 0117 18z" />
              <path d="M12 12v5M9 14l3-2 3 2" />
            </svg>
          )}
          Export PDF · {cloudOn ? 'cloud' : 'local'}
        </button>
        <button className="btn primary">
          <svg viewBox="0 0 24 24">
            <path d="M20 6L9 17l-5-5" />
          </svg>
          Sign out report
        </button>
      </div>
    </div>
  );
}
