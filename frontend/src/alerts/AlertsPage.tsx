import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { NavLink, useSearchParams } from "react-router-dom";

import {
  api,
  AlertRead,
  AlertRuleCreate,
  AlertRuleRead,
  AlertRuleUpdate,
  AlertSeverity,
} from "../api";
import { Button, Card, CardHeader, Input, Select } from "../core/ui/ui";

/**
 * Alerts page at `/alerts`.
 *
 * Phase 12b — full operator surface:
 *   - List active alerts grouped by severity; each row has an
 *     Acknowledge / Unacknowledge button so noisy alerts can be
 *     silenced without resolving the underlying capacity issue.
 *   - Manage alert rules inline: edit threshold, toggle enabled,
 *     delete, or add a new rule. Per-metric overrides supported.
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
  // on the worst stuff. Each group sorted by pct desc.
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

      <RulesCard rules={rules} isLoading={rulesQ.isLoading} />
    </div>
  );
}

// ----- alerts table -----

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
            <th className="text-left px-4 py-2 font-medium">Acknowledge</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((a) => (
            <AlertRow key={a.id} alert={a} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function AlertRow({ alert: a }: { alert: AlertRead }) {
  const qc = useQueryClient();
  const ackMutation = useMutation({
    mutationFn: () => api.acknowledgeAlert(a.id),
    onSuccess: () => {
      // Both the list and the home-dashboard summary need to refresh.
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["alerts-summary"] });
    },
  });
  const unackMutation = useMutation({
    mutationFn: () => api.unacknowledgeAlert(a.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alerts"] });
      qc.invalidateQueries({ queryKey: ["alerts-summary"] });
    },
  });
  return (
    <tr className="border-b border-zinc-800/40 hover:bg-zinc-900/30">
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
        {a.pct == null ? "—" : `${a.pct.toFixed(a.pct < 10 ? 1 : 0)}%`}
      </td>
      <td className="px-4 py-2 text-xs text-zinc-500 tabular-nums">
        {a.threshold_pct}%
      </td>
      <td className="px-4 py-2 text-xs text-zinc-500">{fmtTime(a.first_seen_at)}</td>
      <td className="px-4 py-2 text-xs text-zinc-500">{fmtTime(a.last_seen_at)}</td>
      <td className="px-4 py-2 text-xs">
        {a.acknowledged_at ? (
          <div className="flex items-center gap-2">
            <span className="text-emerald-400" title={`acknowledged ${fmtTime(a.acknowledged_at)}`}>
              ✓ {a.acknowledged_by ?? "—"}
            </span>
            <button
              onClick={() => unackMutation.mutate()}
              disabled={unackMutation.isPending}
              className="text-[10px] text-zinc-500 hover:text-zinc-300 underline"
            >
              {unackMutation.isPending ? "…" : "unack"}
            </button>
          </div>
        ) : (
          <button
            onClick={() => ackMutation.mutate()}
            disabled={ackMutation.isPending}
            className="text-[11px] px-2 py-0.5 rounded border border-zinc-700 text-zinc-300 hover:bg-zinc-800/60 disabled:opacity-50"
          >
            {ackMutation.isPending ? "Ack'ing…" : "Acknowledge"}
          </button>
        )}
      </td>
    </tr>
  );
}

// ----- rules section -----

function RulesCard({
  rules,
  isLoading,
}: {
  rules: AlertRuleRead[];
  isLoading: boolean;
}) {
  const [adding, setAdding] = useState(false);
  return (
    <Card>
      <CardHeader
        title="Configured thresholds"
        description="Rules the evaluator runs after every poll. Per-metric rules override the global default at the same severity."
        action={
          !adding && (
            <button
              onClick={() => setAdding(true)}
              className="text-xs text-blue-400 hover:text-blue-300"
            >
              + Add rule
            </button>
          )
        }
      />
      {adding && (
        <AddRuleForm
          onClose={() => setAdding(false)}
        />
      )}
      {isLoading ? (
        <div className="p-4 text-center text-xs text-zinc-500">Loading…</div>
      ) : rules.length === 0 ? (
        <div className="p-4 text-center text-xs text-zinc-500">
          No rules configured. Click "Add rule" above to create one.
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase text-zinc-500 border-b border-zinc-800">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Name</th>
              <th className="text-left px-4 py-2 font-medium">Scope</th>
              <th className="text-left px-4 py-2 font-medium">Severity</th>
              <th className="text-left px-4 py-2 font-medium">Threshold</th>
              <th className="text-left px-4 py-2 font-medium">Sustained</th>
              <th className="text-left px-4 py-2 font-medium">Enabled</th>
              <th className="text-left px-4 py-2 font-medium w-20"></th>
            </tr>
          </thead>
          <tbody>
            {rules.map((r) => (
              <RuleRow key={r.id} rule={r} />
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function RuleRow({ rule: r }: { rule: AlertRuleRead }) {
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState(r.name);
  const [draftThreshold, setDraftThreshold] = useState(r.threshold_pct);
  const [draftSustained, setDraftSustained] = useState(r.sustained_samples);

  const onSuccess = () =>
    qc.invalidateQueries({ queryKey: ["alert-rules"] });

  const updateMutation = useMutation({
    mutationFn: (body: AlertRuleUpdate) => api.updateAlertRule(r.id, body),
    onSuccess,
  });
  const deleteMutation = useMutation({
    mutationFn: () => api.deleteAlertRule(r.id),
    onSuccess,
  });

  const toggleEnabled = () =>
    updateMutation.mutate({ enabled: !r.enabled });

  const save = () => {
    const patch: AlertRuleUpdate = {};
    if (draftName !== r.name) patch.name = draftName;
    if (draftThreshold !== r.threshold_pct) patch.threshold_pct = draftThreshold;
    if (draftSustained !== r.sustained_samples)
      patch.sustained_samples = draftSustained;
    if (Object.keys(patch).length > 0) {
      updateMutation.mutate(patch, {
        onSuccess: () => {
          onSuccess();
          setEditing(false);
        },
      });
    } else {
      setEditing(false);
    }
  };

  const cancel = () => {
    setDraftName(r.name);
    setDraftThreshold(r.threshold_pct);
    setDraftSustained(r.sustained_samples);
    setEditing(false);
  };

  const remove = () => {
    // Window confirm — keep it minimal. If the operator wants a richer
    // confirm dialog we can add one later, but for a destructive op
    // on rule config (which can be re-added) this is fine.
    if (
      window.confirm(
        `Delete rule "${r.name}"? Historical alerts that fired against this rule keep their snapshotted threshold.`,
      )
    ) {
      deleteMutation.mutate();
    }
  };

  return (
    <tr className="border-b border-zinc-800/40 hover:bg-zinc-900/30">
      <td className="px-4 py-2 text-xs text-zinc-200">
        {editing ? (
          <Input
            value={draftName}
            onChange={(e) => setDraftName(e.target.value)}
            className="text-xs w-40"
          />
        ) : (
          r.name
        )}
      </td>
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
        {editing ? (
          <Input
            type="number"
            min={1}
            max={100}
            value={draftThreshold}
            onChange={(e) => setDraftThreshold(Number(e.target.value))}
            className="text-xs w-16"
          />
        ) : (
          `${r.threshold_pct}%`
        )}
      </td>
      <td className="px-4 py-2 text-xs text-zinc-300 tabular-nums">
        {editing ? (
          <Input
            type="number"
            min={1}
            max={20}
            value={draftSustained}
            onChange={(e) => setDraftSustained(Number(e.target.value))}
            className="text-xs w-14"
          />
        ) : r.sustained_samples <= 1 ? (
          <span className="text-zinc-500">instant</span>
        ) : (
          `${r.sustained_samples} polls`
        )}
      </td>
      <td className="px-4 py-2 text-xs">
        <button
          onClick={toggleEnabled}
          disabled={updateMutation.isPending}
          className={
            "text-[10px] px-2 py-0.5 rounded border " +
            (r.enabled
              ? "border-emerald-800/60 text-emerald-400 hover:bg-emerald-900/20"
              : "border-zinc-700 text-zinc-500 hover:bg-zinc-800/60")
          }
        >
          {r.enabled ? "enabled" : "disabled"}
        </button>
      </td>
      <td className="px-4 py-2 text-xs">
        {editing ? (
          <div className="flex items-center gap-2">
            <button
              onClick={save}
              disabled={updateMutation.isPending}
              className="text-[11px] text-blue-400 hover:text-blue-300 disabled:opacity-50"
            >
              save
            </button>
            <button
              onClick={cancel}
              className="text-[11px] text-zinc-500 hover:text-zinc-300"
            >
              cancel
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setEditing(true)}
              className="text-[11px] text-zinc-400 hover:text-zinc-200"
            >
              edit
            </button>
            <button
              onClick={remove}
              disabled={deleteMutation.isPending}
              className="text-[11px] text-rose-500 hover:text-rose-300 disabled:opacity-50"
            >
              delete
            </button>
          </div>
        )}
      </td>
    </tr>
  );
}

function AddRuleForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [metric, setMetric] = useState("");
  const [severity, setSeverity] = useState<AlertSeverity>("warning");
  const [threshold, setThreshold] = useState(80);
  const [sustained, setSustained] = useState(1);
  const [error, setError] = useState<string | null>(null);

  const createMutation = useMutation({
    mutationFn: (body: AlertRuleCreate) => api.createAlertRule(body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["alert-rules"] });
      onClose();
    },
    onError: (e: unknown) => {
      setError(e instanceof Error ? e.message : "Failed to create rule");
    },
  });

  const submit = () => {
    setError(null);
    if (!name.trim()) {
      setError("Name is required.");
      return;
    }
    createMutation.mutate({
      name: name.trim(),
      // Empty string in the UI = global default. The API expects null
      // for the "all metrics" scope.
      metric: metric.trim() === "" ? null : metric.trim(),
      severity,
      threshold_pct: threshold,
      sustained_samples: sustained,
      enabled: true,
    });
  };

  return (
    <div className="px-4 py-3 border-b border-zinc-800 bg-zinc-900/40 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
          Name
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Tight policy budget"
            className="text-xs w-48"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
          Metric <span className="text-zinc-600">(blank = all metrics)</span>
          <Input
            value={metric}
            onChange={(e) => setMetric(e.target.value)}
            placeholder="e.g. address_objects"
            className="text-xs w-48"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
          Severity
          <Select
            value={severity}
            onChange={(e) => setSeverity(e.target.value as AlertSeverity)}
            className="text-xs"
          >
            <option value="warning">Warning</option>
            <option value="critical">Critical</option>
          </Select>
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
          Threshold %
          <Input
            type="number"
            min={1}
            max={100}
            value={threshold}
            onChange={(e) => setThreshold(Number(e.target.value))}
            className="text-xs w-20"
          />
        </label>
        <label className="flex flex-col gap-1 text-[11px] text-zinc-400">
          Sustained polls <span className="text-zinc-600">(1 = instant)</span>
          <Input
            type="number"
            min={1}
            max={20}
            value={sustained}
            onChange={(e) => setSustained(Number(e.target.value))}
            className="text-xs w-20"
          />
        </label>
        <div className="flex items-center gap-2 pb-0.5">
          <Button onClick={submit} disabled={createMutation.isPending}>
            {createMutation.isPending ? "Saving…" : "Add rule"}
          </Button>
          <button
            onClick={onClose}
            className="text-xs text-zinc-500 hover:text-zinc-300"
          >
            cancel
          </button>
        </div>
      </div>
      {error && (
        <div className="text-xs text-rose-400">{error}</div>
      )}
    </div>
  );
}

// ----- shared -----

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
