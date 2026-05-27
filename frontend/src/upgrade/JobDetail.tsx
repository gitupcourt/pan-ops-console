import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { NavLink, useParams } from "react-router-dom";

import {
  api,
  PrecheckSummary,
  TaskPhase,
  UpgradeJobDetail,
  UpgradeTask,
} from "../api";
import { Button, Card, CardHeader } from "../core/ui/ui";
import { JobStateBadge } from "./UpgradeJobs";

/**
 * Per-job detail view at /upgrade/jobs/:id.
 *
 * Shows the job config + all its DeviceUpgradeTasks with their current
 * phase, grouped by ha_pair_key so an operator can see HA pair
 * progress as a unit. Each task row exposes the action that's valid
 * for its current phase — Confirm at AWAITING_*_CONFIRM, Override at
 * AWAITING_*_OVERRIDE, Retry at FAILED.
 *
 * Polls every 3s while the job isn't terminal so progress shows up
 * without manual refresh. Terminal jobs (COMPLETED / FAILED / ABORTED)
 * stop polling — nothing's going to change.
 */
export default function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>();
  const id = jobId ? Number(jobId) : NaN;

  const jobQ = useQuery({
    queryKey: ["upgrade-job", id],
    queryFn: () => api.getUpgradeJob(id),
    enabled: !Number.isNaN(id),
    refetchInterval: (q) => {
      const state = q.state.data?.state;
      if (!state) return 5000;
      return ["completed", "failed", "aborted"].includes(state)
        ? false
        : 3000;
    },
  });

  if (Number.isNaN(id)) {
    return (
      <div className="text-sm text-rose-400">Invalid job id in URL.</div>
    );
  }
  if (jobQ.isLoading) {
    return <div className="text-sm text-zinc-500">Loading…</div>;
  }
  if (jobQ.error) {
    return (
      <div className="text-sm text-rose-400">
        Failed to load job: {(jobQ.error as Error).message}
      </div>
    );
  }
  const job = jobQ.data;
  if (!job) return null;

  return (
    <div className="space-y-6">
      <NavLink
        to="/upgrade"
        className="text-xs text-zinc-500 hover:text-zinc-200"
      >
        ← All jobs
      </NavLink>

      <Card>
        <CardHeader
          title={job.name}
          description={`Target ${job.target_version} · ${job.workflow} workflow · ${job.task_count} device(s)`}
          action={<JobLifecycleActions job={job} />}
        />
        <JobConfigSummary job={job} />
      </Card>

      <Card>
        <CardHeader
          title="Tasks"
          description="One row per device. HA-paired devices share a pair key and upgrade in sequence."
        />
        <TaskList tasks={job.tasks} />
      </Card>
    </div>
  );
}

function JobLifecycleActions({ job }: { job: UpgradeJobDetail }) {
  const qc = useQueryClient();

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["upgrade-job", job.id] });
    qc.invalidateQueries({ queryKey: ["upgrade-jobs"] });
  };

  const startM = useMutation({
    mutationFn: () => api.startUpgradeJob(job.id),
    onSuccess: invalidate,
  });
  const abortM = useMutation({
    mutationFn: () => api.abortUpgradeJob(job.id),
    onSuccess: invalidate,
  });
  const deleteM = useMutation({
    mutationFn: () => api.deleteUpgradeJob(job.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["upgrade-jobs"] });
      // Navigate away — handled by caller via key change; if this
      // detail page renders for a now-missing job the next refetch
      // 404s and the operator can click "All jobs."
    },
  });

  return (
    <div className="flex items-center gap-2">
      <JobStateBadge state={job.state} />
      {job.state === "pending" && (
        <Button
          variant="primary"
          onClick={() => startM.mutate()}
          disabled={startM.isPending}
        >
          {startM.isPending ? "Starting…" : "Start"}
        </Button>
      )}
      {job.state === "running" && (
        <Button
          variant="danger"
          onClick={() => abortM.mutate()}
          disabled={abortM.isPending}
        >
          {abortM.isPending ? "Aborting…" : "Abort"}
        </Button>
      )}
      {["pending", "completed", "failed", "aborted"].includes(job.state) && (
        <Button
          variant="danger"
          onClick={() => {
            if (window.confirm(`Delete job "${job.name}"?`)) deleteM.mutate();
          }}
          disabled={deleteM.isPending}
        >
          Delete
        </Button>
      )}
    </div>
  );
}

function JobConfigSummary({ job }: { job: UpgradeJobDetail }) {
  // Compact key:value grid of the job's auto/confirm config so the
  // operator can verify what they signed up for without re-opening
  // the create form.
  const rows: [string, string][] = [
    [
      "Image source",
      job.device_pull_image
        ? "Each device pulls from updates.paloaltonetworks.com"
        : `Registered image #${job.image_id ?? "?"}`,
    ],
    [
      "Require failover confirmation",
      yesNo(job.require_failover_confirmation),
    ],
    [
      "Require primary upgrade confirmation",
      yesNo(job.require_primary_upgrade_confirmation),
    ],
    ["Auto-failback", yesNo(job.auto_failback)],
    ["Auto-reboot after install", yesNo(job.auto_reboot_after_install)],
    [
      "Auto-acknowledge precheck failures",
      yesNo(job.auto_ack_precheck_failures),
    ],
    [
      "Auto-acknowledge postcheck failures",
      yesNo(job.auto_ack_postcheck_failures),
    ],
    ["Created", new Date(job.created_at).toLocaleString()],
    ["Started", job.started_at ? new Date(job.started_at).toLocaleString() : "—"],
    [
      "Finished",
      job.finished_at ? new Date(job.finished_at).toLocaleString() : "—",
    ],
  ];

  return (
    <div className="px-4 py-3 grid grid-cols-1 md:grid-cols-2 gap-x-6 gap-y-1 text-xs">
      {rows.map(([k, v]) => (
        <div key={k} className="flex justify-between border-b border-zinc-800/30 py-1">
          <span className="text-zinc-500">{k}</span>
          <span className="text-zinc-300 text-right">{v}</span>
        </div>
      ))}
    </div>
  );
}

function yesNo(b: boolean): string {
  return b ? "Yes" : "No";
}

function TaskList({ tasks }: { tasks: UpgradeTask[] }) {
  // Group by ha_pair_key so paired devices render together.
  const grouped = new Map<string, UpgradeTask[]>();
  for (const t of tasks) {
    grouped.set(t.ha_pair_key, [...(grouped.get(t.ha_pair_key) ?? []), t]);
  }
  // Sort: pairs (multiple members) first, then standalones.
  const groups = Array.from(grouped.entries()).sort(
    ([, a], [, b]) => b.length - a.length,
  );

  return (
    <table className="w-full text-sm">
      <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
        <tr>
          <th className="px-2 py-2 w-6"></th>
          <th className="text-left px-4 py-2 font-medium">Device</th>
          <th className="text-left px-4 py-2 font-medium">Pair</th>
          <th className="text-left px-4 py-2 font-medium">Phase</th>
          <th className="text-left px-4 py-2 font-medium">Updated</th>
          <th className="text-left px-4 py-2 font-medium">Action</th>
        </tr>
      </thead>
      <tbody>
        {groups.map(([key, pair]) =>
          pair.map((t, idx) => (
            <TaskRow
              key={t.id}
              task={t}
              pairKey={key}
              isPairLead={idx === 0 && pair.length > 1}
              isPaired={pair.length > 1}
            />
          )),
        )}
      </tbody>
    </table>
  );
}

function TaskRow({
  task: t,
  pairKey,
  isPairLead,
  isPaired,
}: {
  task: UpgradeTask;
  pairKey: string;
  isPairLead: boolean;
  isPaired: boolean;
}) {
  // Auto-expand parked rows + failed rows — operator needs to see
  // what's blocked or what went wrong without an extra click. They
  // can still collapse if they want.
  const needsAttention =
    PARKED_CONFIRM.includes(t.phase) ||
    PARKED_OVERRIDE.includes(t.phase) ||
    t.phase === "failed";
  const [expanded, setExpanded] = useState(needsAttention);

  return (
    <Fragment>
      <tr
        className={`border-b border-zinc-800/50 ${
          isPairLead ? "border-t-2 border-t-zinc-700/60" : ""
        } ${needsAttention ? "bg-amber-950/10" : ""}`}
      >
        <td className="px-2 py-2 align-top">
          <button
            onClick={() => setExpanded((v) => !v)}
            className="text-zinc-500 hover:text-zinc-300 text-xs"
            title={expanded ? "Collapse" : "Expand"}
          >
            {expanded ? "▾" : "▸"}
          </button>
        </td>
        <td className="px-4 py-2 text-zinc-100">{t.device_name}</td>
        <td className="px-4 py-2 text-zinc-500 text-xs font-mono">
          {isPaired ? pairKey : "—"}
        </td>
        <td className="px-4 py-2">
          <TaskPhaseBadge phase={t.phase} />
          {t.error && (
            <div
              className="text-[10px] text-rose-400 mt-0.5 max-w-md truncate"
              title={t.error}
            >
              {t.error}
            </div>
          )}
        </td>
        <td className="px-4 py-2 text-xs text-zinc-500">
          {relTime(t.updated_at)}
        </td>
        <td className="px-4 py-2">
          <TaskActionButtons task={t} />
        </td>
      </tr>
      {expanded && (
        <tr className="bg-zinc-950/60">
          <td colSpan={6} className="px-4 py-3 border-b border-zinc-800/50">
            <TaskExpandedDetail task={t} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function TaskExpandedDetail({ task: t }: { task: UpgradeTask }) {
  // Three blocks: at-a-glance summary, recent activity log, precheck
  // results (when present). Order picked to put the most-actionable
  // info first — what the orchestrator is doing right now and what
  // it just found.
  const log = readLog(t.progress);
  const completedPhases = readCompletedPhases(t.progress);
  const failingChecks = readFailingChecks(t.progress);

  return (
    <div className="grid gap-3 text-xs">
      <CurrentPhaseExplainer phase={t.phase} failingChecks={failingChecks} />

      {completedPhases.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            Completed phases
          </div>
          <div className="flex flex-wrap gap-1.5">
            {completedPhases.map((p) => (
              <span
                key={p}
                className="px-1.5 py-0.5 rounded bg-emerald-950/50 text-emerald-300 border border-emerald-900/40"
              >
                ✓ {p}
              </span>
            ))}
          </div>
        </div>
      )}

      {t.precheck && <PrecheckResultsTable precheck={t.precheck} />}

      {log.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
            Recent activity ({log.length} line{log.length === 1 ? "" : "s"})
          </div>
          <pre className="max-h-48 overflow-y-auto p-2 rounded bg-zinc-900/60 border border-zinc-800 text-[11px] text-zinc-300 whitespace-pre-wrap leading-snug font-mono">
            {log.slice(-25).join("\n")}
          </pre>
        </div>
      )}
    </div>
  );
}

function CurrentPhaseExplainer({
  phase,
  failingChecks,
}: {
  phase: TaskPhase;
  failingChecks: string[];
}) {
  // Plain-English description of what the orchestrator is doing or
  // waiting on. Helps an operator who doesn't have the phase enum
  // memorized.
  if (PARKED_OVERRIDE.includes(phase)) {
    const which =
      phase === "awaiting_precheck_override" ? "pre-check" : "post-check";
    return (
      <div className="px-2 py-1.5 rounded bg-amber-950/40 border border-amber-900/40 text-amber-200">
        <strong>Waiting on you.</strong> The {which} found something the
        classifier marked as a FAIL severity. Review the results below,
        then:
        <ul className="list-disc list-inside mt-1 text-[11px] space-y-0.5">
          <li>
            <em>Re-run check</em> — if you fixed the issue externally
            (e.g. pushed a candidate config from Panorama), re-execute
            the check on the device to verify.
          </li>
          <li>
            <em>Override + proceed</em> — acknowledge the failure and
            continue with the upgrade anyway.
          </li>
          <li>
            Abort the job from the job header above if you want to
            stop here.
          </li>
        </ul>
        {failingChecks.length > 0 && (
          <div className="text-[11px] mt-1 text-amber-300">
            Failing: <span className="font-mono">{failingChecks.join(", ")}</span>
          </div>
        )}
      </div>
    );
  }
  if (PARKED_CONFIRM.includes(phase)) {
    const desc =
      phase === "awaiting_reboot_confirm"
        ? "device is about to reboot to complete the install"
        : phase === "awaiting_failover_confirm"
          ? "HA failover is about to fire so the upgrading peer can be taken offline safely"
          : "primary (active) peer is about to be upgraded";
    return (
      <div className="px-2 py-1.5 rounded bg-blue-950/40 border border-blue-900/40 text-blue-200">
        <strong>Waiting on you.</strong> The {desc}. Click <em>Confirm</em> to
        proceed; the job was created with manual confirmation required at
        this step.
      </div>
    );
  }
  if (phase === "failed") {
    return (
      <div className="px-2 py-1.5 rounded bg-rose-950/40 border border-rose-900/40 text-rose-200">
        <strong>Failed.</strong> Review the recent activity for the error,
        then click Retry to resume from the last completed phase, or Abort
        the job to give up.
      </div>
    );
  }
  if (phase === "pending") {
    return (
      <div className="text-zinc-400">
        Pending. The orchestrator hasn't started this task yet — typically
        means an HA pair peer is being upgraded first.
      </div>
    );
  }
  if (phase === "done") {
    return (
      <div className="text-emerald-400">
        Done. All phases completed successfully for this device.
      </div>
    );
  }
  // Active phase — describe what's happening right now.
  const active: Record<string, string> = {
    precheck: "Running readiness checks on the device.",
    snapshot: "Taking the pre-upgrade snapshot for diff comparison.",
    downloading_image: "Downloading the target PAN-OS image to the device.",
    suspend_secondary: "Suspending HA on the passive peer.",
    upgrade_secondary: "Installing the new PAN-OS image on the passive peer.",
    postcheck_secondary:
      "Running post-checks on the upgraded passive peer.",
    failover: "Firing the HA failover (active ↔ passive).",
    upgrade_primary: "Installing the new PAN-OS image on the (former) active peer.",
    postcheck_primary: "Running post-checks on the upgraded primary peer.",
    failback: "Failing HA back to original primary.",
    postcheck: "Running post-checks on the upgraded device.",
  };
  const msg = active[phase];
  if (msg) {
    return <div className="text-blue-300">▸ {msg}</div>;
  }
  return null;
}

function PrecheckResultsTable({ precheck }: { precheck: PrecheckSummary }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1 flex items-center gap-3">
        <span>
          Latest pre/post-check ({fmtTime(precheck.ran_at)})
        </span>
        <span className="text-zinc-400 normal-case tracking-normal">
          <PrecheckCount label="pass" n={precheck.pass_count} tone="emerald" />
          <PrecheckCount label="warn" n={precheck.warn_count} tone="amber" />
          <PrecheckCount label="fail" n={precheck.fail_count} tone="rose" />
          <PrecheckCount label="skip" n={precheck.skip_count} tone="zinc" />
        </span>
      </div>
      {precheck.error ? (
        <div className="p-2 rounded bg-rose-950/40 border border-rose-900/40 text-rose-300 text-[11px]">
          Check runner errored before evaluating any check:{" "}
          <span className="font-mono">{precheck.error}</span>
        </div>
      ) : precheck.checks.length === 0 ? (
        <div className="text-zinc-500 italic">No check results recorded.</div>
      ) : (
        <table className="w-full text-[11px]">
          <thead className="text-[10px] uppercase text-zinc-500">
            <tr>
              <th className="text-left px-2 py-1 font-medium w-20">Severity</th>
              <th className="text-left px-2 py-1 font-medium w-44">Check</th>
              <th className="text-left px-2 py-1 font-medium">Reason</th>
            </tr>
          </thead>
          <tbody>
            {precheck.checks.map((c) => (
              <tr key={c.name} className="border-t border-zinc-800/40">
                <td className="px-2 py-1">
                  <SeverityBadge severity={c.severity} />
                </td>
                <td className="px-2 py-1 font-mono text-zinc-300">{c.name}</td>
                <td className="px-2 py-1 text-zinc-400">{c.reason || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function PrecheckCount({
  label,
  n,
  tone,
}: {
  label: string;
  n: number;
  tone: "emerald" | "amber" | "rose" | "zinc";
}) {
  if (n === 0) return null;
  const cls = {
    emerald: "text-emerald-400",
    amber: "text-amber-400",
    rose: "text-rose-400",
    zinc: "text-zinc-500",
  }[tone];
  return (
    <span className={`mr-2 ${cls}`}>
      {n} {label}
    </span>
  );
}

function SeverityBadge({ severity }: { severity: string }) {
  const cls =
    severity === "fail"
      ? "bg-rose-900/40 text-rose-300 border-rose-800/40"
      : severity === "warn"
        ? "bg-amber-900/40 text-amber-300 border-amber-800/40"
        : severity === "skip"
          ? "bg-zinc-900/40 text-zinc-400 border-zinc-700/40"
          : "bg-emerald-900/40 text-emerald-300 border-emerald-800/40";
  return (
    <span
      className={`inline-block px-1.5 py-0.5 rounded border text-[10px] uppercase tracking-wider ${cls}`}
    >
      {severity}
    </span>
  );
}

// ----- progress.json helpers -----

function readLog(progress: Record<string, unknown> | null): string[] {
  if (!progress) return [];
  const raw = progress.log;
  if (!Array.isArray(raw)) return [];
  return raw.filter((x): x is string => typeof x === "string");
}

function readCompletedPhases(
  progress: Record<string, unknown> | null,
): string[] {
  if (!progress) return [];
  const raw = progress.completed_phases;
  if (!Array.isArray(raw)) return [];
  return raw.filter((x): x is string => typeof x === "string");
}

function readFailingChecks(
  progress: Record<string, unknown> | null,
): string[] {
  if (!progress) return [];
  const raw = progress.failing_checks;
  if (!Array.isArray(raw)) return [];
  return raw.filter((x): x is string => typeof x === "string");
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

const PARKED_CONFIRM: TaskPhase[] = [
  "awaiting_reboot_confirm",
  "awaiting_failover_confirm",
  "awaiting_primary_upgrade_confirm",
];
const PARKED_OVERRIDE: TaskPhase[] = [
  "awaiting_precheck_override",
  "awaiting_postcheck_override",
];

function TaskActionButtons({ task }: { task: UpgradeTask }) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["upgrade-job", task.job_id] });
  };

  const confirmM = useMutation({
    mutationFn: () => api.confirmUpgradeTask(task.id),
    onSuccess: invalidate,
  });
  const overrideM = useMutation({
    mutationFn: () => api.overrideUpgradeTask(task.id),
    onSuccess: invalidate,
  });
  const rerunM = useMutation({
    mutationFn: () => api.rerunUpgradeTaskCheck(task.id),
    onSuccess: invalidate,
  });
  const retryM = useMutation({
    mutationFn: () => api.retryUpgradeTask(task.id),
    onSuccess: invalidate,
  });

  if (PARKED_CONFIRM.includes(task.phase)) {
    return (
      <Button
        variant="primary"
        onClick={() => confirmM.mutate()}
        disabled={confirmM.isPending}
      >
        {confirmM.isPending ? "Confirming…" : "Confirm"}
      </Button>
    );
  }
  if (PARKED_OVERRIDE.includes(task.phase)) {
    // Three operator choices at a check-failure gate. Re-run goes
    // first (left) because it's the lowest-risk path — "I fixed it,
    // verify again." Override is the deliberate-bypass red button.
    const busy = overrideM.isPending || rerunM.isPending;
    return (
      <div className="flex items-center gap-2">
        <Button
          onClick={() => rerunM.mutate()}
          disabled={busy}
          title="Re-execute the check on the device. Use after fixing the underlying issue externally (e.g. pushing config from Panorama)."
        >
          {rerunM.isPending ? "Re-running…" : "Re-run check"}
        </Button>
        <Button
          variant="danger"
          onClick={() => overrideM.mutate()}
          disabled={busy}
        >
          {overrideM.isPending ? "Overriding…" : "Override + proceed"}
        </Button>
      </div>
    );
  }
  if (task.phase === "failed") {
    return (
      <Button onClick={() => retryM.mutate()} disabled={retryM.isPending}>
        {retryM.isPending ? "Retrying…" : "Retry"}
      </Button>
    );
  }
  return <span className="text-xs text-zinc-600">—</span>;
}

function TaskPhaseBadge({ phase }: { phase: TaskPhase }) {
  // Color logic: ongoing (blue) / parked-waiting (amber) /
  // terminal-good (emerald) / terminal-bad (rose) / pending (zinc).
  const parked = (PARKED_CONFIRM as string[])
    .concat(PARKED_OVERRIDE as string[])
    .includes(phase);
  let cls = "border-zinc-700 bg-zinc-800 text-zinc-300";
  if (parked) cls = "border-amber-700 bg-amber-900/40 text-amber-200";
  else if (phase === "done") cls = "border-emerald-700 bg-emerald-900/40 text-emerald-200";
  else if (phase === "failed") cls = "border-rose-700 bg-rose-900/40 text-rose-200";
  else if (phase !== "pending")
    cls = "border-blue-700 bg-blue-900/40 text-blue-200";

  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide border ${cls}`}
    >
      {phase.replace(/_/g, " ")}
    </span>
  );
}

function relTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "in the future";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `${hr}h ago`;
  return `${Math.floor(hr / 24)}d ago`;
}
