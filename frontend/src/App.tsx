import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import clsx from "clsx";

import Dashboard from "./pages/Dashboard";
import Inventory from "./pages/Inventory";

export default function App() {
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
            </nav>
          </div>
        </header>

        <main className="max-w-7xl mx-auto px-6 py-6">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/inventory" element={<Inventory />} />
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
