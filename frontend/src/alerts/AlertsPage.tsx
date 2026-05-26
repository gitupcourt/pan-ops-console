import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { NavLink, useSearchParams } from "react-router-dom";

import { api, AlertRead, AlertRuleRead, AlertSeverity } from "../api";
import { Card, CardHeader, Select } from "../core/ui/ui";

/**
 * Alerts page at `/alerts`.
 *
 * Phase 12a — read-only listing of currently-firing alerts plus the
 * active rule table (so operators can see "what would fire and at
 * what threshold" without needing the rules-management UI yet). Phase
 * 12b will layer in acknowledge actions and the rules CRUD UI.
 *
 * The table is intentionally simple: one row per active alert, grouped
 * by severity (critical first, then warning). Each row links to the
 * trend view for the (device, metric) so operators can see the path
 * that led to the breach.
 *
 * URL params:
 *   ?state=active|all       — default "active"
 *   ?severity=critical|warning
 *   ?device_id=N
 */
export default function AlertsPage() {
  const [params, setParams] = useSearchParams();
  const state = (params.get("state") as "active" | "all" | null) ?? "active";
  const severity = params.get("severity") as AlertSeverity | null;

  const alertsQ = useQuery({
    queryKey: ["alerts", state, severity],
    queryFn: () =>
      api.listAlerts({
        state,
        severity: severity ?? undefined,
      }),
    refetchInterval: 30_000,
  });
  const rulesQ = useQuery({
    queryKey: ["alert-rules"],
    queryFn: api.listAlertRules,
  });

  const updateFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  const alerts = alertsQ.data ?? [];
  const rules = rulesQ.data ?? [];

  // Group active alerts by severity. Critical first so the eye lands
  // on the worst stuff. Each group sorted by pct desc — biggest
  // offender at the top of its group.
  const grouped = useMemo(() => {
    const crit = alerts
      .filter((a) => a.severity === "critical")
      .sort(byPctDesc);
    const warn = alerts
      .filter((a) => a.severity === "warning")
      .sort(byPctDesc);
    return { critical: crit, warning: warn };
  }, [alerts]);

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-zinc-100">Alerts</h2>

      <Card>
        <div className="px-4 py-3 flex flex-wrap items-center gap-3 text-xs border-b border-zinc-800">
          <label className="flex items-center gap-1.5 text-zinc-400">
            <span>State:</span>
            <Select
              value={state}
              onChange={(e) => updateFilter("state", e.target.value)}
              className="text-xs"
            >
              <option value="active">Active only</option>
              <option value="all">Include history</option>
            </Select>
          </label>
          <label className="flex items-center gap-1.5 text-zinc-400">
            <span>Severity:</span>
            <Select
              value={severity ?? ""}
              onChange={(e) => updateFilter("severity", e.target.value)}
              className="text-xs"
            >
              <option value="">All</option>
              <option value="critical">Critical only</option>
              <option value="warning">Warning only</option>
            </Select>
          </label>
        </div>

        <CardHeader
          title={state === "active" ? "Active alerts" : "All alerts"}
          description={
            alertsQ.isLoading
              ? "Loading…"
              : `${alerts.length} ${state === "active" ? "open" : "total"}`
          }
        />

        {alertsQ.isLoading ? (
          <div className="p-8 text-center text-xs text-zinc-500">Loading…</div>
        ) : alerts.length === 0 ? (
          <div className="p-8 text-center text-xs text-zinc-500">
            {state === "active"
              ? "No active alerts. The fleet is within configured capacity thresholds."
              : "No alerts in the database yet."}
          </div>
        ) : (
          <>
            {grouped.critical.length > 0 && (
              <AlertGroup label="Critical" tone="critical" rows={grouped.critical} />
            )}
            {grouped.warning.length > 0 && (
              <AlertGroup label="Warning" tone="warning" rows={grouped.warning} />
            )}
          </>
        )}
      </Card>

      <Card>
        <CardHeader
          title="Configured thresholds"
          description="Rules that the evaluator runs after every poll. Edit in phase 12b."
        />
        <RulesTable rules={rules} isLoading={rulesQ.isLoading} />
      </Card>
    </div>
  );
}

function AlertGroup({
  label,
  tone,
  rows,
}: {
  label: string;
  tone: "critical" | "warning";
  rows: AlertRead[];
}) {
  const headerClass =
    tone === "critical"
      ? "text-rose-400 border-rose-900/40"
      : "text-amber-400 border-amber-900/40";
  return (
    <div className="border-b border-zinc-800/60">
      <div
        className={`px-4 py-2 text-xs uppercase tracking-wider border-l-2 ${headerClass}`}
      >
        {label}
        <span className="text-zinc-500 normal-case tracking-normal text-[11px] ml-2">
          ({rows.length})
        </span>
      </div>
      <table className="w-full text-sm">
        <thead className="text-[11px] uppercase text-zinc-500 border-b border-zinc-800">
          <tr>
            <th className="text-left px-4 py-2 font-medium">Device</th>
            <th className="text-left px-4 py-2 font-medium">Metric</th>
            <th className="text-left px-4 py-2 font-medium">Current</th>
            <th className="text-left px-4 py-2 font-medium">Threshold</th>
            <th className="text-left px-4 py-2 font-medium">First seen</th>
            <th className="text-left px-4 py-2 font-medium">Last seen</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <tr
              key={a.id}
              className="border-b border-zinc-800/40 hover:bg-zinc-900/30"
            >
              <td className="px-4 py-2 text-xs">
                <NavLink
                  to={`/capacity/device/${a.device_id}`}
                  className="text-blue-400 hover:text-blue-300"
                >
                  {a.device_name}
                </NavLink>
              </td>
              <td className="px-4 py-2 text-xs">
                <NavLink
                  to={`/capacity/trend/${a.device_id}/${encodeURIComponent(a.metric)}`}
                  className="text-blue-400 hover:text-blue-300"
                >
                  {a.metric}
                </NavLink>
              </td>
              <td className="px-4 py-2 text-xs text-zinc-300 tabular-nums">
                {a.pct == null
                  ? "—"
                  : `${a.pct.toFixed(a.pct < 10 ? 1 : 0)}%`}
              </td>
              <td className="px-4 py-2 text-xs text-zinc-500 tabular-nums">
                {a.threshold_pct}%
              </td>
              <td className="px-4 py-2 text-xs text-zinc-500">
                {fmtTime(a.first_seen_at)}
              </td>
              <td className="px-4 py-2 text-xs text-zinc-500">
                {fmtTime(a.last_seen_at)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RulesTable({
  rules,
  isLoading,
}: {
  rules: AlertRuleRead[];
  isLoading: boolean;
}) {
  if (isLoading) {
    return <div className="p-4 text-center text-xs text-zinc-500">Loading…</div>;
  }
  if (rules.length === 0) {
    return (
      <div className="p-4 text-center text-xs text-zinc-500">
        No rules configured. The migration seeds default warning/critical
        rules on first boot — if you're seeing this, something's wrong.
      </div>
    );
  }
  return (
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase text-zinc-500 border-b border-zinc-800">
        <tr>
          <th className="text-left px-4 py-2 font-medium">Name</th>
          <th className="text-left px-4 py-2 font-medium">Scope</th>
          <th className="text-left px-4 py-2 font-medium">Severity</th>
          <th className="text-left px-4 py-2 font-medium">Threshold</th>
          <th className="text-left px-4 py-2 font-medium">Enabled</th>
        </tr>
      </thead>
      <tbody>
        {rules.map((r) => (
          <tr
            key={r.id}
            className="border-b border-zinc-800/40 hover:bg-zinc-900/30"
          >
            <td className="px-4 py-2 text-xs text-zinc-200">{r.name}</td>
            <td className="px-4 py-2 text-xs">
              {r.metric == null ? (
                <span className="text-zinc-500 italic">all metrics</span>
              ) : (
                <span className="text-zinc-300">{r.metric}</span>
              )}
            </td>
            <td className="px-4 py-2 text-xs">
              <SeverityBadge severity={r.severity} />
            </td>
            <td className="px-4 py-2 text-xs text-zinc-300 tabular-nums">
              {r.threshold_pct}%
            </td>
            <td className="px-4 py-2 text-xs">
              {r.enabled ? (
                <span className="text-emerald-400">enabled</span>
              ) : (
                <span className="text-zinc-500">disabled</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function SeverityBadge({ severity }: { severity: AlertSeverity }) {
  const cls =
    severity === "critical"
      ? "bg-rose-900/40 text-rose-300 border-rose-800/40"
      : "bg-amber-900/40 text-amber-300 border-amber-800/40";
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded border text-[10px] uppercase tracking-wider ${cls}`}
    >
      {severity}
    </span>
  );
}

function byPctDesc(a: AlertRead, b: AlertRead): number {
  return (b.pct ?? -1) - (a.pct ?? -1);
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  const now = Date.now();
  const ageMs = now - d.getTime();
  if (ageMs < 60_000) return "just now";
  if (ageMs < 3_600_000) return `${Math.round(ageMs / 60_000)}m ago`;
  if (ageMs < 86_400_000) return `${Math.round(ageMs / 3_600_000)}h ago`;
  return d.toLocaleDateString();
}
