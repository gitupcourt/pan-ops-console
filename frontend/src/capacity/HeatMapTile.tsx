import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { HeatmapCell } from "../api";

/**
 * One (model, metric) tile on the capacity heat-map.
 *
 * Color derives from `cell.max_pct`. Bands match PA's Capacity
 * Analyzer screenshot: 0–60% safe, 60–80% warning, 80–100% danger.
 * If max_pct is null (no data), tile renders as a neutral
 * placeholder.
 *
 * Hover behavior: small popover anchored to the right of the tile,
 * showing the top devices in this (model, metric) bucket sorted by
 * %. Mirrors the screenshot's "Capacity Used on PA-220 Devices"
 * list with the mini bars.
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
        onClick={() =>
          navigate(
            `/capacity/table?model=${encodeURIComponent(cell.model)}&metric=${encodeURIComponent(cell.metric)}`,
          )
        }
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
        </div>
      </div>

      <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
        Capacity Used on {cell.model} Devices
      </div>
      <div className="space-y-1">
        {cell.top_devices.map((d) => (
          <div key={d.device_id} className="flex items-center gap-2">
            <div className="text-blue-400 w-24 truncate text-[11px]">
              {d.device_name}
            </div>
            <div className="text-zinc-300 tabular-nums text-[11px] w-12 text-right">
              {fmtCount(d.current)}
            </div>
            <div className="text-zinc-400 tabular-nums text-[11px] w-12 text-right">
              {formatPct(d.pct)}
            </div>
            <div className="flex-1 h-1 bg-zinc-800 rounded overflow-hidden">
              <div
                className="h-full rounded bg-rose-500"
                style={{ width: `${Math.min(100, Math.max(0, d.pct ?? 0))}%` }}
              />
            </div>
          </div>
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
