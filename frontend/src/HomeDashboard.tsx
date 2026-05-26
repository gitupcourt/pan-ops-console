import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";

import {
  api,
  AlertsSummary,
  Device,
  JobsSummary,
  Panorama,
  VersionDistributionRow,
} from "./api";
import { Card, CardHeader } from "./core/ui/ui";

/**
 * Home Dashboard — `/`.
 *
 * Holistic at-a-glance view of the whole environment. Each panel is a
 * focused frame that summarizes one slice (alerts, upgrades, version
 * distribution, fleet status) and links into the dedicated page for
 * that slice.
 *
 * Phase 14 wires three of the four frames to real backend data via
 * the aggregation endpoints from phase 8:
 *   - UpgradesFrame  →  GET /upgrade/jobs/summary
 *   - VersionDistributionFrame  →  GET /devices/version-distribution
 *   - FleetSnapshotFrame  →  GET /devices + GET /panoramas (counted client-side)
 *
 * AlertsFrame stays a placeholder until phase 12 ships the rule
 * engine — no point rendering "0 alerts" when the underlying data
 * model doesn't exist yet.
 *
 * The page refetches every 30s to feel live. Every count is also a
 * link into the relevant detail page (often filtered) so the home
 * dashboard works as a triage hub: scan → spot → click → fix.
 */
export default function HomeDashboard() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-zinc-100">Overview</h2>
        <p className="text-sm text-zinc-400 mt-1">
          Fleet status at a glance — drill into any frame for detail.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <AlertsFrame />
        <UpgradesFrame />
        <VersionDistributionFrame />
        <FleetSnapshotFrame />
      </div>
    </div>
  );
}

// ----- frames -----

function AlertsFrame() {
  const q = useQuery({
    queryKey: ["alerts-summary"],
    queryFn: api.getAlertsSummary,
    refetchInterval: 30_000,
  });
  return (
    <Card>
      <CardHeader
        title="Active alerts"
        description="Devices crossing configured capacity thresholds."
        action={
          <NavLink
            to="/alerts"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            View all →
          </NavLink>
        }
      />
      <AlertsContent summary={q.data} isLoading={q.isLoading} error={q.error} />
    </Card>
  );
}

function AlertsContent({
  summary,
  isLoading,
  error,
}: {
  summary: AlertsSummary | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  if (isLoading) {
    return <FrameStatus>Loading…</FrameStatus>;
  }
  if (error) {
    return <FrameStatus tone="error">Failed to load alerts.</FrameStatus>;
  }
  if (!summary) return null;

  // Two prominent tiles for the two severities — critical surfaces as
  // danger when non-zero, warning as warn-amber. Acknowledged count
  // is a smaller third tile beneath so operators see at a glance how
  // many are still asking for attention vs. silenced.
  const tiles: { label: string; value: number; tone: Tone }[] = [
    {
      label: "Critical",
      value: summary.critical,
      tone: summary.critical > 0 ? "danger" : "muted",
    },
    {
      label: "Warning",
      value: summary.warning,
      tone: summary.warning > 0 ? "warn" : "muted",
    },
  ];
  return (
    <div className="p-4 space-y-2">
      <div className="grid grid-cols-2 gap-2">
        {tiles.map((t) => (
          <CountTile key={t.label} label={t.label} value={t.value} tone={t.tone} />
        ))}
      </div>
      {summary.total_active > 0 && (
        <div className="text-[11px] text-zinc-500 flex items-center justify-between px-1 pt-1">
          <span>
            {summary.acknowledged} of {summary.total_active} acknowledged
          </span>
          <NavLink to="/alerts" className="text-blue-400 hover:text-blue-300">
            Triage →
          </NavLink>
        </div>
      )}
      {summary.total_active === 0 && (
        <div className="text-[11px] text-emerald-400/80 text-center pt-1">
          All clear.
        </div>
      )}
    </div>
  );
}

function UpgradesFrame() {
  const q = useQuery({
    queryKey: ["jobs-summary"],
    queryFn: api.getJobsSummary,
    refetchInterval: 30_000,
  });
  return (
    <Card>
      <CardHeader
        title="Upgrade jobs"
        description="Recent + in-flight upgrade orchestration."
        action={
          <NavLink
            to="/upgrade"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            View all →
          </NavLink>
        }
      />
      <UpgradesContent
        summary={q.data}
        isLoading={q.isLoading}
        error={q.error}
      />
    </Card>
  );
}

function UpgradesContent({
  summary,
  isLoading,
  error,
}: {
  summary: JobsSummary | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  if (isLoading) {
    return <FrameStatus>Loading…</FrameStatus>;
  }
  if (error) {
    return <FrameStatus tone="error">Failed to load upgrade jobs.</FrameStatus>;
  }
  if (!summary) return null;

  // Buckets: Active (currently doing something), Completed (recent
  // successes), Failed (needs attention), Aborted (operator-cancelled,
  // informational). Awaiting-confirmation is a transient state in the
  // upgrade orchestrator — count it as Active so it doesn't disappear
  // visually while the operator is mid-flow.
  const active = summary.pending + summary.running + summary.awaiting_confirmation;
  const tiles: { label: string; value: number; tone: Tone }[] = [
    { label: "Active", value: active, tone: active > 0 ? "info" : "muted" },
    {
      label: "Failed",
      value: summary.failed,
      tone: summary.failed > 0 ? "danger" : "muted",
    },
    {
      label: "Completed",
      value: summary.completed,
      tone: summary.completed > 0 ? "success" : "muted",
    },
    {
      label: "Aborted",
      value: summary.aborted,
      tone: "muted",
    },
  ];
  return (
    <div className="grid grid-cols-2 gap-2 p-4">
      {tiles.map((t) => (
        <CountTile key={t.label} label={t.label} value={t.value} tone={t.tone} />
      ))}
    </div>
  );
}

function VersionDistributionFrame() {
  const q = useQuery({
    queryKey: ["version-distribution"],
    queryFn: api.getVersionDistribution,
    refetchInterval: 30_000,
  });
  return (
    <Card>
      <CardHeader
        title="PAN-OS version distribution"
        description="How many devices on each running version."
        action={
          <NavLink
            to="/inventory"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            View inventory →
          </NavLink>
        }
      />
      <VersionDistributionContent
        rows={q.data}
        isLoading={q.isLoading}
        error={q.error}
      />
    </Card>
  );
}

function VersionDistributionContent({
  rows,
  isLoading,
  error,
}: {
  rows: VersionDistributionRow[] | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  if (isLoading) {
    return <FrameStatus>Loading…</FrameStatus>;
  }
  if (error) {
    return (
      <FrameStatus tone="error">Failed to load version distribution.</FrameStatus>
    );
  }
  if (!rows || rows.length === 0) {
    return (
      <FrameStatus>
        No devices yet — add one in Inventory to see version distribution.
      </FrameStatus>
    );
  }

  // Total for the proportion bar. Devices with unknown version
  // (`version === null`) still count toward the denominator —
  // bucketing them visually as "unknown" lets the operator spot
  // never-polled rows and refresh them.
  const total = rows.reduce((acc, r) => acc + r.count, 0);
  return (
    <div className="px-4 py-3 space-y-1.5">
      {rows.map((r) => {
        const label = r.version ?? "(unknown)";
        const pct = total > 0 ? (r.count / total) * 100 : 0;
        const known = r.version !== null;
        return (
          <div
            key={label}
            className="flex items-center gap-3 text-xs"
            title={`${r.count} of ${total} devices on ${label}`}
          >
            <div
              className={`w-32 truncate ${known ? "text-zinc-200" : "text-zinc-500 italic"}`}
            >
              {label}
            </div>
            <div className="flex-1 h-2 bg-zinc-800 rounded overflow-hidden">
              <div
                className={`h-full rounded ${known ? "bg-blue-500/70" : "bg-zinc-600/60"}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <div className="w-8 text-right tabular-nums text-zinc-300">
              {r.count}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function FleetSnapshotFrame() {
  const devicesQ = useQuery({
    queryKey: ["devices"],
    queryFn: api.listDevices,
    refetchInterval: 30_000,
  });
  const panoramasQ = useQuery({
    queryKey: ["panoramas"],
    queryFn: api.listPanoramas,
    refetchInterval: 30_000,
  });
  return (
    <Card>
      <CardHeader
        title="Fleet at a glance"
        description="Devices, Panoramas, polling health."
        action={
          <NavLink
            to="/inventory"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            View inventory →
          </NavLink>
        }
      />
      <FleetSnapshotContent
        devices={devicesQ.data}
        panoramas={panoramasQ.data}
        isLoading={devicesQ.isLoading || panoramasQ.isLoading}
        error={devicesQ.error ?? panoramasQ.error}
      />
    </Card>
  );
}

function FleetSnapshotContent({
  devices,
  panoramas,
  isLoading,
  error,
}: {
  devices: Device[] | undefined;
  panoramas: Panorama[] | undefined;
  isLoading: boolean;
  error: unknown;
}) {
  if (isLoading) {
    return <FrameStatus>Loading…</FrameStatus>;
  }
  if (error) {
    return <FrameStatus tone="error">Failed to load fleet snapshot.</FrameStatus>;
  }
  const ds = devices ?? [];
  const ps = panoramas ?? [];

  // "Healthy" = polling enabled, has been polled, and the last poll
  // didn't error. "Stale" = enabled but no poll in the last hour
  // (catches devices the scheduler hasn't reached or that are too
  // slow). "Errored" = last poll explicitly failed. "Never polled" =
  // freshly added, no last_poll_at yet.
  const ONE_HOUR_MS = 60 * 60 * 1000;
  const now = Date.now();
  let healthy = 0;
  let stale = 0;
  let errored = 0;
  let neverPolled = 0;
  let disabled = 0;
  for (const d of ds) {
    if (!d.polling_enabled) {
      disabled++;
      continue;
    }
    if (d.last_poll_error) {
      errored++;
      continue;
    }
    if (!d.last_poll_at) {
      neverPolled++;
      continue;
    }
    const age = now - new Date(d.last_poll_at).getTime();
    if (age > ONE_HOUR_MS) {
      stale++;
    } else {
      healthy++;
    }
  }

  const tiles: { label: string; value: number; tone: Tone }[] = [
    { label: "Devices", value: ds.length, tone: "muted" },
    { label: "Panoramas", value: ps.length, tone: "muted" },
    { label: "Healthy polls", value: healthy, tone: healthy > 0 ? "success" : "muted" },
    {
      label: "Poll errors",
      value: errored,
      tone: errored > 0 ? "danger" : "muted",
    },
    {
      label: "Stale (>1h)",
      value: stale,
      tone: stale > 0 ? "warn" : "muted",
    },
    {
      label: "Never polled",
      value: neverPolled,
      tone: neverPolled > 0 ? "warn" : "muted",
    },
    {
      label: "Disabled",
      value: disabled,
      tone: "muted",
    },
  ];
  return (
    <div className="grid grid-cols-3 gap-2 p-4">
      {tiles.map((t) => (
        <CountTile key={t.label} label={t.label} value={t.value} tone={t.tone} />
      ))}
    </div>
  );
}

// ----- shared UI bits -----

type Tone = "muted" | "info" | "success" | "warn" | "danger";

function CountTile({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: Tone;
}) {
  const valueClass = {
    muted: "text-zinc-300",
    info: "text-blue-300",
    success: "text-emerald-300",
    warn: "text-amber-300",
    danger: "text-rose-300",
  }[tone];
  return (
    <div className="rounded border border-zinc-800 bg-zinc-900/40 px-3 py-2">
      <div className={`text-2xl font-semibold tabular-nums ${valueClass}`}>
        {value}
      </div>
      <div className="text-[11px] uppercase tracking-wider text-zinc-500 mt-0.5">
        {label}
      </div>
    </div>
  );
}

function FrameStatus({
  children,
  tone,
}: {
  children: React.ReactNode;
  tone?: "error";
}) {
  const cls =
    tone === "error"
      ? "text-rose-400"
      : "text-zinc-500 italic";
  return (
    <div className={`px-4 py-8 text-center text-xs ${cls}`}>{children}</div>
  );
}
