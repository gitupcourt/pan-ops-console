import { useQuery } from "@tanstack/react-query";
import {
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { api, MetricSpec } from "../api";
import { StatusBadge } from "./StatusBadge";

type Props = {
  deviceId: number;
  spec: MetricSpec;
  hours: number;
};

function fmt(n: number | null | undefined): string {
  if (n == null) return "—";
  if (Math.abs(n) >= 1000) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

export function MetricChart({ deviceId, spec, hours }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["series", deviceId, spec.name, hours],
    queryFn: () => api.getSeries(deviceId, spec.name, hours),
  });

  const points = (data?.samples ?? []).map((s) => ({
    ts: new Date(s.ts).getTime(),
    current: s.current,
    max: s.max,
    pct: s.pct,
  }));

  const latest = points[points.length - 1];
  // The max reported by the device usually doesn't change between samples,
  // but pin to the latest just in case (e.g. after a PAN-OS upgrade).
  const max = latest?.max ?? null;
  // Y-axis: anchor to 0..max so headroom is visible. Without a max, let
  // Recharts auto-scale. Pad upper bound by a hair so the line doesn't
  // sit on the top edge.
  const yDomain: [number, number] | [number, "auto"] | undefined =
    max != null && max > 0 ? [0, max] : undefined;

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900/50 p-3">
      <div className="flex items-start justify-between gap-2 mb-2">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-medium text-zinc-200">{spec.description}</h3>
            <StatusBadge status={spec.status} />
          </div>
          <div className="text-[11px] text-zinc-500 mt-0.5">
            {spec.category} · {spec.name}
          </div>
        </div>
        {latest && (
          <div className="text-right">
            <div className="text-lg font-semibold text-zinc-100 tabular-nums leading-tight">
              {fmt(latest.current)}
              {max != null && (
                <span className="text-zinc-500 text-sm font-normal"> / {fmt(max)}</span>
              )}
            </div>
            {latest.pct != null && (
              <div className="text-[11px] text-zinc-500 tabular-nums">
                {latest.pct.toFixed(latest.pct < 1 ? 2 : 1)}% of capacity
              </div>
            )}
          </div>
        )}
      </div>

      <div className="h-24">
        {isLoading ? (
          <div className="h-full flex items-center justify-center text-xs text-zinc-500">
            loading…
          </div>
        ) : error ? (
          <div className="h-full flex items-center justify-center text-xs text-rose-400">
            error
          </div>
        ) : points.length === 0 ? (
          <div className="h-full flex items-center justify-center text-xs text-zinc-600">
            no samples yet
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={points} margin={{ top: 4, right: 6, left: 0, bottom: 0 }}>
              <XAxis dataKey="ts" hide />
              <YAxis
                domain={yDomain ?? ["auto", "auto"]}
                width={42}
                tick={{ fill: "#71717a", fontSize: 10 }}
                tickFormatter={(v) => fmt(v)}
                axisLine={false}
                tickLine={false}
              />
              {max != null && max > 0 && (
                <ReferenceLine
                  y={max}
                  stroke="#dc2626"
                  strokeDasharray="3 3"
                  strokeOpacity={0.6}
                  label={{
                    value: `max ${fmt(max)}`,
                    position: "insideTopRight",
                    fill: "#f87171",
                    fontSize: 10,
                  }}
                />
              )}
              <Tooltip
                contentStyle={{
                  background: "#18181b",
                  border: "1px solid #3f3f46",
                  fontSize: 12,
                }}
                labelFormatter={(ts) => new Date(ts as number).toLocaleString()}
                formatter={(value: number, _name, payload) => {
                  const p = payload?.payload;
                  if (p?.max != null) {
                    const pct = p.max > 0 ? ((value / p.max) * 100).toFixed(2) : null;
                    return [`${fmt(value)} / ${fmt(p.max)}${pct ? `  (${pct}%)` : ""}`, "current"];
                  }
                  return [fmt(value), "current"];
                }}
              />
              <Line
                type="monotone"
                dataKey="current"
                stroke="#60a5fa"
                strokeWidth={2}
                // Show dots when data is sparse — a single sample has no line,
                // so the dot is the ONLY thing the user sees. Made it bigger
                // (r=4) with a white ring so it's visible even when the value
                // sits at the very bottom of a 0..max scale (e.g. 60 of 10000).
                dot={
                  points.length < 30
                    ? { r: 4, fill: "#60a5fa", stroke: "#0b0e14", strokeWidth: 2 }
                    : false
                }
                activeDot={{ r: 5, fill: "#93c5fd", stroke: "#0b0e14", strokeWidth: 2 }}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
