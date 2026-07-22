const NAV_ITEMS = [
  {
    view: 'home',
    label: 'Home',
    icon: (
      <>
        <path d="M3 11l9-8 9 8" />
        <path d="M5 10v10h14V10" />
      </>
    ),
  },
  {
    view: 'live',
    label: 'Live',
    icon: (
      <>
        <circle cx="12" cy="12" r="3.2" />
        <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
      </>
    ),
  },
  {
    view: 'repository',
    label: 'Slides',
    icon: (
      <>
        <rect x="3" y="4" width="18" height="5" rx="1.5" />
        <rect x="3" y="11" width="18" height="5" rx="1.5" />
        <rect x="3" y="18" width="10" height="3" rx="1.5" />
      </>
    ),
  },
  {
    view: 'report',
    label: 'Reports',
    icon: (
      <>
        <path d="M6 2h9l5 5v15H6z" />
        <path d="M15 2v5h5M9 13h7M9 17h7" />
      </>
    ),
  },
];

export default function NavRail({ activeGroup, onNavigate }) {
  return (
    <div className="rail">
      <div className="brand">
        <img src="/logo.png" alt="HealthVue" width="75" height="75" style={{ objectFit: 'contain' }} />
      </div>
      {NAV_ITEMS.map((item) => (
        <button
          key={item.view}
          className={'navbtn' + (activeGroup === item.view ? ' active' : '')}
          onClick={() => onNavigate(item.view)}
        >
          <svg viewBox="0 0 24 24">{item.icon}</svg>
          {item.label}
        </button>
      ))}
      <div className="spacer"></div>
      <button
        className={'navbtn dev' + (activeGroup === 'debug' ? ' active' : '')}
        onClick={() => onNavigate('debug')}
      >
        <svg viewBox="0 0 24 24">
          <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
          <circle cx="12" cy="12" r="4" />
        </svg>
        Debug
      </button>
      <button
        className={'navbtn dev' + (activeGroup === 'console' ? ' active' : '')}
        onClick={() => onNavigate('console')}
      >
        <svg viewBox="0 0 24 24">
          <path d="M4 5h16v14H4z" />
          <path d="M8 10l3 2-3 2M13 14h4" />
        </svg>
        Console
      </button>
    </div>
  );
}
