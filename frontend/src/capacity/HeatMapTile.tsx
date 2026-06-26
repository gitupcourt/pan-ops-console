import { useState } from "react";
import { useNavigate, NavLink, useSearchParams } from "react-router-dom";

import { HeatmapCell } from "../api";
import { relTime } from "../lib/relTime";

/**
 * One (model, metric) tile on the capacity heat-map.
 *
 * Color derives from `cell.max_pct`. Bands: 0–60% safe (green),
 * 60–80% warning (amber), 80–100% danger (red). If max_pct is null
 * (no data), tile renders as a neutral placeholder.
 *
 * Hover behavior: small popover anchored to the right of the tile,
 * showing the top devices in this (model, metric) bucket sorted by
 * %, with mini horizontal bars per device.
 *
 * Click behavior: routes to `/capacity/table?model=X&metric=Y` so
 * the operator can drill into the per-device view (phase 10 fills
 * the destination; phase 9 ships the navigation).
 */
export function HeatMapTile({
  cell,
  colorScheme,
}: {
  cell: HeatmapCell | undefined;
  colorScheme: "multi" | "mono";
}) {
  const navigate = useNavigate();
  // Read current URL params so the tile-click navigate to the table
  // view preserves any filters the operator already set on the heat
  // map (device_group, template_stack). The tile contributes model +
  // metric on top of those.
  const [params] = useSearchParams();
  const [hovered, setHovered] = useState(false);

  if (!cell) {
    // No data for this (model, metric). Render a faint placeholder so the
    // grid still aligns but doesn't claim a value.
    return (
      <div
        className="w-12 h-10 rounded bg-zinc-900/40 border border-zinc-800/40"
        title="No data"
      />
    );
  }

  const pct = cell.max_pct ?? 0;
  const bg = bgForPct(pct, colorScheme, cell.max_pct === null);

  return (
    <div
      className="relative"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={() => {
          const next = new URLSearchParams(params);
          next.set("model", cell.model);
          next.set("metric", cell.metric);
          navigate(`/capacity/table?${next.toString()}`);
        }}
        className={`w-12 h-10 rounded ${bg} hover:ring-2 hover:ring-zinc-300/40 transition-shadow cursor-pointer`}
        title={`${cell.metric_description} on ${cell.model}: ${formatPct(cell.max_pct)} max across ${cell.device_count} device(s)`}
        aria-label={`${cell.metric} on ${cell.model}`}
      />
      {hovered && <HoverPopover cell={cell} />}
    </div>
  );
}

function HoverPopover({ cell }: { cell: HeatmapCell }) {
  // Popover sits flush to the tile's right edge (left-full, no margin)
  // so cursor-traversal from tile to popover doesn't leave the parent's
  // hover-tracking region. The parent's onMouseLeave is what closes it.
  // A small pl-2 on the inner content gives visual breathing room without
  // creating a hover gap.
  return (
    <div
      className="absolute z-20 left-full top-0 w-72 pl-2"
    >
      <div className="rounded-lg border border-zinc-700 bg-zinc-950 shadow-lg p-3 text-xs">
      <div className="space-y-0.5 mb-2">
        <div className="text-zinc-100">
          <span className="text-zinc-500">Metric:</span>{" "}
          <span className="font-medium">{cell.metric_description}</span>
        </div>
        <div className="text-zinc-300">
          <span className="text-zinc-500">Device Model:</span> {cell.model}
        </div>
        <div className="text-zinc-300">
          <span className="text-zinc-500">Devices:</span> {cell.device_count}
          {cell.stale_count > 0 && (
            <span className="text-amber-500/80">
              {" "}
              ({cell.stale_count} stale, not counted in color)
            </span>
          )}
        </div>
      </div>

      <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
        Capacity Used on {cell.model} Devices
      </div>
      <div className="space-y-1">
        {cell.top_devices.map((d) => (
          // Device name links to the trend chart for THIS exact
          // (device, metric) pair — the hover popover is already in the
          // context of one metric, so the click target most useful to
          // the operator is "show me how this metric is trending on
          // this device specifically." The trend route is a phase-11
          // placeholder for now but the navigation is correct, so when
          // phase 11 lands the link starts working without UI changes.
          // Stale devices (stopped reporting) are dimmed and show "last seen
          // …" in place of the usage bar — the % they'd draw is a frozen,
          // last-known value we don't want masquerading as live. Still a
          // link: the trend chart shows exactly where the data went quiet.
          <NavLink
            key={d.device_id}
            to={`/capacity/trend/${d.device_id}/${encodeURIComponent(cell.metric)}`}
            className="flex items-center gap-2 px-1 -mx-1 rounded hover:bg-zinc-800/60"
          >
            <div
              className={`w-24 truncate text-[11px] ${
                d.stale ? "text-zinc-500" : "text-blue-400"
              }`}
            >
              {d.device_name}
            </div>
            <div
              className={`tabular-nums text-[11px] w-12 text-right ${
                d.stale ? "text-zinc-600" : "text-zinc-300"
              }`}
            >
              {fmtCount(d.current)}
            </div>
            <div
              className={`tabular-nums text-[11px] w-12 text-right ${
                d.stale ? "text-zinc-600" : "text-zinc-400"
              }`}
            >
              {formatPct(d.pct)}
            </div>
            {d.stale ? (
              <div className="flex-1 text-[10px] text-zinc-500 italic text-right truncate">
                last seen {relTime(d.last_sample_at)}
              </div>
            ) : (
              <div className="flex-1 h-1 bg-zinc-800 rounded overflow-hidden">
                <div
                  className="h-full rounded bg-rose-500"
                  style={{ width: `${Math.min(100, Math.max(0, d.pct ?? 0))}%` }}
                />
              </div>
            )}
          </NavLink>
        ))}
      </div>

      {cell.device_count > cell.top_devices.length && (
        <div className="mt-2 text-[11px] text-blue-400">
          … Show {cell.device_count - cell.top_devices.length} More Devices
        </div>
      )}
      </div>
    </div>
  );
}

function bgForPct(
  pct: number,
  scheme: "multi" | "mono",
  isNull: boolean,
): string {
  if (isNull) return "bg-zinc-800/40";
  if (scheme === "mono") {
    // Monochrome view: shades of pink/red. Lighter = lower %, darker = higher.
    if (pct < 60) return "bg-pink-950/60";
    if (pct < 80) return "bg-pink-800/60";
    return "bg-pink-600/70";
  }
  // Multicolor (default): green / amber / red.
  if (pct < 60) return "bg-emerald-700/60";
  if (pct < 80) return "bg-amber-600/60";
  return "bg-rose-700/70";
}

function formatPct(pct: number | null): string {
  if (pct == null) return "—";
  return `${pct.toFixed(pct < 10 ? 1 : 0)}%`;
}

function fmtCount(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return n.toLocaleString();
}
