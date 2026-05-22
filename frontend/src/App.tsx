import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import clsx from "clsx";

import { useAuth } from "./auth";
import Bootstrap from "./pages/Bootstrap";
import Dashboard from "./pages/Dashboard";
import Inventory from "./pages/Inventory";
import Login from "./pages/Login";

export default function App() {
  const { user, bootstrap, isBootstrapLoading, isLoading } = useAuth();

  // Initial paint — wait for bootstrap-status before deciding which screen
  // to show. Without this gate, the user sees the login flash before
  // getting redirected to the signup page on a fresh install.
  if (isBootstrapLoading) {
    return <FullPageStatus text="Loading…" />;
  }

  // Fresh install — no users yet. Show first-user setup.
  if (bootstrap?.needs_bootstrap) {
    return <Bootstrap />;
  }

  // Bootstrap-status loaded, but /auth/me still in flight on first paint.
  if (isLoading) {
    return <FullPageStatus text="Loading…" />;
  }

  // Not signed in.
  if (!user) {
    return <Login />;
  }

  // Signed in — the actual app.
  return (
    <BrowserRouter>
      <div className="min-h-full">
        <header className="border-b border-zinc-800 bg-zinc-950/60 backdrop-blur sticky top-0 z-10">
          <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-6">
            <h1 className="text-base font-semibold text-zinc-100">
              PAN Capacity Analyzer
            </h1>
            <nav className="flex items-center gap-4 text-sm">
              <NavTab to="/">Dashboard</NavTab>
              <NavTab to="/inventory">Inventory</NavTab>
              {user.is_admin && <NavTab to="/users">Users</NavTab>}
            </nav>
            <UserMenu />
          </div>
        </header>

        <main className="max-w-7xl mx-auto px-6 py-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/inventory" element={<Inventory />} />
            {/* Users page lands in slice 1c. */}
            <Route path="/users" element={<UsersPlaceholder />} />
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
      <span className="text-zinc-400">
        {user.username}
        {user.is_admin && <span className="ml-1 text-amber-400">·admin</span>}
      </span>
      <button
        onClick={() => logout()}
        className="text-zinc-500 hover:text-zinc-200"
      >
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

function UsersPlaceholder() {
  return (
    <div className="rounded-lg border border-dashed border-zinc-800 p-8 text-center text-sm text-zinc-400">
      User management arrives in the next slice.
    </div>
  );
}
