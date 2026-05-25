import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  History,
  MinusCircle,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";
import type {
  ClassifiedCheck,
  Device,
  PreCheckResult,
  PreCheckSummary,
  Severity,
} from "@/lib/types";
import { relTime } from "@/components/ui/Field";

/**
 * Severity icon — shared across the badge, panel, history rows, and the
 * bulk pre-check card.
 */
export function SeverityIcon({ sev }: { sev: Severity }) {
  if (sev === "pass") return <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-emerald-400" />;
  if (sev === "warn") return <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />;
  if (sev === "fail") return <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-red-400" />;
  return <MinusCircle className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" />;
}

/**
 * Small badge with the latest pre-check overall severity + counter strip.
 * Used in the Pre-check column on the Devices table.
 */
export function PrecheckBadge({ summary }: { summary: PreCheckSummary | null }) {
  if (!summary) return <span className="text-xs text-slate-500">—</span>;
  const sev = summary.overall_severity;
  const color =
    sev === "fail" ? "text-red-400" :
    sev === "warn" ? "text-amber-400" :
    sev === "pass" ? "text-emerald-400" : "text-slate-400";
  return (
    <div className="flex flex-col gap-0.5 text-xs">
      <div className={`flex items-center gap-1 font-medium ${color}`}>
        {sev === "fail" && <XCircle className="h-3.5 w-3.5" />}
        {sev === "warn" && <AlertTriangle className="h-3.5 w-3.5" />}
        {sev === "pass" && <CheckCircle2 className="h-3.5 w-3.5" />}
        {sev === "skip" && <MinusCircle className="h-3.5 w-3.5" />}
        {sev}
      </div>
      <div className="text-slate-500">
        <span className="text-emerald-500">{summary.pass_count}✓</span>
        {summary.warn_count > 0 && <> <span className="text-amber-500">{summary.warn_count}⚠</span></>}
        {summary.fail_count > 0 && <> <span className="text-red-500">{summary.fail_count}✗</span></>}
        {summary.skip_count > 0 && <> <span className="text-slate-500">{summary.skip_count}⊘</span></>}
        <span className="ml-1">· {relTime(new Date(summary.ran_at))}</span>
      </div>
    </div>
  );
}

/**
 * One per-check row showing severity icon + check name + classified reason,
 * with the raw library reason underneath when we've overridden it.
 */
export function CheckRow({ name, r }: { name: string; r: ClassifiedCheck }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <SeverityIcon sev={r.severity} />
      <div className="flex-1">
        <div className="font-mono text-xs uppercase tracking-wide text-slate-300">{name}</div>
        <div className="text-slate-400">{r.reason}</div>
        {r.raw_reason && r.raw_reason !== r.reason && (
          <div className="mt-0.5 text-xs italic text-slate-600">raw: {r.raw_reason}</div>
        )}
      </div>
    </div>
  );
}

/**
 * Collapsible row representing one historical pre-check run. Click to expand
 * and see the full per-check breakdown.
 */
export function HistoryRow({ run, label }: { run: PreCheckResult; label?: string }) {
  const [open, setOpen] = useState(false);
  const entries = Object.entries(run.results);
  return (
    <div className="rounded border border-slate-800/50">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-3 px-2 py-1.5 text-left text-xs text-slate-400 hover:bg-slate-900/40"
      >
        <ChevronRight className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`} />
        <SeverityIcon sev={run.overall_severity} />
        {label && <span className="font-medium text-slate-200">{label}</span>}
        <span className="text-slate-300">{new Date(run.ran_at).toLocaleString()}</span>
        <span className="text-emerald-500">{run.pass_count}✓</span>
        {run.warn_count > 0 && <span className="text-amber-500">{run.warn_count}⚠</span>}
        {run.fail_count > 0 && <span className="text-red-500">{run.fail_count}✗</span>}
        {run.skip_count > 0 && <span className="text-slate-500">{run.skip_count}⊘</span>}
        {run.error && <span className="ml-auto text-red-400 truncate max-w-md">{run.error}</span>}
      </button>
      {open && entries.length > 0 && (
        <div className="space-y-1.5 border-t border-slate-800/50 px-3 py-2">
          {entries.map(([name, r]) => (
            <CheckRow key={name} name={name} r={r} />
          ))}
        </div>
      )}
      {open && entries.length === 0 && run.error && (
        <div className="border-t border-slate-800/50 px-3 py-2 text-xs text-red-400">
          {run.error}
        </div>
      )}
    </div>
  );
}

/**
 * The full pre-check disclosure that lives in the expanded device row.
 * Shows current results + a History button that fetches past runs on demand.
 */
export function PrecheckPanel({
  device, precheck, precheckRunning,
}: {
  device: Device;
  precheck: PreCheckResult | null;
  precheckRunning: boolean;
}) {
  const [history, setHistory] = useState<PreCheckResult[] | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const hasResult = precheck != null;

  async function loadHistory() {
    try {
      const rows = await api<PreCheckResult[]>(`/api/devices/${device.id}/precheck/history?limit=10`);
      setHistory(rows);
      setShowHistory(true);
    } catch (err) {
      alert(`Failed to load history: ${err instanceof Error ? err.message : err}`);
    }
  }

  // Don't render at all when there's nothing to show — keeps the expanded
  // detail compact for devices that have never been pre-checked.
  if (!hasResult && !precheckRunning && !device.latest_precheck) {
    return null;
  }

  return (
    <div className="mt-4 rounded-md border border-slate-800 bg-slate-950 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <ShieldCheck className="h-4 w-4 text-indigo-400" />
          <span className="font-medium">Pre-checks</span>
          {precheck && (
            <span className="text-xs text-slate-500">· {new Date(precheck.ran_at).toLocaleString()}</span>
          )}
        </div>
        <button
          onClick={loadHistory}
          className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-0.5 text-xs hover:bg-slate-800"
        >
          <History className="h-3 w-3" /> History
        </button>
      </div>

      {precheckRunning && !hasResult && (
        <div className="text-sm text-slate-400">Running checks…</div>
      )}

      {precheck?.error && !Object.keys(precheck.results).length && (
        <div className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          {precheck.error}
        </div>
      )}

      {precheck && Object.keys(precheck.results).length > 0 && (
        <div className="space-y-1.5">
          {Object.entries(precheck.results).map(([name, r]) => (
            <CheckRow key={name} name={name} r={r} />
          ))}
        </div>
      )}

      {showHistory && history && (
        <div className="mt-3 border-t border-slate-800 pt-3">
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">Last {history.length} runs</div>
          <div className="space-y-1">
            {history.map((h) => <HistoryRow key={h.id ?? h.ran_at} run={h} />)}
            {history.length === 0 && (
              <div className="text-xs text-slate-500">No previous runs.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
