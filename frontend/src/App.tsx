import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router-dom";
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
import PollingSettings from "./core/settings/PollingSettings";
import SettingsLayout from "./core/settings/SettingsLayout";
import { GlobalSearch } from "./core/devices/GlobalSearch";
import Inventory from "./core/devices/Inventory";
import HomeDashboard from "./HomeDashboard";
import JobDetail from "./upgrade/JobDetail";
import PrecheckSets from "./upgrade/PrecheckSets";
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
          <div className="max-w-screen-2xl mx-auto px-6 py-3 flex items-center gap-6">
            <h1 className="text-base font-semibold text-zinc-100">
              PAN NGFW Ops Console
            </h1>
            {/* Top-level nav order is operator-defined: by-frequency-of-use
                first (Dashboard / Alerts / Capacity), then operational
                surfaces (Jobs / Inventory), then admin (Authentication).
                "Jobs" replaces the older "Upgrade" label — the module
                will host job types beyond upgrade in the future
                (snapshots, bulk precheck, etc.) so the more general
                label fits. */}
            <nav className="flex items-center gap-4 text-sm">
              <NavTab to="/">Dashboard</NavTab>
              <NavTab to="/alerts">Alerts</NavTab>
              <NavTab to="/capacity">Capacity</NavTab>
              <NavTab to="/upgrade">Jobs</NavTab>
              <NavTab to="/inventory">Inventory</NavTab>
              {user.is_admin && <NavTab to="/settings">Settings</NavTab>}
            </nav>
            <div className="ml-auto flex items-center gap-4">
              <GlobalSearch />
              <UserMenu />
            </div>
          </div>
        </header>

        <main className="max-w-screen-2xl mx-auto px-6 py-6">
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
            {/* Job routes still live under /upgrade for backward-compat
                — the nav label is the only thing that changed. URL stays
                stable so bookmarks and the platform-sibling references
                don't churn. */}
            <Route path="/upgrade" element={<UpgradeJobs />} />
            <Route path="/upgrade/jobs/:jobId" element={<JobDetail />} />
            {/* Settings shell (admin-only): a sub-nav over Polling,
                Pre/post-check sets, and the auth admin (Users /
                Providers). The active sub-route renders in an <Outlet />.
                Old top-level URLs (/authentication, /users, /providers,
                /upgrade/precheck-sets) redirect into here so existing
                bookmarks keep working. */}
            <Route
              path="/settings"
              element={user.is_admin ? <SettingsLayout /> : <NotAuthorized />}
            >
              <Route index element={<Navigate to="polling" replace />} />
              <Route path="polling" element={<PollingSettings />} />
              <Route path="precheck-sets" element={<PrecheckSets />} />
              <Route path="users" element={<Users />} />
              <Route path="providers" element={<Providers />} />
            </Route>
            {/* Back-compat redirects for the pre-Settings URLs. */}
            <Route
              path="/upgrade/precheck-sets"
              element={<Navigate to="/settings/precheck-sets" replace />}
            />
            <Route
              path="/authentication"
              element={<Navigate to="/settings/users" replace />}
            />
            <Route
              path="/authentication/users"
              element={<Navigate to="/settings/users" replace />}
            />
            <Route
              path="/authentication/providers"
              element={<Navigate to="/settings/providers" replace />}
            />
            <Route path="/users" element={<Navigate to="/settings/users" replace />} />
            <Route
              path="/providers"
              element={<Navigate to="/settings/providers" replace />}
            />
            <Route path="/profile" element={<Profile />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  );
}

function NavTab({ to, children }: { to: string; children: React.ReactNode }) {
  // `end` prop matters for the root "/" tab — without it, every nested
  // route would highlight the Dashboard tab. For top-level non-root
  // tabs (e.g. /authentication), we WANT prefix matching so a child
  // route like /authentication/users keeps the Authentication tab lit.
  const exact = to === "/";
  return (
    <NavLink
      to={to}
      end={exact}
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
