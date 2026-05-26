import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import clsx from "clsx";

import AlertsPage from "./alerts/AlertsPage";
import CapacityAnalyzer from "./capacity/CapacityAnalyzer";
import CapacityTable from "./capacity/CapacityTable";
import CapacityTrend from "./capacity/CapacityTrend";
import Dashboard from "./capacity/Dashboard";
import { useAuth } from "./core/auth/AuthContext";
import Bootstrap from "./core/auth/Bootstrap";
import Login from "./core/auth/Login";
import Profile from "./core/auth/Profile";
import Providers from "./core/auth/Providers";
import Users from "./core/auth/Users";
import Inventory from "./core/devices/Inventory";
import HomeDashboard from "./HomeDashboard";
import JobDetail from "./upgrade/JobDetail";
import UpgradeJobs from "./upgrade/UpgradeJobs";

export default function App() {
  const { user, bootstrap, isBootstrapLoading, isLoading } = useAuth();

  if (isBootstrapLoading) {
    return <FullPageStatus text="Loading…" />;
  }
  if (bootstrap?.needs_bootstrap) {
    return <Bootstrap />;
  }
  if (isLoading) {
    return <FullPageStatus text="Loading…" />;
  }
  if (!user) {
    return <Login />;
  }

  return (
    <BrowserRouter>
      <div className="min-h-full">
        <header className="border-b border-zinc-800 bg-zinc-950/60 backdrop-blur sticky top-0 z-10">
          <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-6">
            <h1 className="text-base font-semibold text-zinc-100">
              PAN NGFW Ops Console
            </h1>
            <nav className="flex items-center gap-4 text-sm">
              <NavTab to="/">Dashboard</NavTab>
              <NavTab to="/capacity">Capacity</NavTab>
              <NavTab to="/alerts">Alerts</NavTab>
              <NavTab to="/inventory">Inventory</NavTab>
              <NavTab to="/upgrade">Upgrade</NavTab>
              {user.is_admin && <NavTab to="/users">Users</NavTab>}
              {user.is_admin && <NavTab to="/providers">Providers</NavTab>}
            </nav>
            <UserMenu />
          </div>
        </header>

        <main className="max-w-7xl mx-auto px-6 py-6">
          <Routes>
            <Route path="/" element={<HomeDashboard />} />
            {/* Capacity Analyzer drill chain: heat-map → table → trend.
                The previous /capacity (per-device chart grid) lives on
                at /capacity/device — the table view deep-links there
                from the device-name column. */}
            <Route path="/capacity" element={<CapacityAnalyzer />} />
            {/* Two routes for the per-device chart grid: bare
                `/capacity/device` shows the dropdown defaulted to
                the alphabetic-first device (entry point from the
                top nav), and `/capacity/device/:deviceId` deep-links
                directly to a specific device (entry point from the
                Capacity Table host column). Dashboard reads the
                param via useParams and uses it as the initial /
                URL-synced selection. */}
            <Route path="/capacity/device" element={<Dashboard />} />
            <Route path="/capacity/device/:deviceId" element={<Dashboard />} />
            <Route path="/capacity/table" element={<CapacityTable />} />
            <Route
              path="/capacity/trend/:deviceId/:metric"
              element={<CapacityTrend />}
            />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/inventory" element={<Inventory />} />
            <Route path="/upgrade" element={<UpgradeJobs />} />
            <Route path="/upgrade/jobs/:jobId" element={<JobDetail />} />
            <Route path="/users" element={user.is_admin ? <Users /> : <NotAuthorized />} />
            <Route path="/providers" element={user.is_admin ? <Providers /> : <NotAuthorized />} />
            <Route path="/profile" element={<Profile />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

function NavTab({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        clsx(
          "px-2 py-1 rounded transition-colors",
          isActive ? "text-zinc-100 bg-zinc-800" : "text-zinc-400 hover:text-zinc-100",
        )
      }
    >
      {children}
    </NavLink>
  );
}

function UserMenu() {
  const { user, logout } = useAuth();
  if (!user) return null;
  return (
    <div className="ml-auto flex items-center gap-3 text-xs">
      <NavLink to="/profile" className="text-zinc-400 hover:text-zinc-100">
        {user.username}
        {user.is_admin && <span className="ml-1 text-amber-400">·admin</span>}
      </NavLink>
      <button onClick={() => logout()} className="text-zinc-500 hover:text-zinc-200">
        Sign out
      </button>
    </div>
  );
}

function FullPageStatus({ text }: { text: string }) {
  return (
    <div className="min-h-screen flex items-center justify-center text-sm text-zinc-500">
      {text}
    </div>
  );
}

function NotAuthorized() {
  return (
    <div className="rounded-lg border border-dashed border-zinc-800 p-8 text-center text-sm text-zinc-400">
      Admins only.
    </div>
  );
}
