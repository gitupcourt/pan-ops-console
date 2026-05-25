import { useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Circle,
  Loader2,
  MinusCircle,
  PauseCircle,
  PlayCircle,
  Power,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";

type Task = {
  id: number;
  device_id: number;
  device_name: string | null;
  device_ha_role: string | null;
  device_current_version: string | null;
  ha_pair_key: string;
  phase: string;
  progress: Record<string, unknown> | null;
  error: string | null;
  updated_at: string;
};

type Job = {
  id: number;
  name: string;
  target_version: string;
  state: string;
  workflow: string;
  require_failover_confirmation: boolean;
  require_primary_upgrade_confirmation: boolean;
  auto_failback: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  tasks: Task[];
};

// Phases where the orchestrator is parked waiting for an HA-flow OK.
// Continue button styling: sky blue ("looks good, proceed").
const AWAITING_PHASES = new Set([
  "awaiting_failover_confirm",
  "awaiting_primary_upgrade_confirm",
]);

// Phases where the orchestrator is parked because a check FAILED and the
// operator must explicitly override. Different button styling so it doesn't
// look like a routine "next step" click — this is acknowledging a problem.
const OVERRIDE_PHASES = new Set([
  "awaiting_precheck_override",
  "awaiting_postcheck_override",
]);

// Phase where the orchestrator is parked waiting for the operator to OK
// the actual reboot of the device. Distinct button — this is the only
// click that drops the mgmt plane.
const REBOOT_PHASE = "awaiting_reboot_confirm";

const TERMINAL_PHASES = new Set(["done", "failed"]);

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();

  // Track tasks whose confirm/override we've just sent, so we can hide the
  // button immediately while waiting for the next poll to reflect the new
  // phase. Otherwise the worker takes ~5s to pick up the token, during which
  // the button looks like it did nothing.
  const [justConfirmed, setJustConfirmed] = useState<Set<number>>(new Set());

  const { data, isLoading } = useQuery({
    queryKey: ["job", id],
    queryFn: () => api<Job>(`/api/jobs/${id}`),
    refetchInterval: (q) => {
      const d = q.state.data as Job | undefined;
      if (d && ["completed", "failed", "aborted"].includes(d.state)) return false;
      return 4000;
    },
  });

  // Once a poll comes back showing the task has moved past awaiting/override/reboot,
  // drop it from justConfirmed so we don't keep hiding the button if the
  // phase loops back.
  useMemo(() => {
    if (!data) return;
    const stillParked = new Set(
      data.tasks
        .filter((t) =>
          AWAITING_PHASES.has(t.phase)
          || OVERRIDE_PHASES.has(t.phase)
          || t.phase === REBOOT_PHASE
        )
        .map((t) => t.id),
    );
    setJustConfirmed((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const id of prev) {
        if (!stillParked.has(id)) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [data]);

  const confirm = useMutation({
    mutationFn: (taskId: number) =>
      api(`/api/jobs/${id}/tasks/${taskId}/confirm`, { method: "POST" }),
    onMutate: (taskId) => {
      // Optimistic: hide the button right away so the click feels responsive
      // even though the worker takes ~5s to notice the token.
      setJustConfirmed((prev) => new Set(prev).add(taskId));
    },
    onError: (_err, taskId) => {
      // Rollback so the user can retry.
      setJustConfirmed((prev) => {
        const next = new Set(prev);
        next.delete(taskId);
        return next;
      });
    },
    onSettled: () => qc.invalidateQueries({ queryKey: ["job", id] }),
  });

  const abort = useMutation({
    mutationFn: () => api(`/api/jobs/${id}/abort`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job", id] }),
  });

  const retry = useMutation({
    mutationFn: () => api(`/api/jobs/${id}/retry`, { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["job", id] }),
  });

  if (isLoading || !data) return <div className="text-slate-400">Loading…</div>;

  return (
    <div className="space-y-6">
      <Link to="/jobs" className="inline-flex items-center gap-1 text-sm text-slate-400 hover:text-slate-200">
        <ChevronLeft className="h-4 w-4" /> All jobs
      </Link>

      <Header
        data={data}
        onAbort={() => abort.mutate()}
        aborting={abort.isPending}
        onRetry={() => retry.mutate()}
        retrying={retry.isPending}
      />

      <Pairs
        data={data}
        onConfirm={(taskId) => confirm.mutate(taskId)}
        confirming={confirm.isPending}
        justConfirmed={justConfirmed}
      />
    </div>
  );
}

function Header({
  data, onAbort, aborting, onRetry, retrying,
}: {
  data: Job;
  onAbort: () => void;
  aborting: boolean;
  onRetry: () => void;
  retrying: boolean;
}) {
  const isTerminal = ["completed", "failed", "aborted"].includes(data.state);
  const retryable = data.state === "failed" || data.state === "aborted";
  return (
    <header className="flex items-start justify-between gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold">{data.name}</h1>
        <div className="flex flex-wrap items-center gap-3 text-sm text-slate-400">
          <span>Target: <span className="font-mono text-slate-200">{data.target_version}</span></span>
          <span>·</span>
          <JobStateBadge state={data.state} />
          <span>·</span>
          <span>{data.tasks.length} target{data.tasks.length !== 1 ? "s" : ""}</span>
          {data.require_primary_upgrade_confirmation && (
            <>
              <span>·</span>
              <span className="rounded bg-sky-900/40 px-1.5 py-0.5 text-xs text-sky-300">paused between HA members</span>
            </>
          )}
          {data.auto_failback && (
            <>
              <span>·</span>
              <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300">auto failback</span>
            </>
          )}
        </div>
        <div className="text-xs text-slate-500">
          Created {new Date(data.created_at).toLocaleString()}
          {data.started_at && <> · Started {new Date(data.started_at).toLocaleString()}</>}
          {data.finished_at && <> · Finished {new Date(data.finished_at).toLocaleString()}</>}
        </div>
      </div>
      <div className="flex gap-2">
        {retryable && (
          <button
            onClick={onRetry}
            disabled={retrying}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            title="Re-run failed tasks from where they stopped. Completed tasks are left alone."
          >
            <PlayCircle className="h-4 w-4" />
            {retrying ? "Retrying…" : "Retry job"}
          </button>
        )}
        {!isTerminal && (
          <button
            onClick={onAbort}
            disabled={aborting}
            className="rounded-md border border-red-700 px-3 py-2 text-sm text-red-400 hover:bg-red-900/30 disabled:opacity-50"
          >
            {aborting ? "Aborting…" : "Abort job"}
          </button>
        )}
      </div>
    </header>
  );
}

function Pairs({
  data,
  onConfirm,
  confirming,
  justConfirmed,
}: {
  data: Job;
  onConfirm: (taskId: number) => void;
  confirming: boolean;
  justConfirmed: Set<number>;
}) {
  // Group by ha_pair_key. For pair-* keys, the two tasks render side by side.
  const groups = useMemo(() => {
    const m = new Map<string, Task[]>();
    for (const t of data.tasks) {
      const arr = m.get(t.ha_pair_key) ?? [];
      arr.push(t);
      m.set(t.ha_pair_key, arr);
    }
    // sort each group: passive first (it gets upgraded first), then active
    for (const arr of m.values()) {
      arr.sort((a, b) => roleOrder(a.device_ha_role) - roleOrder(b.device_ha_role));
    }
    return Array.from(m.entries());
  }, [data.tasks]);

  return (
    <div className="space-y-4">
      {groups.map(([key, tasks]) => (
        <div key={key} className="rounded-lg border border-slate-800 bg-slate-900 p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-xs uppercase tracking-wide text-slate-500">
              {key.startsWith("pair-") ? "HA pair" : "Standalone"}
            </div>
            <div className="text-[10px] font-mono text-slate-600">{key}</div>
          </div>
          <div className={`grid gap-3 ${tasks.length > 1 ? "md:grid-cols-2" : ""}`}>
            {tasks.map((t) => (
              <TaskCard
                key={t.id}
                task={t}
                onConfirm={() => onConfirm(t.id)}
                confirming={confirming}
                justConfirmed={justConfirmed.has(t.id)}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function TaskCard({
  task,
  onConfirm,
  confirming,
  justConfirmed,
}: {
  task: Task;
  onConfirm: () => void;
  confirming: boolean;
  justConfirmed: boolean;
}) {
  const awaiting = AWAITING_PHASES.has(task.phase);
  const overriding = OVERRIDE_PHASES.has(task.phase);
  const rebootGate = task.phase === REBOOT_PHASE;
  const done = task.phase === "done";
  const failed = task.phase === "failed";
  const log: string[] = Array.isArray(task.progress?.log) ? (task.progress!.log as string[]) : [];
  const precheckId = task.progress?.precheck_run_id;
  const postcheckId = task.progress?.postcheck_run_id;
  // Percent progress for the currently-active download / install phase.
  const downloadProgress = typeof task.progress?.download_progress === "number" ? task.progress!.download_progress as number : null;
  const installProgress = typeof task.progress?.install_progress === "number" ? task.progress!.install_progress as number : null;

  return (
    <div className={`rounded-md border p-3 ${
      failed
        ? "border-red-800 bg-red-950/20"
        : done
          ? "border-emerald-800 bg-emerald-950/20"
          : overriding
            ? "border-amber-700 bg-amber-950/30"
            : rebootGate
              ? "border-violet-700 bg-violet-950/30"
              : awaiting
                ? "border-sky-700 bg-sky-950/30"
                : "border-slate-800"
    }`}>
      <div className="mb-2 flex items-center justify-between">
        <div>
          <div className="font-medium text-slate-100">
            {task.device_name ?? `Device #${task.device_id}`}
            {task.device_ha_role && task.device_ha_role !== "standalone" && task.device_ha_role !== "unknown" && (
              <span className="ml-2 rounded bg-slate-800 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-slate-400">
                {task.device_ha_role}
              </span>
            )}
          </div>
          <div className="text-xs text-slate-500">
            Current: <span className="font-mono">{task.device_current_version ?? "—"}</span>
          </div>
        </div>
        <PhaseChip phase={task.phase} />
      </div>

      {precheckId != null && (
        <PrecheckPanel runId={Number(precheckId)} kind="pre" defaultOpen={failed} />
      )}
      {postcheckId != null && (
        <PrecheckPanel runId={Number(postcheckId)} kind="post" defaultOpen={failed} />
      )}

      {task.error && (
        <div className="mb-2 flex items-start gap-1 rounded border border-red-800 bg-red-950/40 px-2 py-1 text-xs text-red-300">
          <AlertCircle className="mt-0.5 h-3 w-3 shrink-0" />
          <span className="whitespace-pre-wrap break-words">{task.error}</span>
        </div>
      )}

      {log.length > 0 && (
        <details className="text-xs text-slate-400">
          <summary className="cursor-pointer hover:text-slate-200">Log ({log.length})</summary>
          <ul className="mt-1 space-y-0.5 rounded bg-slate-950 p-2 font-mono">
            {log.map((line, i) => (
              <li key={i} className="whitespace-pre-wrap break-words">{line}</li>
            ))}
          </ul>
        </details>
      )}

      {(task.phase === "downloading_image" && downloadProgress != null) && (
        <ProgressBar label="Downloading image" pct={downloadProgress} />
      )}
      {((task.phase === "upgrade_secondary" || task.phase === "upgrade_primary") && installProgress != null) && (
        <ProgressBar label="Installing" pct={installProgress} />
      )}

      {awaiting && !justConfirmed && (
        <button
          onClick={onConfirm}
          disabled={confirming}
          className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
        >
          <PlayCircle className="h-4 w-4" />
          {confirming ? "Continuing…" : "Continue"}
        </button>
      )}

      {rebootGate && !justConfirmed && (
        <div className="mt-3 flex items-start gap-2 rounded border border-violet-800 bg-violet-950/30 p-2">
          <Power className="mt-0.5 h-4 w-4 shrink-0 text-violet-300" />
          <div className="flex-1 text-xs text-violet-200">
            New image installed. The device is still running the old version
            until you reboot. Click below when you're ready — mgmt plane will
            drop for a few minutes.
            <button
              onClick={onConfirm}
              disabled={confirming}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-violet-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-500 disabled:opacity-50"
            >
              <Power className="h-4 w-4" />
              {confirming ? "Rebooting…" : "Reboot now"}
            </button>
          </div>
        </div>
      )}

      {overriding && !justConfirmed && (
        <div className="mt-3 flex items-start gap-2 rounded border border-amber-800 bg-amber-950/30 p-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
          <div className="flex-1 text-xs text-amber-200">
            A check failed. Review the details above, then either proceed (acknowledge
            the failure and continue with the upgrade) or abort the job.
            <button
              onClick={onConfirm}
              disabled={confirming}
              className="mt-2 inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
            >
              <AlertTriangle className="h-4 w-4" />
              {confirming ? "Overriding…" : "Proceed anyway"}
            </button>
          </div>
        </div>
      )}

      {justConfirmed && (awaiting || overriding || rebootGate) && (
        <div className="mt-3 flex items-center gap-2 rounded border border-slate-700 bg-slate-950 px-2 py-1.5 text-xs text-slate-300">
          <Loader2 className="h-3 w-3 animate-spin" />
          {rebootGate
            ? "Reboot request sent — worker will issue the restart within ~5s."
            : "Confirmation sent — waiting for the worker to advance (~5s)."}
        </div>
      )}

      <div className="mt-2 text-[10px] text-slate-600">
        Updated {new Date(task.updated_at).toLocaleString()}
      </div>
    </div>
  );
}

function ProgressBar({ label, pct }: { label: string; pct: number }) {
  const clamped = Math.max(0, Math.min(100, pct));
  return (
    <div className="mt-2">
      <div className="mb-1 flex items-center justify-between text-xs text-slate-400">
        <span>{label}</span>
        <span className="font-mono text-slate-300">{clamped}%</span>
      </div>
      <div className="h-1 w-full overflow-hidden rounded bg-slate-800">
        <div
          className="h-full bg-amber-500 transition-all"
          style={{ width: `${clamped}%` }}
        />
      </div>
    </div>
  );
}

type CheckResult = { severity: "pass" | "warn" | "fail" | "skip"; reason: string; raw_reason: string };

type PrecheckRunOut = {
  id: number;
  device_id: number;
  ran_at: string;
  overall_severity: "pass" | "warn" | "fail" | "skip";
  pass_count: number;
  warn_count: number;
  fail_count: number;
  skip_count: number;
  results: Record<string, CheckResult>;
  error: string | null;
};

function PrecheckPanel({
  runId, kind, defaultOpen,
}: { runId: number; kind: "pre" | "post"; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  // Only fetch once expanded — avoids N requests per job page-load.
  const run = useQuery({
    queryKey: ["precheck-run", runId],
    queryFn: () => api<PrecheckRunOut>(`/api/devices/precheck/runs/${runId}`),
    enabled: open,
  });

  const label = kind === "pre" ? "Pre-check" : "Post-check";

  return (
    <div className="mb-2 rounded border border-slate-800 bg-slate-950/40">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs text-slate-400 hover:bg-slate-900/40"
      >
        <ChevronRight className={`h-3 w-3 transition-transform ${open ? "rotate-90" : ""}`} />
        <span className="font-medium text-slate-300">{label} #{runId}</span>
        {run.data && (
          <>
            <SmallSeverityIcon sev={run.data.overall_severity} />
            <span className="text-emerald-500">{run.data.pass_count}✓</span>
            {run.data.warn_count > 0 && <span className="text-amber-500">{run.data.warn_count}⚠</span>}
            {run.data.fail_count > 0 && <span className="text-red-500">{run.data.fail_count}✗</span>}
            {run.data.skip_count > 0 && <span className="text-slate-500">{run.data.skip_count}⊘</span>}
          </>
        )}
      </button>
      {open && (
        <div className="space-y-1 border-t border-slate-800/50 px-3 py-2 text-xs">
          {run.isLoading && <div className="text-slate-500">Loading…</div>}
          {run.error && <div className="text-red-400">{(run.error as Error).message}</div>}
          {run.data && Object.entries(run.data.results).map(([name, r]) => (
            <div key={name} className="flex items-start gap-2">
              <SmallSeverityIcon sev={r.severity} />
              <div className="flex-1">
                <div className="font-mono uppercase tracking-wide text-slate-300">{name}</div>
                <div className="text-slate-400">{r.reason}</div>
              </div>
            </div>
          ))}
          {run.data && Object.keys(run.data.results).length === 0 && (
            <div className="text-slate-500">No results recorded.</div>
          )}
        </div>
      )}
    </div>
  );
}

function SmallSeverityIcon({ sev }: { sev: "pass" | "warn" | "fail" | "skip" }) {
  if (sev === "pass") return <CheckCircle2 className="h-3 w-3 text-emerald-400" />;
  if (sev === "warn") return <AlertTriangle className="h-3 w-3 text-amber-400" />;
  if (sev === "fail") return <XCircle className="h-3 w-3 text-red-400" />;
  return <MinusCircle className="h-3 w-3 text-slate-500" />;
}

function PhaseChip({ phase }: { phase: string }) {
  const info = phaseInfo(phase);
  return (
    <span className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${info.cls}`}>
      {info.icon}
      {info.label}
    </span>
  );
}

function phaseInfo(phase: string): { cls: string; icon: React.ReactNode; label: string } {
  const labels: Record<string, string> = {
    pending: "Pending",
    precheck: "Pre-check",
    awaiting_precheck_override: "Pre-check failed — awaiting override",
    snapshot: "Snapshot",
    downloading_image: "Downloading image",
    suspend_secondary: "Suspending HA (secondary)",
    upgrade_secondary: "Upgrading secondary",
    postcheck_secondary: "Post-check (secondary)",
    awaiting_postcheck_override: "Post-check failed — awaiting override",
    awaiting_reboot_confirm: "Install done — awaiting reboot confirmation",
    awaiting_failover_confirm: "Awaiting failover confirmation",
    failover: "Failover",
    awaiting_primary_upgrade_confirm: "Awaiting OK to upgrade primary",
    upgrade_primary: "Upgrading primary",
    postcheck_primary: "Post-check (primary)",
    failback: "Failback",
    report: "Report",
    done: "Done",
    failed: "Failed",
  };
  const label = labels[phase] ?? phase;
  if (phase === "done") return { cls: "bg-emerald-900/40 text-emerald-300", icon: <CheckCircle2 className="h-3 w-3" />, label };
  if (phase === "failed") return { cls: "bg-red-900/40 text-red-300", icon: <XCircle className="h-3 w-3" />, label };
  if (OVERRIDE_PHASES.has(phase)) return { cls: "bg-amber-900/40 text-amber-300", icon: <AlertTriangle className="h-3 w-3" />, label };
  if (phase === REBOOT_PHASE) return { cls: "bg-violet-900/40 text-violet-300", icon: <Power className="h-3 w-3" />, label };
  if (AWAITING_PHASES.has(phase)) return { cls: "bg-sky-900/40 text-sky-300", icon: <PauseCircle className="h-3 w-3" />, label };
  if (phase === "pending") return { cls: "bg-slate-800 text-slate-400", icon: <Circle className="h-3 w-3" />, label };
  return { cls: "bg-amber-900/40 text-amber-300", icon: <Loader2 className="h-3 w-3 animate-spin" />, label };
}

function roleOrder(role: string | null): number {
  // Render passive first since it gets upgraded first.
  if (role === "passive") return 0;
  if (role === "active") return 1;
  return 2;
}

function JobStateBadge({ state }: { state: string }) {
  const map: Record<string, { color: string; icon: React.ReactNode; text: string }> = {
    pending: { color: "text-slate-400", icon: <Circle className="h-3 w-3" />, text: "pending" },
    running: { color: "text-amber-400", icon: <Loader2 className="h-3 w-3 animate-spin" />, text: "running" },
    awaiting_confirmation: { color: "text-sky-400", icon: <PauseCircle className="h-3 w-3" />, text: "awaiting confirmation" },
    completed: { color: "text-emerald-400", icon: <CheckCircle2 className="h-3 w-3" />, text: "completed" },
    failed: { color: "text-red-400", icon: <XCircle className="h-3 w-3" />, text: "failed" },
    aborted: { color: "text-slate-500", icon: <XCircle className="h-3 w-3" />, text: "aborted" },
  };
  const info = map[state] ?? { color: "text-slate-400", icon: <Circle className="h-3 w-3" />, text: state };
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-medium ${info.color}`}>
      {info.icon}
      {info.text}
    </span>
  );
}

// Mark as used so eslint --no-unused doesn't trip on it (kept for symmetry).
void TERMINAL_PHASES;
