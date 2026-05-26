import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { NavLink } from "react-router-dom";

import { api, JobState, UpgradeJob } from "../api";
import { Button, Card, CardHeader, Empty } from "../core/ui/ui";
import { JobForm } from "./JobForm";

/**
 * The /upgrade landing page — list of jobs + inline create form.
 *
 * Two-column information density: each row shows the job's name,
 * target version, state badge, task progress, and timestamps. Click a
 * row → /upgrade/jobs/{id} for the detail view with per-task progress
 * and operator actions (confirm / override / retry).
 *
 * State filtering: a future enhancement might add "show only RUNNING"
 * etc. For now we show everything sorted newest first, and let the
 * operator scan visually — fits the operator's typical fleet size
 * (15-30 jobs in flight at most).
 */
export default function UpgradeJobs() {
  const qc = useQueryClient();
  const jobsQ = useQuery({
    queryKey: ["upgrade-jobs"],
    queryFn: api.listUpgradeJobs,
    // Refetch every 5s while the list is visible — jobs change state
    // as the orchestrator works through them; without this the
    // operator has to manually refresh to see progress.
    refetchInterval: 5000,
  });

  const [creating, setCreating] = useState(false);

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Upgrade jobs"
          description="Bulk upgrades across one or more firewalls, with HA pair awareness and confirmation gates at each major step."
          action={
            <Button variant="primary" onClick={() => setCreating((v) => !v)}>
              {creating ? "Cancel" : "New job"}
            </Button>
          }
        />

        {creating && (
          <JobForm
            onDone={() => {
              setCreating(false);
              qc.invalidateQueries({ queryKey: ["upgrade-jobs"] });
            }}
          />
        )}

        <JobList jobs={jobsQ.data ?? []} loading={jobsQ.isLoading} />
      </Card>
    </div>
  );
}

function JobList({
  jobs,
  loading,
}: {
  jobs: UpgradeJob[];
  loading: boolean;
}) {
  if (loading && jobs.length === 0) {
    return (
      <div className="p-8 text-center text-xs text-zinc-500">Loading…</div>
    );
  }
  if (jobs.length === 0) {
    return (
      <Empty>
        No upgrade jobs yet. Click <span className="text-zinc-300">New job</span>{" "}
        above to create one.
      </Empty>
    );
  }

  return (
    <table className="w-full text-sm">
      <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
        <tr>
          <th className="text-left px-4 py-2 font-medium">Name</th>
          <th className="text-left px-4 py-2 font-medium">Target</th>
          <th className="text-left px-4 py-2 font-medium">State</th>
          <th className="text-left px-4 py-2 font-medium">Devices</th>
          <th className="text-left px-4 py-2 font-medium">Started</th>
          <th className="text-left px-4 py-2 font-medium">Finished</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((j) => (
          <Fragment key={j.id}>
            <tr className="border-b border-zinc-800/50 hover:bg-zinc-900/40">
              <td className="px-4 py-2">
                <NavLink
                  to={`/upgrade/jobs/${j.id}`}
                  className="text-blue-400 hover:text-blue-300"
                >
                  {j.name}
                </NavLink>
              </td>
              <td className="px-4 py-2 text-zinc-300 tabular-nums">
                {j.target_version}
              </td>
              <td className="px-4 py-2">
                <JobStateBadge state={j.state} />
              </td>
              <td className="px-4 py-2 text-zinc-300 tabular-nums">
                {j.task_count}
              </td>
              <td className="px-4 py-2 text-xs text-zinc-500">
                {fmtTs(j.started_at)}
              </td>
              <td className="px-4 py-2 text-xs text-zinc-500">
                {fmtTs(j.finished_at)}
              </td>
            </tr>
          </Fragment>
        ))}
      </tbody>
    </table>
  );
}

export function JobStateBadge({ state }: { state: JobState }) {
  // Color the state badge so an operator scanning the list can
  // immediately spot what needs attention.
  const styles: Record<JobState, string> = {
    pending: "border-zinc-700 bg-zinc-800 text-zinc-300",
    running: "border-blue-700 bg-blue-900/40 text-blue-200",
    awaiting_confirmation: "border-amber-700 bg-amber-900/40 text-amber-200",
    completed: "border-emerald-700 bg-emerald-900/40 text-emerald-200",
    failed: "border-rose-700 bg-rose-900/40 text-rose-200",
    aborted: "border-zinc-700 bg-zinc-900/40 text-zinc-400",
  };
  const label = state.replace(/_/g, " ");
  return (
    <span
      className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide border ${styles[state]}`}
    >
      {label}
    </span>
  );
}

function fmtTs(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString();
}
