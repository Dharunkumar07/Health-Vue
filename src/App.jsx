import { useState } from 'react';
import './App.css';
import BootOverlay from './components/BootOverlay';
import NavRail from './components/NavRail';
import StatusBar from './components/StatusBar';
import HomeView from './views/HomeView';
import RepositoryView from './views/RepositoryView';
import ViewerView from './views/ViewerView';
import LiveView from './views/LiveView';
import ReportView from './views/ReportView';
import ConsoleView from './views/ConsoleView';
import DebugView from './views/DebugView';

const TITLES = {
  home: 'Home',
  live: 'Live Mode',
  repository: 'Slide Repository',
  viewer: 'Slide Viewer',
  report: 'Report Editor',
  console: 'Developer Console',
  debug: 'Debug',
};
const CRUMBS = {
  home: 'Bench 01 · Dr. K. Bairavi',
  live: 'GRBL · X/Y/Z active',
  repository: '8 slides · local',
  viewer: 'PBS-2261 · Giemsa',
  report: 'PBS-2261 · unsigned',
  console: 'motion FW · field debug',
  debug: 'GRBL · raw axis test',
};
const NAV_GROUP = { viewer: 'repository', report: 'report' };

function App() {
  const [booted, setBooted] = useState(false);
  const [view, setView] = useState('home');
  const [cloudOn, setCloudOn] = useState(false);

  const navGroup = NAV_GROUP[view] || view;

  return (
    <div className="device">
      <div className="screen">
        {!booted && <BootOverlay onDismiss={() => setBooted(true)} />}

        <NavRail activeGroup={navGroup} onNavigate={setView} />

        <div className="main">
          <StatusBar
            title={TITLES[view] || view}
            crumb={CRUMBS[view] || ''}
            cloudOn={cloudOn}
            onToggleCloud={() => setCloudOn((v) => !v)}
          />

          <div className="body">
            <div className={'view' + (view === 'home' ? ' active' : '')} id="home">
              <HomeView onNavigate={setView} />
            </div>
            <div className={'view' + (view === 'repository' ? ' active' : '')} id="repository">
              <RepositoryView onNavigate={setView} />
            </div>
            <div className={'view' + (view === 'viewer' ? ' active' : '')} id="viewer">
              <ViewerView onNavigate={setView} />
            </div>
            <div className={'view' + (view === 'live' ? ' active' : '')} id="live">
              <LiveView onNavigate={setView} />
            </div>
            <div className={'view' + (view === 'report' ? ' active' : '')} id="report">
              <ReportView cloudOn={cloudOn} />
            </div>
            <div className={'view' + (view === 'console' ? ' active' : '')} id="console">
              <ConsoleView active={view === 'console'} />
            </div>
            <div className={'view' + (view === 'debug' ? ' active' : '')} id="debug">
              <DebugView />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
