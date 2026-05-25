import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  CheckCircle2,
  Circle,
  Loader2,
  PauseCircle,
  Rocket,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";

type Task = {
  id: number;
  device_id: number;
  device_name: string | null;
  ha_pair_key: string;
  phase: string;
  progress: Record<string, unknown> | null;
};

// Phase → fraction-of-job-complete. Used to render a coarse percentage on
// the Jobs list without having to compute it server-side. Values are tuned so
// the bar moves visibly at each meaningful checkpoint rather than sitting at
// 5% for the entire install (which can be 20+ minutes). The download/install
// percentages are then interpolated within their slice.
const PHASE_WEIGHT: Record<string, number> = {
  pending: 0.0,
  precheck: 0.05,
  awaiting_precheck_override: 0.07,
  snapshot: 0.1,
  downloading_image: 0.15,
  suspend_secondary: 0.35,
  upgrade_secondary: 0.4,
  awaiting_reboot_confirm: 0.55,
  postcheck_secondary: 0.55,
  awaiting_failover_confirm: 0.6,
  failover: 0.65,
  awaiting_primary_upgrade_confirm: 0.65,
  upgrade_primary: 0.8,
  postcheck_primary: 0.9,
  awaiting_postcheck_override: 0.92,
  failback: 0.95,
  done: 1.0,
  failed: 1.0,
  aborted: 1.0,
};

function taskCompletionPct(t: Task): number {
  const base = PHASE_WEIGHT[t.phase] ?? 0;
  // Within downloading_image / upgrade_* phases, interpolate using the
  // device-reported progress so the bar advances during a slow download.
  const prog = t.progress ?? {};
  if (t.phase === "downloading_image" && typeof prog.download_progress === "number") {
    // download_progress: 0..100 spans the 0.10 → 0.15 slice.
    const span = 0.15 - 0.10;
    return Math.min(1, 0.10 + (span * (prog.download_progress as number)) / 100);
  }
  if (t.phase === "upgrade_secondary" && typeof prog.install_progress === "number") {
    const span = 0.55 - 0.35;
    return Math.min(1, 0.35 + (span * (prog.install_progress as number)) / 100);
  }
  if (t.phase === "upgrade_primary" && typeof prog.install_progress === "number") {
    const span = 0.90 - 0.65;
    return Math.min(1, 0.65 + (span * (prog.install_progress as number)) / 100);
  }
  return base;
}

function jobPercent(j: Job): number {
  if (j.tasks.length === 0) return 0;
  const sum = j.tasks.reduce((acc, t) => acc + taskCompletionPct(t), 0);
  return Math.round((sum / j.tasks.length) * 100);
}

type Job = {
  id: number;
  name: string;
  target_version: string;
  state: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  tasks: Task[];
};

export default function Jobs() {
  const { data, isLoading } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api<Job[]>("/api/jobs"),
    refetchInterval: 5000,
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Upgrade Jobs</h1>
        <Link
          to="/devices"
          className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 px-3 py-2 text-sm text-slate-300 hover:bg-slate-800"
        >
          <Rocket className="h-4 w-4" />
          New job → start from Devices
        </Link>
      </div>

      {isLoading && <div className="text-slate-400">Loading…</div>}

      <div className="rounded-lg border border-slate-800 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-3 py-2 text-left">Name</th>
              <th className="px-3 py-2 text-left">Target</th>
              <th className="px-3 py-2 text-left">Devices</th>
              <th className="px-3 py-2 text-left">State</th>
              <th className="px-3 py-2 text-left">Progress</th>
              <th className="px-3 py-2 text-left">Created</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((j) => (
              <tr key={j.id} className="border-t border-slate-800 hover:bg-slate-900/50">
                <td className="px-3 py-2">
                  <Link to={`/jobs/${j.id}`} className="text-indigo-400 hover:underline">
                    {j.name}
                  </Link>
                </td>
                <td className="px-3 py-2 font-mono text-slate-300">{j.target_version}</td>
                <td className="px-3 py-2 text-slate-400">
                  {j.tasks.length} target{j.tasks.length !== 1 ? "s" : ""}
                  {j.tasks.some((t) => t.ha_pair_key.startsWith("pair-")) && " (HA)"}
                </td>
                <td className="px-3 py-2">
                  <StateBadge state={j.state} />
                </td>
                <td className="px-3 py-2">
                  <JobProgress j={j} />
                </td>
                <td className="px-3 py-2 text-slate-400">
                  {new Date(j.created_at).toLocaleString()}
                </td>
              </tr>
            ))}
            {(data ?? []).length === 0 && !isLoading && (
              <tr><td className="px-3 py-6 text-center text-slate-500" colSpan={6}>
                No jobs yet. Head to Devices, select firewalls, and click <span className="text-slate-300">Upgrade N</span>.
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function JobProgress({ j }: { j: Job }) {
  const pct = jobPercent(j);
  const terminal = j.state === "completed" || j.state === "failed" || j.state === "aborted";
  const color =
    j.state === "failed" ? "bg-red-500"
    : j.state === "aborted" ? "bg-slate-500"
    : j.state === "completed" ? "bg-emerald-500"
    : "bg-amber-500";
  return (
    <div className="flex items-center gap-2 min-w-[140px]">
      <div className="h-1.5 flex-1 overflow-hidden rounded bg-slate-800">
        <div className={`h-full ${color} ${terminal ? "" : "transition-all"}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="w-9 text-right text-xs tabular-nums text-slate-400">{pct}%</span>
    </div>
  );
}

function StateBadge({ state }: { state: string }) {
  const map: Record<string, { color: string; icon: React.ReactNode; text: string }> = {
    pending: { color: "text-slate-400", icon: <Circle className="h-3 w-3" />, text: "pending" },
    running: { color: "text-amber-400", icon: <Loader2 className="h-3 w-3 animate-spin" />, text: "running" },
    awaiting_confirmation: {
      color: "text-sky-400",
      icon: <PauseCircle className="h-3 w-3" />,
      text: "awaiting confirmation",
    },
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
