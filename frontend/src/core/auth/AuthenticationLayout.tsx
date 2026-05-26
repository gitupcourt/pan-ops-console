import clsx from "clsx";
import { NavLink, Outlet } from "react-router-dom";

/**
 * Authentication shell at `/authentication`.
 *
 * Hosts the Users + Providers admin pages (and future auth-related
 * pages — 2FA settings, session admin, audit log, etc.) under a
 * single nav tab with a sub-nav. Operators wanted to consolidate the
 * top-level chrome; auth concerns naturally cluster together and
 * surface less often than per-module pages, so they belong behind
 * one tab.
 *
 * Routes nested under this layout render in the <Outlet />. Adding a
 * new auth surface = add a sub-tab below + a nested <Route> in
 * App.tsx.
 */
export default function AuthenticationLayout() {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-zinc-100">Authentication</h2>
      </div>
      <nav className="flex items-center gap-1 text-sm border-b border-zinc-800">
        <SubTab to="/authentication/users">Users</SubTab>
        <SubTab to="/authentication/providers">Providers</SubTab>
      </nav>
      <Outlet />
    </div>
  );
}

function SubTab({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      end
      className={({ isActive }) =>
        clsx(
          "px-3 py-2 -mb-px border-b-2 transition-colors text-sm",
          isActive
            ? "border-blue-400 text-zinc-100"
            : "border-transparent text-zinc-400 hover:text-zinc-100",
        )
      }
    >
      {children}
    </NavLink>
  );
}
