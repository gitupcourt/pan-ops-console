import { useQuery } from "@tanstack/react-query";
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { api, MetricSpec } from "../api";
import { StatusBadge } from "./StatusBadge";

type Props = {
  deviceId: number;
  spec: MetricSpec;
  hours: number;
};

export function MetricChart({ deviceId, spec, hours }: Props) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["series", deviceId, spec.name, hours],
    queryFn: () => api.getSeries(deviceId, spec.name, hours),
  });

  const points = (data?.samples ?? []).map((s) => ({
    ts: new Date(s.ts).getTime(),
    current: s.current,
    pct: s.pct,
    max: s.max,
  }));

  const latest = points[points.length - 1];
  const showPct = spec.has_max && latest?.pct != null;

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
            <div className="text-lg font-semibold text-zinc-100 tabular-nums">
              {showPct
                ? `${latest.pct!.toFixed(1)}%`
                : Number(latest.current).toLocaleString()}
            </div>
            {showPct && (
              <div className="text-[11px] text-zinc-500 tabular-nums">
                {Number(latest.current).toLocaleString()} /{" "}
                {Number(latest.max).toLocaleString()}
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
            <LineChart data={points} margin={{ top: 2, right: 4, left: 0, bottom: 0 }}>
              <XAxis dataKey="ts" hide />
              <YAxis
                hide={!showPct}
                domain={showPct ? [0, 100] : ["auto", "auto"]}
              />
              <Tooltip
                contentStyle={{ background: "#18181b", border: "1px solid #3f3f46", fontSize: 12 }}
                labelFormatter={(ts) => new Date(ts as number).toLocaleString()}
                formatter={(value: number) =>
                  showPct ? `${value.toFixed(1)}%` : Number(value).toLocaleString()
                }
              />
              <Line
                type="monotone"
                dataKey={showPct ? "pct" : "current"}
                stroke="#60a5fa"
                strokeWidth={1.5}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
