import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle2,
  Loader2,
  MinusCircle,
  PauseCircle,
  Rocket,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";
import type { ActiveTask } from "@/lib/types";

type Job = { id: number; name: string; state: string; target_version: string; created_at: string };

type LatestPrecheck = {
  id: number;
  ran_at: string;
  overall_severity: "pass" | "warn" | "fail" | "skip";
  pass_count: number;
  warn_count: number;
  fail_count: number;
  skip_count: number;
};

type Device = {
  id: number;
  name: string;
  current_version: string | null;
  connected: boolean;
  latest_precheck: LatestPrecheck | null;
};

export default function Dashboard() {
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: () => api<Job[]>("/api/jobs") });
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => api<Device[]>("/api/devices"),
    refetchInterval: 30_000,
  });
  const activeTasks = useQuery({
    queryKey: ["active-tasks"],
    queryFn: () => api<ActiveTask[]>("/api/jobs/active-tasks"),
    refetchInterval: 5000,
  });

  const fleet = useMemo(() => {
    const list = devices.data ?? [];
    let pass = 0, warn = 0, fail = 0, never = 0;
    for (const d of list) {
      if (!d.latest_precheck) { never += 1; continue; }
      const s = d.latest_precheck.overall_severity;
      if (s === "pass" || s === "skip") pass += 1;
      else if (s === "warn") warn += 1;
      else fail += 1;
    }
    return { total: list.length, pass, warn, fail, never };
  }, [devices.data]);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card title="Devices" value={devices.data?.length ?? "—"} />
        <Card title="Jobs (all)" value={jobs.data?.length ?? "—"} />
        <UpgradesInFlightTile tasks={activeTasks.data ?? []} />
      </div>

      <section>
        <h2 className="mb-2 flex items-center gap-2 text-lg font-semibold">
          <ShieldCheck className="h-4 w-4 text-indigo-400" /> Fleet pre-check status
        </h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <FleetTile
            label="Last pass"
            count={fleet.pass}
            total={fleet.total}
            color="emerald"
            icon={<CheckCircle2 className="h-5 w-5" />}
            href="/devices?precheck=pass,skip"
          />
          <FleetTile
            label="Warning"
            count={fleet.warn}
            total={fleet.total}
            color="amber"
            icon={<AlertTriangle className="h-5 w-5" />}
            href="/devices?precheck=warn"
          />
          <FleetTile
            label="Failing"
            count={fleet.fail}
            total={fleet.total}
            color="red"
            icon={<XCircle className="h-5 w-5" />}
            href="/devices?precheck=fail"
          />
          <FleetTile
            label="Never checked"
            count={fleet.never}
            total={fleet.total}
            color="slate"
            icon={<MinusCircle className="h-5 w-5" />}
            href="/devices?precheck=none"
          />
        </div>
        <Link to="/devices" className="mt-2 inline-block text-xs text-indigo-400 hover:underline">
          → View / pre-check all devices
        </Link>
      </section>

      <section>
        <h2 className="mb-2 text-lg font-semibold">Recent jobs</h2>
        <div className="rounded-lg border border-slate-800">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-slate-400">
              <tr>
                <th className="px-3 py-2 text-left">Name</th>
                <th className="px-3 py-2 text-left">Target</th>
                <th className="px-3 py-2 text-left">State</th>
                <th className="px-3 py-2 text-left">Created</th>
              </tr>
            </thead>
            <tbody>
              {(jobs.data ?? []).slice(0, 10).map((j) => (
                <tr key={j.id} className="border-t border-slate-800">
                  <td className="px-3 py-2">{j.name}</td>
                  <td className="px-3 py-2">{j.target_version}</td>
                  <td className="px-3 py-2">{j.state}</td>
                  <td className="px-3 py-2 text-slate-400">{new Date(j.created_at).toLocaleString()}</td>
                </tr>
              ))}
              {(jobs.data ?? []).length === 0 && (
                <tr><td className="px-3 py-6 text-center text-slate-500" colSpan={4}>No jobs yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

/**
 * Live upgrades-in-flight tile. Replaces the dumb "in progress = count of
 * jobs in running state" tile with a per-device view: how many devices are
 * actively being upgraded right now, and how many of those are parked on a
 * human-confirmation gate. The latter is the operationally important number
 * — that's where the operator needs to step in.
 */
function UpgradesInFlightTile({ tasks }: { tasks: ActiveTask[] }) {
  const awaiting = tasks.filter((t) => t.awaiting);
  const working = tasks.filter((t) => !t.awaiting);
  const jobIds = Array.from(new Set(tasks.map((t) => t.job_id)));

  // Pick a representative recent job to deep-link into. When there's only
  // one job in flight, that's the link target; otherwise we just go to /jobs.
  const linkTarget = jobIds.length === 1 ? `/jobs/${jobIds[0]}` : "/jobs";

  return (
    <Link
      to={linkTarget}
      className="block rounded-lg border border-slate-800 bg-slate-900 p-4 transition-colors hover:bg-slate-800/50"
    >
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <Rocket className="h-4 w-4 text-amber-400" />
        Upgrades in flight
      </div>
      <div className="mt-1 flex items-baseline gap-3">
        <div className="text-3xl font-semibold">{tasks.length}</div>
        <div className="text-xs text-slate-500">
          devices · {jobIds.length} job{jobIds.length !== 1 ? "s" : ""}
        </div>
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-xs">
        {working.length > 0 && (
          <span className="inline-flex items-center gap-1 text-amber-300">
            <Loader2 className="h-3 w-3 animate-spin" />
            {working.length} running
          </span>
        )}
        {awaiting.length > 0 && (
          <span className="inline-flex items-center gap-1 text-sky-300">
            <PauseCircle className="h-3 w-3" />
            {awaiting.length} awaiting confirmation
          </span>
        )}
        {tasks.length === 0 && <span className="text-slate-500">No active upgrades.</span>}
      </div>
    </Link>
  );
}

function Card({ title, value }: { title: string; value: number | string }) {
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="text-sm text-slate-400">{title}</div>
      <div className="mt-1 text-3xl font-semibold">{value}</div>
    </div>
  );
}

function FleetTile({
  label, count, total, color, icon, href,
}: {
  label: string;
  count: number;
  total: number;
  color: "emerald" | "amber" | "red" | "slate";
  icon: React.ReactNode;
  href?: string;
}) {
  const pct = total ? Math.round((count / total) * 100) : 0;
  const text = {
    emerald: "text-emerald-400",
    amber: "text-amber-400",
    red: "text-red-400",
    slate: "text-slate-400",
  }[color];
  const inner = (
    <>
      <div className={`flex items-center gap-2 text-sm ${text}`}>{icon}{label}</div>
      <div className="mt-2 flex items-baseline gap-2">
        <div className={`text-3xl font-semibold ${text}`}>{count}</div>
        <div className="text-xs text-slate-500">of {total} ({pct}%)</div>
      </div>
    </>
  );
  const cls = "block rounded-lg border border-slate-800 bg-slate-900 p-4";
  if (href) {
    return (
      <Link to={href} className={`${cls} transition-colors hover:bg-slate-800/50`}>
        {inner}
      </Link>
    );
  }
  return <div className={cls}>{inner}</div>;
}
