import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, ReactNode, useState } from "react";
import { NavLink, useParams } from "react-router-dom";

import {
  api,
  JobState,
  PrecheckSummary,
  SnapshotDiff,
  SnapshotDiffAreaReport,
  TaskPhase,
  UpgradeJobDetail,
  UpgradeTask,
} from "../api";
import { DiskCleanupModal } from "../core/devices/DiskCleanupModal";
import { ErrorBoundary } from "../core/ui/ErrorBoundary";
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
        {job.state === "failed" && job.failure_reason && (
          <div className="mx-4 mt-1 mb-2 px-3 py-2 rounded bg-rose-950/40 border border-rose-900/50 text-rose-200 text-xs">
            <span className="font-semibold">Job failed:</span>{" "}
            {job.failure_reason}
            <div className="text-[11px] text-rose-300/70 mt-1">
              Expand the affected device row below for the per-phase
              activity log, or check the worker logs for a full traceback.
            </div>
          </div>
        )}
        <JobConfigSummary job={job} />
      </Card>

      <Card>
        <CardHeader
          title="Tasks"
          description="One row per device. HA-paired devices share a pair key and upgrade in sequence."
        />
        <TaskList tasks={job.tasks} jobState={job.state} />
      </Card>
    </div>
  );
}

function JobLifecycleActions({ job }: { job: UpgradeJobDetail }) {
  const qc = useQueryClient();

  // `await` the invalidate so React Query's `isPending` stays true
  // through the refetch — without this, the button flips back to its
  // idle label the instant the POST resolves, even though the worker
  // hasn't yet picked up the state change. Operator perception: the
  // button "did nothing." With await, the disabled+spinner state
  // persists until the next /jobs/{id} payload lands, which is when
  // the new task phase actually becomes visible in the UI.
  const invalidate = async () => {
    await Promise.all([
      qc.invalidateQueries({ queryKey: ["upgrade-job", job.id] }),
      qc.invalidateQueries({ queryKey: ["upgrade-jobs"] }),
    ]);
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
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["upgrade-jobs"] });
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
          <BusyLabel busy={startM.isPending} busyLabel="Starting…" idleLabel="Start" />
        </Button>
      )}
      {job.state === "running" && (
        <Button
          variant="danger"
          onClick={() => abortM.mutate()}
          disabled={abortM.isPending}
        >
          <BusyLabel busy={abortM.isPending} busyLabel="Aborting…" idleLabel="Abort" />
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
          <BusyLabel busy={deleteM.isPending} busyLabel="Deleting…" idleLabel="Delete" />
        </Button>
      )}
    </div>
  );
}

/**
 * Tiny SVG spinner — used inside Buttons during their in-flight state.
 *
 * Tailwind's `animate-spin` does the rotation; the SVG provides the
 * arc-of-a-circle shape that reads as "loading." Inline because we
 * only need it here and keeping it inline avoids icon-library churn.
 */
function Spinner({ className = "" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin h-3 w-3 ${className}`}
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden="true"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"
      />
    </svg>
  );
}

/**
 * Spinner + label, with a small gap, so an in-flight Button reads
 * unambiguously as "working on it" rather than "did you actually
 * click that?"
 */
function BusyLabel({
  busy,
  busyLabel,
  idleLabel,
}: {
  busy: boolean;
  busyLabel: string;
  idleLabel: string;
}) {
  if (!busy) return <>{idleLabel}</>;
  return (
    <span className="inline-flex items-center gap-1.5">
      <Spinner />
      {busyLabel}
    </span>
  );
}

function JobConfigSummary({ job }: { job: UpgradeJobDetail }) {
  // Compact key:value grid of the job's auto/confirm config so the
  // operator can verify what they signed up for without re-opening
  // the create form.
  const rows: [string, string][] = [
    [
      "Mode",
      job.pre_stage_mode === "stage_only"
        ? "Stage only — precheck + download + snapshot, no install"
        : "Full upgrade",
    ],
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

function TaskList({
  tasks,
  jobState,
}: {
  tasks: UpgradeTask[];
  jobState: JobState;
}) {
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
              jobState={jobState}
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
  jobState,
}: {
  task: UpgradeTask;
  pairKey: string;
  isPairLead: boolean;
  isPaired: boolean;
  jobState: JobState;
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
        <td className="px-4 py-2 text-zinc-500 text-xs">
          {isPaired ? (
            <span
              title={`Grouping key: ${pairKey}`}
              className="rounded border border-blue-800/50 bg-blue-950/40 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-blue-300"
            >
              HA pair
            </span>
          ) : (
            "—"
          )}
        </td>
        <td className="px-4 py-2">
          <div className="flex items-center gap-1.5 flex-wrap">
            <TaskPhaseBadge phase={t.phase} />
            <PhaseSubstep task={t} />
          </div>
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
          <TaskActionButtons task={t} jobState={jobState} />
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
  // Four blocks: at-a-glance summary, override-history banner (if
  // any), completed-phase chips, precheck results, recent activity
  // log. Order picked to put the most-actionable info first — what
  // the orchestrator is doing right now and what it just found.
  const log = readLog(t.progress);
  const completedPhases = readCompletedPhases(t.progress);
  const failingChecks = readFailingChecks(t.progress);
  const overrides = readOverrides(t.progress);

  return (
    <div className="grid gap-3 text-xs">
      <CurrentPhaseExplainer phase={t.phase} failingChecks={failingChecks} />

      {overrides.length > 0 && <OverrideHistory overrides={overrides} />}

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

      {t.precheck && (
        <PrecheckResultsTable
          precheck={t.precheck}
          deviceId={t.device_id}
          deviceName={t.device_name}
        />
      )}

      <SnapshotsSection task={t} />

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

interface OverrideEntry {
  phase: string;
  by: string;
  at: string;
}

function OverrideHistory({ overrides }: { overrides: OverrideEntry[] }) {
  // Sits above the precheck table because the question this answers
  // ("why is the device past the gate when the precheck still shows
  // FAIL?") is settled by knowing somebody overrode it, and by whom.
  return (
    <div className="px-2 py-1.5 rounded bg-amber-950/30 border border-amber-900/30 text-amber-200">
      <div className="text-[10px] uppercase tracking-wider text-amber-400/80 mb-0.5">
        Operator override{overrides.length > 1 ? "s" : ""}
      </div>
      <ul className="space-y-0.5 text-[11px]">
        {overrides.map((o, i) => (
          <li key={i}>
            <span className="font-mono text-amber-300">
              {prettyPhase(o.phase)}
            </span>{" "}
            overridden by{" "}
            <span className="font-semibold text-amber-100">{o.by}</span>{" "}
            <span className="text-amber-400/70">at {fmtTime(o.at)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function prettyPhase(phase: string): string {
  return phase.replace(/_/g, " ");
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
        Pending — not started yet. Upgrades run a few at a time; this task is
        queued behind others. (For an HA pair, the peer member upgrades first.)
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

function PrecheckResultsTable({
  precheck,
  deviceId,
  deviceName,
}: {
  precheck: PrecheckSummary;
  deviceId: number;
  deviceName: string;
}) {
  const [cleanup, setCleanup] = useState(false);
  // A failing/warning disk-space check is the cue to offer cleanup inline.
  const diskCheck = precheck.checks.find(
    (c) =>
      c.name.toLowerCase().includes("disk") &&
      String(c.severity).toLowerCase() !== "pass",
  );
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
      {diskCheck && (
        <div className="mb-2 flex flex-wrap items-center gap-2 rounded border border-amber-800/40 bg-amber-950/20 px-2 py-1 text-[11px]">
          <span className="text-amber-200">
            Low disk space flagged
            {diskCheck.reason ? ` — ${diskCheck.reason}` : ""}.
          </span>
          <button
            type="button"
            onClick={() => setCleanup(true)}
            className="text-blue-400 underline hover:text-blue-300"
          >
            Free up disk space
          </button>
        </div>
      )}
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
      {cleanup && (
        <DiskCleanupModal
          deviceId={deviceId}
          deviceName={deviceName}
          onClose={() => setCleanup(false)}
        />
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

function readOverrides(
  progress: Record<string, unknown> | null,
): OverrideEntry[] {
  // Filter shape defensively — the field is operator-edited downstream
  // (well, written by our own override route, but JSON blobs in
  // progress.* drift over time), so don't trust shape blindly.
  if (!progress) return [];
  const raw = progress.overrides;
  if (!Array.isArray(raw)) return [];
  return raw.filter((x): x is OverrideEntry => {
    if (typeof x !== "object" || x === null) return false;
    const o = x as Partial<OverrideEntry>;
    return (
      typeof o.phase === "string" &&
      typeof o.by === "string" &&
      typeof o.at === "string"
    );
  });
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

const TERMINAL_JOB_STATES: JobState[] = ["completed", "failed", "aborted"];

function TaskActionButtons({
  task,
  jobState,
}: {
  task: UpgradeTask;
  jobState: JobState;
}) {
  // Hooks declared BEFORE the conditional return so the rules-of-hooks
  // invariant holds: every render call hits the same hook sequence.
  // (React Query's useMutation registers an effect; bailing early
  // before declaring them would change the hook count between renders
  // and trigger a warning.)
  const qc = useQueryClient();
  // `await` so `isPending` remains true through the refetch — gives
  // the operator a visible "still working" window between clicking
  // and the new task phase appearing in the polled payload. Without
  // this, the spinner flicks off the instant the POST returns, even
  // though the orchestrator-side state hasn't visibly advanced yet.
  const invalidate = async () => {
    await qc.invalidateQueries({ queryKey: ["upgrade-job", task.job_id] });
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

  // A FAILED task is retryable even on a FAILED job: retry resumes from the
  // last completed marker AND un-fails the job server-side (the recovery path
  // after a driver crash or phase failure). COMPLETED / ABORTED stay terminal.
  if (
    task.phase === "failed" &&
    jobState !== "completed" &&
    jobState !== "aborted"
  ) {
    return (
      <Button onClick={() => retryM.mutate()} disabled={retryM.isPending}>
        <BusyLabel
          busy={retryM.isPending}
          busyLabel="Retrying…"
          idleLabel="Retry"
        />
      </Button>
    );
  }

  // If the job itself is terminal, the orchestrator won't act on any
  // click — `drive_pair` short-circuits on terminal-job state. Render
  // a placeholder so the operator doesn't get stuck pressing buttons
  // that silently no-op. We hit this on Job #1 when the worker pod
  // restarted while a task was parked at AWAITING_PRECHECK_OVERRIDE:
  // job went FAILED, task stayed parked, buttons looked actionable
  // but every click was swallowed. (Exception: a FAILED task on a FAILED
  // job is handled just above — retry un-fails the job and resumes.)
  if (TERMINAL_JOB_STATES.includes(jobState)) {
    return (
      <span
        className="text-[11px] text-zinc-500 italic"
        title={`Job is ${jobState}. Delete this job and start a new one to act on this device.`}
      >
        Job {jobState} — no actions
      </span>
    );
  }

  if (PARKED_CONFIRM.includes(task.phase)) {
    return (
      <Button
        variant="primary"
        onClick={() => confirmM.mutate()}
        disabled={confirmM.isPending}
      >
        <BusyLabel
          busy={confirmM.isPending}
          busyLabel="Confirming…"
          idleLabel="Confirm"
        />
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
          <BusyLabel
            busy={rerunM.isPending}
            busyLabel="Re-running…"
            idleLabel="Re-run check"
          />
        </Button>
        <Button
          variant="danger"
          onClick={() => overrideM.mutate()}
          disabled={busy}
        >
          <BusyLabel
            busy={overrideM.isPending}
            busyLabel="Overriding…"
            idleLabel="Override + proceed"
          />
        </Button>
      </div>
    );
  }
  if (task.phase === "failed") {
    return (
      <Button onClick={() => retryM.mutate()} disabled={retryM.isPending}>
        <BusyLabel
          busy={retryM.isPending}
          busyLabel="Retrying…"
          idleLabel="Retry"
        />
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

// Human-readable labels for the orchestrator's `progress.phase_substep`
// values. Unknown keys fall back to underscores→spaces so a new backend
// substep still renders sanely without a frontend deploy.
const SUBSTEP_LABELS: Record<string, string> = {
  installing: "installing",
  rebooting: "rebooting",
  verifying: "verifying",
  resuming_ha: "resuming HA",
  waiting_for_passive: "waiting for HA sync",
  partner_upgrading: "partner upgrading",
};

/**
 * Renders the within-phase sub-step + install/download progress % next
 * to the phase badge, e.g. "· rebooting" or "· installing 47%".
 *
 * Reads straight from task.progress (phase_substep / install_progress /
 * download_progress) — the orchestrator writes them there, no dedicated
 * TaskRead fields needed. Renders nothing when there's no substep and no
 * relevant percent for the current phase.
 */
function PhaseSubstep({ task: t }: { task: UpgradeTask }) {
  const substep = readSubstep(t.progress);
  const pct = readPhasePercent(t.phase, t.progress);

  if (!substep && pct == null) return null;

  const label = substep
    ? SUBSTEP_LABELS[substep] ?? substep.replace(/_/g, " ")
    : null;

  return (
    <span className="text-[10px] text-zinc-400 inline-flex items-center gap-1">
      <span className="text-zinc-600">·</span>
      {label && <span>{label}</span>}
      {pct != null && (
        <span className="font-mono text-blue-300">{pct}%</span>
      )}
    </span>
  );
}

function readSubstep(progress: Record<string, unknown> | null): string | null {
  const v = progress?.phase_substep;
  return typeof v === "string" && v.length > 0 ? v : null;
}

// install_progress applies to the UPGRADE_* phases; download_progress to
// DOWNLOADING_IMAGE. Returns the relevant percent for the current phase,
// or null when there's nothing meaningful to show.
function readPhasePercent(
  phase: TaskPhase,
  progress: Record<string, unknown> | null,
): number | null {
  if (!progress) return null;
  const key =
    phase === "downloading_image"
      ? "download_progress"
      : phase === "upgrade_secondary" || phase === "upgrade_primary"
        ? "install_progress"
        : null;
  if (!key) return null;
  const v = progress[key];
  if (typeof v !== "number" || Number.isNaN(v)) return null;
  if (v < 0 || v > 100) return null;
  return v;
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

// ============================================================
// Snapshots: View pre / View post / Compare diff
// ============================================================

/**
 * Section rendered in TaskExpandedDetail when the task has snapshot
 * data. Surfaces three actions: View Pre, View Post, Compare Diff.
 * Each opens a side-panel overlay; only one is open at a time.
 *
 * Renders nothing when the task has no snapshot ids — keeps the
 * expanded panel quiet for pending / pre-snapshot tasks.
 */
function SnapshotsSection({ task: t }: { task: UpgradeTask }) {
  const [open, setOpen] = useState<
    | { kind: "snapshot"; id: number; label: string }
    | { kind: "diff"; id: number }
    | null
  >(null);

  if (
    t.pre_snapshot_id == null &&
    t.post_snapshot_id == null &&
    t.snapshot_diff_id == null
  ) {
    return null;
  }

  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-zinc-500 mb-1">
        Snapshots
      </div>
      <div className="flex flex-wrap gap-2 text-[11px]">
        {t.pre_snapshot_id != null && (
          <Button
            onClick={() =>
              setOpen({
                kind: "snapshot",
                id: t.pre_snapshot_id!,
                label: "Pre-upgrade",
              })
            }
          >
            View pre
          </Button>
        )}
        {t.post_snapshot_id != null && (
          <Button
            onClick={() =>
              setOpen({
                kind: "snapshot",
                id: t.post_snapshot_id!,
                label: "Post-upgrade",
              })
            }
          >
            View post
          </Button>
        )}
        {t.snapshot_diff_id != null && (
          <Button
            variant={t.snapshot_diff_all_passed ? "default" : "danger"}
            onClick={() =>
              setOpen({ kind: "diff", id: t.snapshot_diff_id! })
            }
            title={
              t.snapshot_diff_all_passed
                ? "All snapshot areas match between pre and post."
                : "Some areas changed between pre and post — click to review."
            }
          >
            Compare diff
            {t.snapshot_diff_all_passed === true && " ✓"}
            {t.snapshot_diff_all_passed === false &&
              t.snapshot_diff_failing_areas &&
              t.snapshot_diff_failing_areas.length > 0 && (
                <span className="ml-1 text-rose-300">
                  ⚠ {t.snapshot_diff_failing_areas.length}
                </span>
              )}
          </Button>
        )}
      </div>

      {open?.kind === "snapshot" && (
        <SnapshotSidePanel
          snapshotId={open.id}
          label={open.label}
          onClose={() => setOpen(null)}
        />
      )}
      {open?.kind === "diff" && (
        <SnapshotDiffSidePanel
          diffId={open.id}
          onClose={() => setOpen(null)}
        />
      )}
    </div>
  );
}

/**
 * Modal-style side panel — fixed right slab, click backdrop to close.
 * Used for both snapshot and diff views; shared shell to keep the
 * behavior identical regardless of payload kind.
 */
function SidePanelShell({
  title,
  subtitle,
  onClose,
  children,
}: {
  title: string;
  subtitle?: string;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl h-full bg-zinc-950 border-l border-zinc-800 overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 bg-zinc-950 border-b border-zinc-800 px-4 py-3 flex items-start justify-between">
          <div>
            <div className="text-sm font-semibold text-zinc-100">{title}</div>
            {subtitle && (
              <div className="text-[11px] text-zinc-500 mt-0.5">{subtitle}</div>
            )}
          </div>
          <button
            onClick={onClose}
            className="text-zinc-500 hover:text-zinc-200 text-sm"
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <div className="p-4">{children}</div>
      </div>
    </div>
  );
}

function SnapshotSidePanel({
  snapshotId,
  label,
  onClose,
}: {
  snapshotId: number;
  label: string;
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["snapshot", snapshotId],
    queryFn: () => api.getSnapshot(snapshotId),
  });

  const snap = q.data;
  const subtitle = snap
    ? `${snap.device_name} · ${snap.pan_os_version ?? "unknown version"} · ${fmtTime(
        snap.taken_at,
      )}`
    : "Loading…";

  return (
    <SidePanelShell
      title={`${label} snapshot`}
      subtitle={subtitle}
      onClose={onClose}
    >
      {q.isLoading && (
        <div className="text-xs text-zinc-500">Loading snapshot…</div>
      )}
      {q.error && (
        <div className="text-xs text-rose-400">
          Failed to load snapshot: {(q.error as Error).message}
        </div>
      )}
      {snap && snap.error && (
        <div className="text-xs p-2 rounded bg-rose-950/40 border border-rose-900/40 text-rose-300 mb-3">
          Capture errored: <span className="font-mono">{snap.error}</span>
        </div>
      )}
      {snap && Object.keys(snap.data).length === 0 && (
        <div className="text-xs text-zinc-500 italic">
          No areas captured (the snapshot row exists but the
          panos-upgrade-assurance runner returned no data — usually
          because the device was unreachable at capture time).
        </div>
      )}
      {snap && (
        <ErrorBoundary title="Couldn't render this snapshot — close the panel to continue.">
          <div className="space-y-2">
            {Object.entries(snap.data ?? {}).map(([area, payload]) => (
              <AreaAccordion key={area} title={area} initiallyOpen={false}>
                <pre className="text-[11px] text-zinc-300 whitespace-pre-wrap break-all font-mono">
                  {jsonPretty(payload)}
                </pre>
              </AreaAccordion>
            ))}
          </div>
        </ErrorBoundary>
      )}
    </SidePanelShell>
  );
}

function SnapshotDiffSidePanel({
  diffId,
  onClose,
}: {
  diffId: number;
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["snapshot-diff", diffId],
    queryFn: () => api.getSnapshotDiff(diffId),
  });

  const diff = q.data;
  const subtitle = diff
    ? `${diff.left_version ?? "?"} → ${diff.right_version ?? "?"} · ${fmtTime(
        diff.computed_at,
      )}`
    : "Loading…";

  return (
    <SidePanelShell
      title="Snapshot diff"
      subtitle={subtitle}
      onClose={onClose}
    >
      {q.isLoading && (
        <div className="text-xs text-zinc-500">Loading diff…</div>
      )}
      {q.error && (
        <div className="text-xs text-rose-400">
          Failed to load diff: {(q.error as Error).message}
        </div>
      )}
      {diff && (
        <ErrorBoundary title="Couldn't render this diff — close the panel to continue.">
          <DiffReport diff={diff} />
        </ErrorBoundary>
      )}
    </SidePanelShell>
  );
}

function DiffReport({ diff }: { diff: SnapshotDiff }) {
  // Headline summary then per-area accordion. Failing areas open by
  // default — operator's eyes go there first.
  const report = diff.report ?? {};

  // The backend persists a compare-level failure as {"_error": "..."} when
  // the panos-upgrade-assurance compare raised before producing any
  // per-area report. Surface it as a banner rather than a fake "area".
  const compareError =
    typeof (report as Record<string, unknown>)._error === "string"
      ? ((report as Record<string, unknown>)._error as string)
      : null;

  // Defensive partition. An area body can legitimately be null — e.g.
  // session_stats compared without a threshold spec returns null from the
  // library — and `null.passed` would otherwise throw. Pre-ErrorBoundary
  // that blanked the entire console ("Compare diff → black screen"); we now
  // skip non-object bodies and note them instead of crashing.
  const entries = Object.entries(report).filter(([k]) => k !== "_error");
  const renderable = entries.filter(
    ([, a]) => a != null && typeof a === "object",
  ) as [string, SnapshotDiffAreaReport][];
  const notComparable = entries
    .filter(([, a]) => a == null || typeof a !== "object")
    .map(([k]) => k);

  const failing = renderable.filter(([, a]) => !a.passed);
  const passing = renderable.filter(([, a]) => a.passed);

  if (compareError) {
    return (
      <div className="space-y-3 text-xs">
        <div className="px-3 py-2 rounded border bg-rose-950/40 border-rose-900/40 text-rose-200">
          <strong>The snapshot comparison could not be completed.</strong> The
          pre and post snapshots exist, but the compare step errored before
          producing a report.
          <pre className="mt-2 whitespace-pre-wrap break-all font-mono text-[11px] text-rose-300/90">
            {compareError}
          </pre>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-3 text-xs">
      <div
        className={`px-3 py-2 rounded border ${
          diff.all_passed
            ? "bg-emerald-950/40 border-emerald-900/40 text-emerald-200"
            : "bg-amber-950/40 border-amber-900/40 text-amber-200"
        }`}
      >
        {diff.all_passed ? (
          <strong>All {renderable.length} areas match.</strong>
        ) : (
          <>
            <strong>
              {failing.length} of {renderable.length} areas changed.
            </strong>{" "}
            Review each below and decide whether the change is expected for
            this upgrade (license bumps and content-DB updates usually are;
            session-table drift across a reboot is normal; routing table
            drift is worth a closer look).
          </>
        )}
      </div>

      {notComparable.length > 0 && (
        <div className="px-3 py-1.5 rounded border border-zinc-800 bg-zinc-900/40 text-[11px] text-zinc-400">
          Not compared:{" "}
          <span className="font-mono text-zinc-300">
            {notComparable.join(", ")}
          </span>{" "}
          — these areas don't produce a meaningful pre/post diff (e.g. session
          counters, which reset across a reboot).
        </div>
      )}

      {failing.length > 0 && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-amber-400/80">
            Changed
          </div>
          {failing.map(([area, report]) => (
            <DiffAreaAccordion key={area} area={area} report={report} startOpen />
          ))}
        </div>
      )}

      {passing.length > 0 && (
        <div className="space-y-2">
          <div className="text-[10px] uppercase tracking-wider text-emerald-400/80">
            Unchanged
          </div>
          {passing.map(([area, report]) => (
            <DiffAreaAccordion
              key={area}
              area={area}
              report={report}
              startOpen={false}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ---- snapshot-diff rendering helpers ----
//
// panos-upgrade-assurance emits a recursive comparison tree. Every node is
// {missing:{passed,missing_keys[]}, added:{passed,added_keys[]},
//  changed:{passed,changed_raw{}}, passed}, and `changed_raw` maps a key to
// either a {left_snap,right_snap} leaf (a value that differed) or another
// node (a nested dict). We flatten the `changed` tree into one row per leaf
// so the operator sees "entry › field: before → after" instead of having to
// read nested JSON. Validated against real arp_table/routes/nics/ip_sec_tunnels
// diffs (nics entries are direct leaves; the others nest one level).

type DiffChangeRow = {
  path: string[];
  before?: unknown;
  after?: unknown;
  kind: "changed" | "added" | "removed";
};

function isDiffLeaf(
  v: unknown,
): v is { left_snap?: unknown; right_snap?: unknown } {
  return (
    typeof v === "object" &&
    v !== null &&
    ("left_snap" in v || "right_snap" in v)
  );
}

function collectDiffChanges(
  node: unknown,
  prefix: string[],
  out: DiffChangeRow[],
): void {
  if (isDiffLeaf(node)) {
    out.push({
      path: prefix,
      before: node.left_snap,
      after: node.right_snap,
      kind: "changed",
    });
    return;
  }
  if (typeof node !== "object" || node === null) {
    // Unexpected scalar where a node was expected — surface as the new value.
    out.push({ path: prefix, after: node, kind: "changed" });
    return;
  }
  const n = node as Record<string, any>;
  const changedRaw = n.changed?.changed_raw;
  if (changedRaw && typeof changedRaw === "object") {
    for (const [k, v] of Object.entries(changedRaw)) {
      collectDiffChanges(v, [...prefix, k], out);
    }
  }
  const addedKeys = n.added?.added_keys;
  if (Array.isArray(addedKeys)) {
    for (const k of addedKeys) out.push({ path: [...prefix, String(k)], kind: "added" });
  }
  const missingKeys = n.missing?.missing_keys;
  if (Array.isArray(missingKeys)) {
    for (const k of missingKeys) out.push({ path: [...prefix, String(k)], kind: "removed" });
  }
}

function fmtDiffValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string" || typeof v === "number" || typeof v === "boolean") {
    return String(v);
  }
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

function DiffAreaAccordion({
  area,
  report,
  startOpen,
}: {
  area: string;
  report: SnapshotDiffAreaReport;
  startOpen: boolean;
}) {
  const [showRaw, setShowRaw] = useState(false);

  // Flatten the changed tree to leaf rows; list added/removed entry keys.
  const changedRaw = report.changed?.changed_raw ?? {};
  const changedEntryCount = Object.keys(changedRaw).length;
  const changeRows: DiffChangeRow[] = [];
  for (const [entryId, entryNode] of Object.entries(changedRaw)) {
    collectDiffChanges(entryNode, [entryId], changeRows);
  }
  const addedEntries = report.added?.added_keys ?? [];
  const removedEntries = report.missing?.missing_keys ?? [];

  const summary = report.passed
    ? "no change"
    : [
        changedEntryCount && `${changedEntryCount} changed`,
        addedEntries.length && `${addedEntries.length} added`,
        removedEntries.length && `${removedEntries.length} removed`,
      ]
        .filter(Boolean)
        .join(", ") || "see details";

  return (
    <AreaAccordion
      title={area}
      titleSuffix={
        <span
          className={`text-[10px] uppercase tracking-wider ${
            report.passed ? "text-emerald-400/80" : "text-amber-400/80"
          }`}
        >
          {summary}
        </span>
      }
      initiallyOpen={startOpen}
    >
      <div className="space-y-3">
        {changeRows.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-amber-400/80 mb-1">
              Changed ({changedEntryCount})
            </div>
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-[11px]">
                <thead>
                  <tr className="text-left text-zinc-500">
                    <th className="font-medium pr-4 pb-1">Item</th>
                    <th className="font-medium pr-4 pb-1">Before</th>
                    <th className="font-medium pb-1">After</th>
                  </tr>
                </thead>
                <tbody className="font-mono">
                  {changeRows.map((r, i) => (
                    <tr key={i} className="border-t border-zinc-800/60">
                      <td className="pr-4 py-1 align-top text-zinc-300 break-all">
                        {r.path.join(" › ")}
                        {r.kind === "added" && (
                          <span className="ml-1 text-emerald-400/70">(added)</span>
                        )}
                        {r.kind === "removed" && (
                          <span className="ml-1 text-rose-400/70">(removed)</span>
                        )}
                      </td>
                      <td className="pr-4 py-1 align-top text-zinc-400 break-all">
                        {r.kind === "added" ? "—" : fmtDiffValue(r.before)}
                      </td>
                      <td className="py-1 align-top text-amber-200 break-all">
                        {r.kind === "removed" ? "—" : fmtDiffValue(r.after)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {addedEntries.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-emerald-400/80 mb-1">
              Added ({addedEntries.length})
            </div>
            <ul className="space-y-0.5 font-mono text-[11px] text-emerald-200">
              {addedEntries.map((k, i) => (
                <li key={i} className="break-all">
                  + {String(k)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {removedEntries.length > 0 && (
          <div>
            <div className="text-[10px] uppercase tracking-wider text-rose-400/80 mb-1">
              Removed ({removedEntries.length})
            </div>
            <ul className="space-y-0.5 font-mono text-[11px] text-rose-200">
              {removedEntries.map((k, i) => (
                <li key={i} className="break-all">
                  − {String(k)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {report.passed && (
          <div className="text-[11px] text-emerald-300 italic">
            All items match between the two snapshots.
          </div>
        )}

        {/* Power-user escape hatch: the raw comparison tree. */}
        <div>
          <button
            onClick={() => setShowRaw((v) => !v)}
            className="text-[10px] uppercase tracking-wider text-zinc-500 hover:text-zinc-300"
          >
            {showRaw ? "▾ Hide raw" : "▸ Show raw"}
          </button>
          {showRaw && (
            <pre className="mt-1 text-[11px] text-zinc-400 whitespace-pre-wrap break-all font-mono">
              {jsonPretty(report)}
            </pre>
          )}
        </div>
      </div>
    </AreaAccordion>
  );
}

function AreaAccordion({
  title,
  titleSuffix,
  initiallyOpen,
  children,
}: {
  title: string;
  titleSuffix?: ReactNode;
  initiallyOpen: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(initiallyOpen);
  return (
    <div className="border border-zinc-800/60 rounded">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-2 py-1.5 text-left hover:bg-zinc-900/60"
      >
        <span className="flex items-center gap-2">
          <span className="text-zinc-500">{open ? "▾" : "▸"}</span>
          <span className="font-mono text-zinc-200 text-[11px]">{title}</span>
        </span>
        {titleSuffix}
      </button>
      {open && (
        <div className="px-3 py-2 border-t border-zinc-800/60">{children}</div>
      )}
    </div>
  );
}

function jsonPretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
