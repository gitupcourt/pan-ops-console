import clsx from "clsx";
import { NavLink, Outlet } from "react-router-dom";

/**
 * Settings shell at `/settings` (admin-only).
 *
 * Consolidates the app's configuration surfaces under one top-nav tab
 * with a sub-nav: Polling, Pre/post-check sets, and the auth admin
 * (Users / Providers). These all surface infrequently and are
 * config-rather-than-operate, so they cluster naturally behind one tab
 * instead of cluttering the top nav.
 *
 * Nested routes render in the <Outlet />. The old top-level routes
 * (/authentication, /users, /providers, /upgrade/precheck-sets)
 * redirect here so existing bookmarks keep working.
 */
export default function SettingsLayout() {
  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-zinc-100">Settings</h2>
      <nav className="flex items-center gap-1 text-sm border-b border-zinc-800 flex-wrap">
        <SubTab to="/settings/polling">Polling</SubTab>
        <SubTab to="/settings/precheck-sets">Pre/post-check sets</SubTab>
        <SubTab to="/settings/users">Users</SubTab>
        <SubTab to="/settings/providers">Providers</SubTab>
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
