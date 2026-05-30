import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, Device } from "../../api";

/**
 * Global device search in the top chrome. Partial, case-insensitive
 * match on name / IP / hostname / serial; selecting a result jumps to
 * that device's Capacity page (/capacity/device/:id).
 *
 * Data source is the same `["devices"]` query Inventory uses, so it's
 * already cached — no new endpoint, no extra fetch in practice. Fleet
 * sizes are small enough that client-side filtering is fine; if the
 * fleet ever dwarfs the cache, swap the filter for a server search.
 *
 * Keyboard: ↓/↑ move the highlight, Enter selects, Esc closes. Click
 * outside closes. Results cap at 8 with a "+N more — refine" footer so
 * the dropdown never runs off-screen.
 */
const MAX_RESULTS = 8;

export function GlobalSearch() {
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [highlight, setHighlight] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);

  // Devices are only fetched once the operator focuses the box — no
  // point loading the list on every page just to power a search they
  // may not use. Shares the Inventory cache key once loaded.
  const [activated, setActivated] = useState(false);
  const devsQ = useQuery({
    queryKey: ["devices"],
    queryFn: api.listDevices,
    enabled: activated,
    staleTime: 60_000,
  });

  const matches = useMemo(() => {
    const needle = q.trim().toLowerCase();
    if (!needle) return [] as Device[];
    const devs = devsQ.data ?? [];
    const scored = devs.filter((d) => {
      const hay = [d.name, d.ip_address, d.hostname, d.serial]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
    // Prefer name-prefix matches, then the rest — most operators type
    // the start of a device name.
    scored.sort((a, b) => {
      const ap = a.name.toLowerCase().startsWith(needle) ? 0 : 1;
      const bp = b.name.toLowerCase().startsWith(needle) ? 0 : 1;
      if (ap !== bp) return ap - bp;
      return a.name.localeCompare(b.name);
    });
    return scored;
  }, [q, devsQ.data]);

  const shown = matches.slice(0, MAX_RESULTS);

  // Reset highlight whenever the result set changes.
  useEffect(() => setHighlight(0), [q]);

  // Click-outside closes the dropdown.
  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const go = (d: Device) => {
    setOpen(false);
    setQ("");
    navigate(`/capacity/device/${d.id}`);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (!shown.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, shown.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick = shown[highlight];
      if (pick) go(pick);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <input
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => {
          setActivated(true);
          if (q) setOpen(true);
        }}
        onKeyDown={onKeyDown}
        placeholder="Search devices…"
        aria-label="Search devices by name, IP, or serial"
        className="w-48 bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-xs text-zinc-100 placeholder:text-zinc-500 focus:outline-none focus:border-blue-500 focus:w-64 transition-[width]"
      />
      {open && q.trim() && (
        <div className="absolute right-0 mt-1 w-80 max-w-[90vw] rounded border border-zinc-700 bg-zinc-950 shadow-xl z-20 overflow-hidden">
          {devsQ.isLoading ? (
            <div className="px-3 py-2 text-xs text-zinc-500">Loading devices…</div>
          ) : shown.length === 0 ? (
            <div className="px-3 py-2 text-xs text-zinc-500">
              No devices match “{q.trim()}”.
            </div>
          ) : (
            <ul className="max-h-80 overflow-y-auto text-sm">
              {shown.map((d, i) => (
                <li key={d.id}>
                  <button
                    type="button"
                    onMouseEnter={() => setHighlight(i)}
                    onClick={() => go(d)}
                    className={`w-full text-left px-3 py-1.5 flex items-center justify-between gap-3 ${
                      i === highlight ? "bg-blue-950/50" : "hover:bg-zinc-900"
                    }`}
                  >
                    <span className="text-zinc-100 truncate">{d.name}</span>
                    <span className="text-[10px] text-zinc-500 font-mono truncate">
                      {d.ip_address ?? d.hostname}
                      {d.serial ? ` · ${d.serial}` : ""}
                    </span>
                  </button>
                </li>
              ))}
              {matches.length > MAX_RESULTS && (
                <li className="px-3 py-1.5 text-[10px] text-zinc-600 border-t border-zinc-800">
                  +{matches.length - MAX_RESULTS} more — refine your search
                </li>
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
