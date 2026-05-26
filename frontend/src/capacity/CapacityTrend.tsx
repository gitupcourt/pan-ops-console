import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Area,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  NavLink,
  useNavigate,
  useParams,
} from "react-router-dom";

import { api, CapacityTableRow } from "../api";
import { Button, Card, CardHeader, Select } from "../core/ui/ui";

/**
 * Capacity trend view at `/capacity/trend/{deviceId}/{metric}`.
 *
 * Phase 11. The deepest drill: heat-map (9) → table (10) → trend
 * (this PR).
 *
 * Renders the time series for a single (device, metric) pair plus an
 * optional linear-regression projection of when the value will hit
 * the device's max. The PA screenshot's banner ("Forecasted date to
 * hit maximum capacity") + the dashed vertical line at that date
 * are both surfaced here.
 *
 * Below the chart: a small device-picker table listing every device
 * of the same model with the same metric. Click any row to swap which
 * device's chart is displayed. (PA's UI has radio buttons on each row
 * but they're redundant with the row click, so we drop them.)
 */
export default function CapacityTrend() {
  const { deviceId: deviceIdStr, metric } = useParams<{
    deviceId: string;
    metric: string;
  }>();
  const navigate = useNavigate();
  const deviceId = deviceIdStr ? Number(deviceIdStr) : NaN;

  const [days, setDays] = useState(30);

  const trendQ = useQuery({
    queryKey: ["capacity-trend", deviceId, metric, days],
    queryFn: () => api.getCapacityTrend(deviceId, metric ?? "", days),
    enabled: !Number.isNaN(deviceId) && !!metric,
    refetchInterval: 30_000,
  });

  if (Number.isNaN(deviceId) || !metric) {
    return (
      <div className="text-sm text-rose-400">
        Invalid URL — expected /capacity/trend/&lt;deviceId&gt;/&lt;metric&gt;.
      </div>
    );
  }

  const trend = trendQ.data;
  // Fetch peer devices (same model, same metric) for the picker table.
  // Depends on `trend` being loaded first so we know the model.
  const peersQ = useQuery({
    queryKey: ["capacity-trend-peers", trend?.model, metric],
    queryFn: () =>
      api.getCapacityTable({
        model: trend?.model ?? null,
        metric,
      }),
    enabled: !!trend?.model,
  });

  if (trendQ.isLoading) {
    return <div className="text-sm text-zinc-500">Loading…</div>;
  }
  if (trendQ.error) {
    return (
      <div className="text-sm text-rose-400">
        Failed to load trend: {(trendQ.error as Error).message}
      </div>
    );
  }
  if (!trend) return null;

  return (
    <div className="space-y-4">
      <Breadcrumbs />

      <h2 className="text-xl font-semibold text-zinc-100">
        Capacity Analyzer
      </h2>

      <Card>
        <div className="px-4 py-3 flex flex-wrap items-center gap-3 text-xs border-b border-zinc-800">
          <label className="flex items-center gap-1.5 text-zinc-400">
            <span>Time:</span>
            <Select
              value={days}
              onChange={(e) => setDays(Number(e.target.value))}
              className="text-xs"
            >
              <option value={7}>Past 7 Days</option>
              <option value={30}>Past 30 Days</option>
              <option value={90}>Past 90 Days</option>
              <option value={180}>Past 180 Days</option>
            </Select>
          </label>
          <Button onClick={() => setDays(30)}>Reset</Button>
        </div>

        <CardHeader
          title={`Metric ${trend.metric_description} on device ${trend.device_name}${trend.model ? ` [${trend.model}]` : ""}`}
        />

        <TrendChart trend={trend} />

        {trend.prediction.predicted_date && (
          <ForecastBanner trend={trend} />
        )}
      </Card>

      {peersQ.data && peersQ.data.rows.length > 1 && (
        <Card>
          <CardHeader
            title={`${trend.metric_description} on all ${trend.model ?? "devices"}`}
            description="Pick a different device to chart its history for this metric."
          />
          <PeerPicker
            rows={peersQ.data.rows}
            currentDeviceId={deviceId}
            onPick={(newId) =>
              navigate(
                `/capacity/trend/${newId}/${encodeURIComponent(metric)}`,
              )
            }
          />
        </Card>
      )}
    </div>
  );
}

function Breadcrumbs() {
  return (
    <div className="text-xs text-zinc-500">
      <NavLink to="/capacity" className="text-blue-400 hover:text-blue-300">
        Capacity Analyzer Heat-map
      </NavLink>
      {" › "}
      <NavLink
        to="/capacity/table"
        className="text-blue-400 hover:text-blue-300"
      >
        Capacity Analyzer Table
      </NavLink>
      {" › "}
      <span>Capacity Analyzer Trend</span>
    </div>
  );
}

function TrendChart({
  trend,
}: {
  trend: NonNullable<ReturnType<typeof api.getCapacityTrend>> extends Promise<infer T>
    ? T
    : never;
}) {
  // Recharts wants numeric x-axis values, so convert ts → ms.
  const data = trend.samples.map((s) => ({
    ts: new Date(s.ts).getTime(),
    current: s.current,
    pct: s.pct,
  }));

  // Build the projection line for rendering. If we have a predicted
  // hit date AND the slope is positive, extend a line from the latest
  // sample to (predicted_date, max). Otherwise no projection band.
  const projection: { ts: number; projected: number }[] = [];
  const max = trend.max_value;
  if (
    trend.prediction.method === "linear_regression" &&
    trend.prediction.predicted_date &&
    max != null &&
    data.length > 0
  ) {
    const last = data[data.length - 1];
    const predTs = new Date(trend.prediction.predicted_date).getTime();
    projection.push({ ts: last.ts, projected: last.current });
    projection.push({ ts: predTs, projected: max });
  }

  // The chart needs a unified data series — merge actual + projection
  // by timestamp. Actual samples get `current`; projection gets
  // `projected`; both can be plotted on the same axis.
  const merged = mergeForChart(data, projection);

  const predTs = trend.prediction.predicted_date
    ? new Date(trend.prediction.predicted_date).getTime()
    : null;

  return (
    <div className="px-4 py-6">
      <div className="h-72">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={merged} margin={{ top: 8, right: 20, left: 0, bottom: 8 }}>
            <XAxis
              dataKey="ts"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              tickFormatter={(t) => new Date(t as number).toLocaleDateString()}
              tick={{ fill: "#71717a", fontSize: 10 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={[0, max != null && max > 0 ? Math.max(max, ...data.map((d) => d.current)) : "auto"]}
              tick={{ fill: "#71717a", fontSize: 10 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => fmtCount(v as number)}
              width={48}
            />
            {max != null && max > 0 && (
              <ReferenceLine
                y={max}
                stroke="#f87171"
                strokeDasharray="3 3"
                label={{
                  value: `MAX CAPACITY ${fmtCount(max)}`,
                  position: "insideTopLeft",
                  fill: "#f87171",
                  fontSize: 10,
                }}
              />
            )}
            {predTs != null && (
              <ReferenceLine
                x={predTs}
                stroke="#fbbf24"
                strokeDasharray="3 3"
                label={{
                  value: `Forecast: ${new Date(predTs).toLocaleDateString()}`,
                  position: "insideTopRight",
                  fill: "#fbbf24",
                  fontSize: 10,
                }}
              />
            )}

            {/* Projection band — area shaded from current to max along
                the projection line. Renders behind the actual line. */}
            {projection.length > 0 && (
              <Area
                type="linear"
                dataKey="projected"
                stroke="#fbbf24"
                strokeDasharray="4 4"
                strokeWidth={1.5}
                fill="#fbbf24"
                fillOpacity={0.08}
                connectNulls
                isAnimationActive={false}
              />
            )}

            <Line
              type="monotone"
              dataKey="current"
              stroke="#60a5fa"
              strokeWidth={2}
              dot={data.length < 30 ? { r: 3, fill: "#60a5fa" } : false}
              activeDot={{ r: 5, fill: "#93c5fd", stroke: "#0b0e14", strokeWidth: 2 }}
              isAnimationActive={false}
            />

            <Tooltip
              contentStyle={{
                background: "#18181b",
                border: "1px solid #3f3f46",
                fontSize: 12,
              }}
              labelFormatter={(t) =>
                new Date(t as number).toLocaleString()
              }
              formatter={(value: number, name: string) => {
                if (name === "projected") return [fmtCount(value), "projected"];
                const pct = max && max > 0 ? ((value / max) * 100).toFixed(1) : null;
                return [
                  `${fmtCount(value)}${pct ? ` (${pct}%)` : ""}`,
                  "actual",
                ];
              }}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function ForecastBanner({
  trend,
}: {
  trend: NonNullable<ReturnType<typeof api.getCapacityTrend>> extends Promise<infer T>
    ? T
    : never;
}) {
  const p = trend.prediction;
  if (!p.predicted_date) return null;
  const predDate = new Date(p.predicted_date);
  const days = p.days_left;
  const tone =
    days != null && days < 30
      ? "border-rose-700 bg-rose-900/30 text-rose-200"
      : days != null && days < 90
        ? "border-amber-700 bg-amber-900/30 text-amber-200"
        : "border-blue-700 bg-blue-900/20 text-blue-200";
  return (
    <div className={`mx-4 mb-4 rounded border px-3 py-2 text-xs ${tone}`}>
      <span className="font-semibold">{predDate.toLocaleDateString()}</span>{" "}
      <span className="text-zinc-300">
        (Forecasted date to hit maximum capacity
        {days != null && ` — ${days} day${days === 1 ? "" : "s"} from latest sample`})
      </span>
    </div>
  );
}

function PeerPicker({
  rows,
  currentDeviceId,
  onPick,
}: {
  rows: CapacityTableRow[];
  currentDeviceId: number;
  onPick: (deviceId: number) => void;
}) {
  // Sort peers by pct desc to mirror the screenshot's "most used first"
  // ordering. Stable secondary sort by device_id for deterministic
  // rendering across refetches.
  const sorted = [...rows].sort((a, b) => {
    const ap = a.pct ?? -1;
    const bp = b.pct ?? -1;
    if (bp !== ap) return bp - ap;
    return a.device_id - b.device_id;
  });
  return (
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase text-zinc-500 border-b border-zinc-800">
        <tr>
          <th className="text-left px-4 py-2 font-medium">Host</th>
          <th className="text-left px-4 py-2 font-medium">Amount Used</th>
          <th className="text-left px-4 py-2 font-medium">Unused</th>
          <th className="text-left px-4 py-2 font-medium">SW</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => {
          const unused =
            r.max != null && r.current != null ? r.max - r.current : null;
          const unusedPct =
            r.max != null && r.max > 0 && unused != null
              ? (unused / r.max) * 100
              : null;
          const selected = r.device_id === currentDeviceId;
          return (
            // The whole row is the selection affordance. The selected
            // row gets a subtle highlight + left-border accent so the
            // operator can see at a glance which device the chart above
            // is currently showing.
            <tr
              key={r.device_id}
              className={`border-b border-zinc-800/40 hover:bg-zinc-900/30 cursor-pointer ${
                selected ? "bg-zinc-800/40" : ""
              }`}
              onClick={() => onPick(r.device_id)}
            >
              <td
                className={`px-4 py-2 ${
                  selected
                    ? "border-l-2 border-blue-400 text-blue-300 font-medium"
                    : "text-blue-400"
                }`}
              >
                {r.device_name}
              </td>
              <td className="px-4 py-2 text-xs text-zinc-300">
                {fmtCount(r.current)}{" "}
                <span className="text-zinc-500">
                  ({r.pct == null ? "—" : `${r.pct.toFixed(r.pct < 10 ? 1 : 0)}%`})
                </span>
              </td>
              <td className="px-4 py-2 text-xs text-zinc-400">
                {unused == null
                  ? "—"
                  : `${fmtCount(unused)}${unusedPct != null ? ` (${unusedPct.toFixed(unusedPct < 10 ? 1 : 0)}%)` : ""}`}
              </td>
              <td className="px-4 py-2 text-xs text-zinc-500">
                {r.software_version ?? "—"}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ----- helpers -----

function mergeForChart(
  actual: { ts: number; current: number; pct: number | null }[],
  projection: { ts: number; projected: number }[],
): Array<{
  ts: number;
  current?: number;
  pct?: number | null;
  projected?: number;
}> {
  // Build a sorted union of timestamps and emit one row per ts with
  // whichever fields apply. Recharts handles missing keys correctly
  // (the line just doesn't render at points without data).
  const map = new Map<number, {
    ts: number;
    current?: number;
    pct?: number | null;
    projected?: number;
  }>();
  for (const a of actual) {
    map.set(a.ts, { ts: a.ts, current: a.current, pct: a.pct });
  }
  for (const p of projection) {
    const existing = map.get(p.ts);
    if (existing) existing.projected = p.projected;
    else map.set(p.ts, { ts: p.ts, projected: p.projected });
  }
  return Array.from(map.values()).sort((a, b) => a.ts - b.ts);
}

function fmtCount(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}
