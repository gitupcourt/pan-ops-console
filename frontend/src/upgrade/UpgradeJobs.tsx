import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useMemo } from "react";
import { NavLink, useSearchParams } from "react-router-dom";

import { api, JobState, UpgradeJob } from "../api";
import { Button, Card, CardHeader, Empty } from "../core/ui/ui";
import { JobForm } from "./JobForm";

/**
 * The /upgrade landing page — list of jobs (+ the new-job form when
 * launched from /inventory).
 *
 * Job creation is initiated from /inventory (phase 13c): operators
 * pick the devices first (with all the DG/TS filter + multi-select
 * tooling from the inventory page) and then hand off to /upgrade
 * with `?new=true&devices=…` to fill in version + image + checks +
 * confirmation gates. Earlier iterations also had a "New job" button
 * on this page, but the duplicate entry point was clutter — single
 * entry point = single mental model ("pick devices, then upgrade
 * them").
 *
 * The detail row → /upgrade/jobs/{id} for per-task progress and
 * operator actions (confirm / override / retry).
 *
 * State filtering: a future enhancement might add "show only RUNNING"
 * etc. For now we show everything sorted newest first.
 */
export default function UpgradeJobs() {
  const qc = useQueryClient();
  const [params, setParams] = useSearchParams();
  const jobsQ = useQuery({
    queryKey: ["upgrade-jobs"],
    queryFn: api.listUpgradeJobs,
    // Refetch every 5s while the list is visible — jobs change state
    // as the orchestrator works through them; without this the
    // operator has to manually refresh to see progress.
    refetchInterval: 5000,
  });

  // Hand-off contract from /inventory (phase 13c):
  //   /upgrade?new=true&devices=1,2,3
  // → render the new-job form with those device IDs pre-checked.
  // Closing the form strips the URL params so navigating away + back
  // doesn't re-pop the selection.
  const initialDeviceIds = useMemo(() => {
    const raw = params.get("devices");
    if (!raw) return [];
    return raw
      .split(",")
      .map((s) => Number(s.trim()))
      .filter((n) => Number.isFinite(n) && n > 0);
  }, [params]);
  const creating =
    params.get("new") === "true" || initialDeviceIds.length > 0;

  const closeForm = () => setParams(new URLSearchParams());

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Upgrade jobs"
          description={
            creating
              ? "Configure the new job below. Cancel to return to the job list."
              : "Bulk upgrades across one or more firewalls, with HA pair awareness and confirmation gates at each major step. Pick devices on Inventory, or Start Job to choose them here."
          }
          action={
            creating ? (
              <Button onClick={closeForm}>← Back to jobs</Button>
            ) : (
              <div className="flex items-center gap-3">
                <Button
                  variant="primary"
                  onClick={() => setParams(new URLSearchParams({ new: "true" }))}
                  title="Open the job form with an empty device picker"
                >
                  + Start Job
                </Button>
                <NavLink
                  to="/inventory"
                  className="text-xs text-blue-400 hover:text-blue-300"
                >
                  Pick from Inventory →
                </NavLink>
                <NavLink
                  to="/upgrade/precheck-sets"
                  className="text-xs text-blue-400 hover:text-blue-300"
                >
                  Manage precheck sets
                </NavLink>
              </div>
            )
          }
        />

        {creating ? (
          <JobForm
            initialDeviceIds={initialDeviceIds}
            onDone={() => {
              closeForm();
              qc.invalidateQueries({ queryKey: ["upgrade-jobs"] });
            }}
          />
        ) : (
          <JobList jobs={jobsQ.data ?? []} loading={jobsQ.isLoading} />
        )}
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
        No upgrade jobs yet. Head to{" "}
        <NavLink
          to="/inventory"
          className="text-blue-400 hover:text-blue-300 underline"
        >
          Inventory
        </NavLink>
        , pick the devices you want to upgrade, and click{" "}
        <span className="text-zinc-300">Upgrade selected</span>.
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
