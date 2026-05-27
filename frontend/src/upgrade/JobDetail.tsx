import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, useParams } from "react-router-dom";

import {
  api,
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
            <tr
              key={t.id}
              className={`border-b border-zinc-800/50 ${
                idx === 0 && pair.length > 1
                  ? "border-t-2 border-t-zinc-700/60"
                  : ""
              }`}
            >
              <td className="px-4 py-2 text-zinc-100">{t.device_name}</td>
              <td className="px-4 py-2 text-zinc-500 text-xs font-mono">
                {pair.length > 1 ? key : "—"}
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
          )),
        )}
      </tbody>
    </table>
  );
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
    return (
      <Button
        variant="danger"
        onClick={() => overrideM.mutate()}
        disabled={overrideM.isPending}
      >
        {overrideM.isPending ? "Overriding…" : "Override + proceed"}
      </Button>
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
