import { Loader2, RefreshCw, ShieldCheck } from "lucide-react";
import type { BulkSummary, Device } from "@/lib/types";
import { HistoryRow } from "./PrecheckPanel";

/**
 * Live progress card shown above the Devices table while a bulk pre-check
 * is running. Sorts completed devices worst-first so the operator sees the
 * issues quickly; renders pending devices as spinners.
 *
 * `onRefresh` exists because React Query stops polling once
 * `pending_count === 0`, and if a long-running tab was inactive when that
 * transition happened, the card could be stuck on stale "running" state.
 * Manual refresh sidesteps that without forcing a page reload.
 */
export function BulkPrecheckCard({
  summary, devices, targetIds, onDismiss, onRefresh,
}: {
  summary: BulkSummary;
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
  const severityRank: Record<string, number> = { fail: 0, warn: 1, pass: 2, skip: 3 };
  const orderedResults = Object.values(summary.results).sort((a, b) => {
    const sa = severityRank[a.overall_severity] ?? 9;
    const sb = severityRank[b.overall_severity] ?? 9;
    if (sa !== sb) return sa - sb;
    return (byId.get(a.device_id)?.name ?? "").localeCompare(byId.get(b.device_id)?.name ?? "");
  });

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-medium text-white">
            <ShieldCheck className="h-4 w-4 text-amber-400" />
            Bulk pre-check {done ? "complete" : "in progress"}
            <span className="text-xs text-slate-400">
              · {summary.completed_count}/{summary.target_count} devices
              {!done && " · " + (summary.pending_count) + " in flight"}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-3 text-xs">
            {summary.pass_count > 0 && <span className="text-emerald-400">{summary.pass_count} pass</span>}
            {summary.warn_count > 0 && <span className="text-amber-400">{summary.warn_count} warn</span>}
            {summary.fail_count > 0 && <span className="text-red-400">{summary.fail_count} fail</span>}
            {summary.error_count > 0 && <span className="text-red-500">{summary.error_count} error</span>}
            {summary.completed_count === 0 && !done && <span className="text-slate-500">spinning up…</span>}
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
          className={`h-full transition-all ${done ? "bg-emerald-500" : "bg-amber-500"}`}
          style={{ width: `${pct}%` }}
        />
      </div>

      {orderedResults.length > 0 && (
        <div className="mt-4 space-y-1.5">
          <div className="text-xs uppercase tracking-wide text-slate-500">Per-device results</div>
          {orderedResults.map((r) => (
            <HistoryRow
              key={r.device_id}
              run={r}
              label={byId.get(r.device_id)?.name ?? `Device #${r.device_id}`}
            />
          ))}
          {targetIds
            .filter((id) => summary.results[id] === undefined)
            .map((id) => (
              <div
                key={`pending-${id}`}
                className="flex items-center gap-3 rounded border border-slate-800/50 px-2 py-1.5 text-xs text-slate-500"
              >
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                <span className="font-medium text-slate-300">{byId.get(id)?.name ?? `Device #${id}`}</span>
                <span>queued / running…</span>
              </div>
            ))}
        </div>
      )}
    </div>
  );
}
