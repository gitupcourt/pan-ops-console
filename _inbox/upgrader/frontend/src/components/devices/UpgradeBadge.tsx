import { Link } from "react-router-dom";
import { Loader2, PauseCircle, Rocket } from "lucide-react";
import type { ActiveTask } from "@/lib/types";

/**
 * Per-row badge shown when a device is currently part of a running upgrade
 * job. Links to the job detail page so the operator can click straight from
 * the Devices table into the live timeline.
 *
 * Three visual states:
 *   - awaiting_* phase: amber pulse + "needs confirm"
 *   - downloading/upgrading with %: amber bar + percentage
 *   - other in-flight: spinning loader + phase label
 *
 * Compact (~110px wide) so it slots into the existing PAN-OS column without
 * stretching the row.
 */
export function UpgradeBadge({ t }: { t: ActiveTask }) {
  const pct =
    t.phase === "downloading_image" && typeof t.download_progress === "number"
      ? t.download_progress
      : (t.phase === "upgrade_secondary" || t.phase === "upgrade_primary")
        && typeof t.install_progress === "number"
        ? t.install_progress
        : null;

  const label = phaseLabel(t.phase);
  const isAwaiting = t.awaiting;

  return (
    <Link
      to={`/jobs/${t.job_id}`}
      title={`${t.job_name} → ${t.target_version} · ${label}`}
      className={`mt-0.5 inline-flex max-w-fit items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${
        isAwaiting
          ? "border-sky-700 bg-sky-950/40 text-sky-300 hover:bg-sky-900/40"
          : "border-amber-700 bg-amber-950/40 text-amber-300 hover:bg-amber-900/40"
      }`}
    >
      {isAwaiting ? (
        <PauseCircle className="h-3 w-3" />
      ) : pct != null ? (
        <Rocket className="h-3 w-3" />
      ) : (
        <Loader2 className="h-3 w-3 animate-spin" />
      )}
      <span className="font-medium">{label}</span>
      {pct != null && <span className="tabular-nums">· {pct}%</span>}
      <span className="opacity-70">→ {t.target_version}</span>
    </Link>
  );
}

function phaseLabel(phase: string): string {
  const map: Record<string, string> = {
    pending: "queued",
    precheck: "pre-check",
    awaiting_precheck_override: "needs OK",
    snapshot: "snapshot",
    downloading_image: "downloading",
    suspend_secondary: "HA suspend",
    upgrade_secondary: "installing (passive)",
    awaiting_reboot_confirm: "reboot?",
    postcheck_secondary: "post-check",
    awaiting_postcheck_override: "needs OK",
    awaiting_failover_confirm: "failover?",
    failover: "failing over",
    awaiting_primary_upgrade_confirm: "continue?",
    upgrade_primary: "installing (active)",
    postcheck_primary: "post-check",
    failback: "failback",
    report: "reporting",
  };
  return map[phase] ?? phase;
}
