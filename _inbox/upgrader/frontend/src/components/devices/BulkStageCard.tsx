import { CheckCircle2, Loader2, Package, RefreshCw, XCircle } from "lucide-react";
import type { BulkStageSummary, Device } from "@/lib/types";

/**
 * Live progress card for a bulk pre-stage operation (download PAN-OS image to
 * N devices). Mirrors BulkPrecheckCard's shape but the per-device row is
 * simpler — no per-check breakdown to expand, just "downloading" / "done" /
 * "failed".
 *
 * State for each device is derived from `finished_at` (truthy = settled) plus
 * `outcome`. Doing it that way avoids a subtle bug where outcome defaults to
 * a fail-like value before the worker actually finishes — without this guard
 * every in-flight row would render with a red X.
 */
export function BulkStageCard({
  summary, devices, targetIds, onDismiss, onRefresh,
}: {
  summary: BulkStageSummary;
  devices: Device[];
  targetIds: number[];
  onDismiss: () => void;
  onRefresh: () => void;
}) {
  const done = summary.pending_count === 0;
  const pct = summary.target_count > 0
    ? Math.round((summary.completed_count / summary.target_count) * 100)
    : 0;
  const byId = new Map(devices.map((d) => [d.id, d]));

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-white">
            <Package className="h-4 w-4 text-sky-400" />
            Pre-stage <span className="font-mono">{summary.version}</span> {done ? "complete" : "in progress"}
            <span className="text-xs text-slate-400">
              · {summary.completed_count}/{summary.target_count} devices
              {!done && " · " + summary.pending_count + " downloading"}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs">
            {summary.success_count > 0 && <span className="text-emerald-400">{summary.success_count} downloaded</span>}
            {summary.failure_count > 0 && <span className="text-red-400">{summary.failure_count} failed</span>}
            {summary.completed_count === 0 && !done && <span className="text-slate-500">downloading… (this can take several minutes per device)</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={onRefresh}
            className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-0.5 text-xs text-slate-300 hover:bg-slate-800"
            title="Re-fetch latest status from the server"
          >
            <RefreshCw className="h-3 w-3" /> Refresh
          </button>
          <button onClick={onDismiss} className="text-xs text-slate-400 hover:text-slate-200">Dismiss</button>
        </div>
      </div>
      <div className="mt-3 h-1 w-full overflow-hidden rounded bg-slate-800">
        <div
          className={`h-full transition-all ${done ? "bg-emerald-500" : "bg-sky-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mt-4 space-y-1.5">
        {targetIds.map((id) => {
          const r = summary.results[id];
          const name = byId.get(id)?.name ?? `Device #${id}`;
          const isRunning = !r || !r.finished_at;
          const ok = !!r && !!r.finished_at && r.outcome === "pass";

          return (
            <div
              key={id}
              className="flex items-center gap-3 rounded border border-slate-800/50 px-2 py-1.5 text-xs"
            >
              {isRunning ? (
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-sky-400" />
              ) : ok ? (
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
              ) : (
                <XCircle className="h-3.5 w-3.5 shrink-0 text-red-400" />
              )}
              <span className={`font-medium ${isRunning ? "text-slate-300" : "text-slate-200"}`}>
                {name}
              </span>
              <span className="text-slate-400">{r?.version ?? summary.version}</span>
              {isRunning && <span className="text-slate-500">downloading…</span>}
              {!isRunning && r?.error && (
                <span className="ml-auto whitespace-pre-wrap break-words text-red-300">{r.error}</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
