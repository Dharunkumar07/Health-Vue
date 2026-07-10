const SLIDES = [
  { acc: 'PBS-2261', name: 'Peripheral blood smear', stain: 'Giemsa', mag: '40×', fill: 'blood', badge: 'AI PRE-READ', badgeType: 'ai', time: '08:14', status: 'amber', statusLabel: '3 flags' },
  { acc: 'HPE-1180', name: 'Endometrial biopsy', stain: 'H&E', mag: '20×', fill: 'tissue', badge: 'AI PRE-READ', badgeType: 'ai', time: '07:52', status: 'green', statusLabel: 'Clear' },
  { acc: 'CYT-0442', name: 'Cervical cytology', stain: 'Pap', mag: '40×', fill: 'tissue', badge: 'REVIEW', badgeType: 'rev', time: '07:30', status: 'amber', statusLabel: 'Pending' },
  { acc: 'PBS-2260', name: 'Peripheral blood smear', stain: 'Giemsa', mag: '40×', fill: 'blood', badge: null, time: 'Yesterday', status: 'green', statusLabel: 'Signed' },
  { acc: 'HPE-1179', name: 'Cervical punch biopsy', stain: 'H&E', mag: '20×', fill: 'tissue', badge: 'AI PRE-READ', badgeType: 'ai', time: 'Yesterday', status: 'green', statusLabel: 'Signed' },
  { acc: 'URN-0091', name: 'Urine microscopy', stain: 'Wet mount', mag: '40×', fill: 'tissue', badge: null, time: 'Yesterday', status: 'green', statusLabel: 'Signed' },
  { acc: 'PBS-2259', name: 'Peripheral blood smear', stain: 'Giemsa', mag: '100× oil', fill: 'blood', badge: null, time: '2 days', status: 'green', statusLabel: 'Signed' },
  { acc: 'HPE-1178', name: 'Ovarian mass', stain: 'H&E', mag: '10×', fill: 'tissue', badge: null, time: '2 days', status: 'green', statusLabel: 'Signed' },
];

export default function RepositoryView({ onNavigate }) {
  return (
    <div className="pad">
      <div className="repo-head">
        <div className="search">
          <svg viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4-4" />
          </svg>
          <input placeholder="Search accession, patient, stain, tissue…" />
        </div>
        <div className="seg">
          <button className="on">All</button>
          <button>Blood</button>
          <button>Tissue</button>
          <button>Cytology</button>
        </div>
        <button className="filter-btn">
          <svg viewBox="0 0 24 24">
            <path d="M3 5h18M6 12h12M10 19h4" />
          </svg>
          Filter
        </button>
      </div>

      <div className="repo-grid">
        {SLIDES.map((s) => (
          <div className="slide-card" key={s.acc} onClick={() => onNavigate('viewer')}>
            <div className="sc-thumb">
              <div className={s.fill + '-fill'}></div>
              {s.badge && <span className={'badge ' + s.badgeType}>{s.badge}</span>}
            </div>
            <div className="sc-body">
              <div className="sc-acc">{s.acc}</div>
              <div className="sc-name">{s.name}</div>
              <div className="sc-tags">
                <span className="tag stain">{s.stain}</span>
                <span className="tag">{s.mag}</span>
              </div>
              <div className="sc-foot">
                <span>{s.time}</span>
                <span className="st">
                  <span className={'dot' + (s.status === 'amber' ? ' amber' : '')}></span>
                  {s.statusLabel}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
