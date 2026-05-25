import { Routes, Route, Navigate, Link, useLocation, useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { api, getToken, setToken } from "./lib/api";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Devices from "./pages/Devices";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import Settings from "./pages/Settings";

type Me = {
  id: number;
  username: string;
  is_admin: boolean;
};

function NavLink({ to, children }: { to: string; children: React.ReactNode }) {
  const { pathname } = useLocation();
  const active = pathname === to || pathname.startsWith(to + "/");
  return (
    <Link
      to={to}
      className={`px-3 py-2 rounded-md text-sm ${
        active ? "bg-slate-800 text-white" : "text-slate-300 hover:bg-slate-800/60"
      }`}
    >
      {children}
    </Link>
  );
}

function Shell({ me, children }: { me: Me; children: React.ReactNode }) {
  const navigate = useNavigate();
  const logout = () => {
    setToken(null);
    navigate("/login", { replace: true });
  };
  return (
    <div className="flex h-full flex-col">
      <header className="border-b border-slate-800 bg-slate-900">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3">
          <div className="flex items-center gap-6">
            <span className="font-semibold text-white">pan-fw-upgrader</span>
            <nav className="flex gap-1">
              <NavLink to="/dashboard">Dashboard</NavLink>
              <NavLink to="/devices">Devices</NavLink>
              <NavLink to="/jobs">Jobs</NavLink>
              <NavLink to="/settings">Settings</NavLink>
            </nav>
          </div>
          <div className="flex items-center gap-3 text-sm text-slate-300">
            <span>{me.username}</span>
            <button onClick={logout} className="rounded-md border border-slate-700 px-2 py-1 hover:bg-slate-800">
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6">{children}</main>
    </div>
  );
}

export default function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = getToken();
    if (!token) {
      setLoading(false);
      return;
    }
    api<Me>("/api/auth/me")
      .then(setMe)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="p-8 text-slate-400">Loading…</div>;

  if (!me) {
    return (
      <Routes>
        <Route path="/login" element={<Login onSignIn={setMe} />} />
        <Route path="*" element={<Navigate to="/login" replace />} />
      </Routes>
    );
  }

  return (
    <Shell me={me}>
      <Routes>
        <Route path="/" element={<Navigate to="/dashboard" replace />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/devices" element={<Devices />} />
        <Route path="/jobs" element={<Jobs />} />
        <Route path="/jobs/:id" element={<JobDetail />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/dashboard" replace />} />
      </Routes>
    </Shell>
  );
}
