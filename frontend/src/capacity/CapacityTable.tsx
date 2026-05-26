import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  NavLink,
  useNavigate,
  useSearchParams,
} from "react-router-dom";

import { api, CapacityTableRow, MetricCategory } from "../api";
import { Button, Card, CardHeader, Select } from "../core/ui/ui";

/**
 * Capacity table view at `/capacity/table`.
 *
 * Phase 10. The middle drill-step between the heat-map (`/capacity`,
 * phase 9) and the per-device-per-metric trend chart
 * (`/capacity/trend/{device}/{metric}`, phase 11).
 *
 * Filter context is URL-driven so the heat-map's tile click can deep-
 * link straight to a filtered slice (`?model=PA-220&metric=address_objects`)
 * and the operator can copy/share the URL. Changing a filter on this
 * page rewrites the URL params; the back button takes you to the
 * previous filter combination, which means heat-map-tile-clicked-out
 * back-arrows return to the heat map.
 *
 * Rows are grouped by Resource Category (Configuration / System /
 * Traffic) per the PA screenshot, with each category collapsible.
 * Within each category, rows sort by % desc — what's on fire surfaces
 * first.
 *
 * "Clear all" reverts to showing every (device, metric) in the fleet.
 * Backend caps at 500 rows; with the realistic operator scale
 * (100 devices × 17 metrics = 1700 rows) we'll add server-side
 * pagination here when needed. For 500-and-under, virtualization is
 * a phase-15 nice-to-have.
 */
export default function CapacityTable() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  // Read initial filter values from URL. All optional.
  const model = params.get("model") ?? "";
  const metric = params.get("metric") ?? "";
  const deviceGroup = params.get("device_group") ?? "";
  const templateStack = params.get("template_stack") ?? "";

  const tableQ = useQuery({
    queryKey: [
      "capacity-table",
      model,
      metric,
      deviceGroup,
      templateStack,
    ],
    queryFn: () =>
      api.getCapacityTable({
        model: model || null,
        metric: metric || null,
        device_group: deviceGroup || null,
        template_stack: templateStack || null,
      }),
    refetchInterval: 30_000,
  });

  // Helper to update a single filter in the URL params.
  const updateFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next);
  };

  const clearAll = () => {
    setParams(new URLSearchParams());
  };

  const rows = tableQ.data?.rows ?? [];
  const total = tableQ.data?.total ?? 0;

  // Surface available models / metrics / DGs / TSs from the current
  // result set so dropdowns are populated dynamically. Like the heat
  // map: no hardcoded lists.
  const { models, metrics, deviceGroups, templateStacks } = useMemo(
    () => deriveOptions(rows),
    [rows],
  );

  // Group rows by category for the section layout.
  const byCategory = useMemo(
    () => groupByCategory(rows),
    [rows],
  );

  const title = describeFilters({ model, metric, deviceGroup, templateStack });

  return (
    <div className="space-y-4">
      <div className="text-xs text-zinc-500">
        <NavLink to="/capacity" className="text-blue-400 hover:text-blue-300">
          Capacity Analyzer Heat-map
        </NavLink>
        {" › "}
        <span>Capacity Analyzer Table</span>
      </div>

      <h2 className="text-xl font-semibold text-zinc-100">
        Capacity Analyzer
      </h2>

      <Card>
        <div className="px-4 py-3 flex flex-wrap items-center gap-3 text-xs border-b border-zinc-800">
          <FilterDropdown
            label="Metric"
            value={metric}
            onChange={(v) => updateFilter("metric", v)}
            options={[
              { value: "", label: "All" },
              ...metrics.map((m) => ({
                value: m.metric,
                label: m.metric_description,
              })),
            ]}
          />
          <FilterDropdown
            label="Model Type"
            value={model}
            onChange={(v) => updateFilter("model", v)}
            options={[
              { value: "", label: "All" },
              ...models.map((m) => ({ value: m, label: m })),
            ]}
          />
          <FilterDropdown
            label="Device Group"
            value={deviceGroup}
            onChange={(v) => updateFilter("device_group", v)}
            options={[
              { value: "", label: "All" },
              ...deviceGroups.map((g) => ({ value: g, label: g })),
            ]}
          />
          <FilterDropdown
            label="Template Stack"
            value={templateStack}
            onChange={(v) => updateFilter("template_stack", v)}
            options={[
              { value: "", label: "All" },
              ...templateStacks.map((t) => ({ value: t, label: t })),
            ]}
          />
          <Button onClick={clearAll}>Clear all filters</Button>
        </div>

        <CardHeader
          title={title}
          description={
            rows.length === total
              ? `${rows.length} row${rows.length === 1 ? "" : "s"}`
              : `Showing ${rows.length} of ${total} rows (backend cap)`
          }
        />

        {tableQ.isLoading ? (
          <div className="p-8 text-center text-xs text-zinc-500">Loading…</div>
        ) : rows.length === 0 ? (
          <div className="p-8 text-center text-xs text-zinc-500">
            No rows match the current filters. Try Clear all filters above,
            or check the heat map for what models / metrics have samples.
          </div>
        ) : (
          <>
            <CategorySection
              label="Configuration Resource"
              category="config"
              rows={byCategory.config}
              onMetricClick={(deviceId, metricName) =>
                navigate(`/capacity/trend/${deviceId}/${encodeURIComponent(metricName)}`)
              }
            />
            <CategorySection
              label="System Resource"
              category="system"
              rows={byCategory.system}
              onMetricClick={(deviceId, metricName) =>
                navigate(`/capacity/trend/${deviceId}/${encodeURIComponent(metricName)}`)
              }
            />
            <CategorySection
              label="Traffic Resource"
              category="traffic"
              rows={byCategory.traffic}
              onMetricClick={(deviceId, metricName) =>
                navigate(`/capacity/trend/${deviceId}/${encodeURIComponent(metricName)}`)
              }
            />
          </>
        )}
      </Card>
    </div>
  );
}

// ----- category section -----

function CategorySection({
  label,
  category,
  rows,
  onMetricClick,
}: {
  label: string;
  category: MetricCategory;
  rows: CapacityTableRow[];
  onMetricClick: (deviceId: number, metric: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  if (rows.length === 0) return null;

  return (
    <div className="border-b border-zinc-800/60">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center gap-2 px-4 py-2 text-xs uppercase tracking-wider text-zinc-300 hover:bg-zinc-800/40"
      >
        <span className={`inline-block w-3 transition-transform ${collapsed ? "" : "rotate-90"}`}>
          ›
        </span>
        <span>{label}</span>
        <span className="text-zinc-500 normal-case tracking-normal text-[11px] ml-1">
          ({rows.length})
        </span>
      </button>

      {!collapsed && <CategoryTable rows={rows} onMetricClick={onMetricClick} />}
    </div>
  );
}

function CategoryTable({
  rows,
  onMetricClick,
}: {
  rows: CapacityTableRow[];
  onMetricClick: (deviceId: number, metric: string) => void;
}) {
  return (
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase text-zinc-500 border-b border-zinc-800">
        <tr>
          <th className="text-left px-4 py-2 font-medium">Metric</th>
          <th className="text-left px-4 py-2 font-medium">Amount Used</th>
          <th className="text-left px-4 py-2 font-medium">Alert</th>
          <th className="text-left px-4 py-2 font-medium">Predicted to Hit</th>
          <th className="text-left px-4 py-2 font-medium">SW</th>
          <th className="text-left px-4 py-2 font-medium">Host</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={`${r.device_id}-${r.metric}`} className="border-b border-zinc-800/40 hover:bg-zinc-900/30">
            <td className="px-4 py-2">
              <button
                onClick={() => onMetricClick(r.device_id, r.metric)}
                className="text-blue-400 hover:text-blue-300 text-left"
              >
                {r.metric_description}
              </button>
            </td>
            <td className="px-4 py-2">
              <UsageCell row={r} />
            </td>
            <td className="px-4 py-2 text-xs text-zinc-500">
              {/* Alerts ship in phase 12; until then this cell is just
                  the empty state. Once the rule engine lands, this
                  becomes the alert name link (e.g. "Approaching Max
                  Capacity - Address Objects") routing to /alerts. */}
              —
            </td>
            <td className="px-4 py-2 text-xs text-zinc-500">
              {/* Predicted date ships in phase 15 via pre-computed
                  rollups. Until then this is null on every row.
                  Phase 11's trend view computes it per-request for the
                  one (device, metric) being viewed. */}
              —
            </td>
            <td className="px-4 py-2 text-xs text-zinc-400 tabular-nums">
              {r.software_version ?? "—"}
            </td>
            <td className="px-4 py-2">
              <NavLink
                to="/capacity/device"
                className="text-blue-400 hover:text-blue-300 text-xs"
                title={`Open device-level view (selector currently lands on first device — phase 11 deep-links by id)`}
              >
                {r.device_name}
              </NavLink>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function UsageCell({ row }: { row: CapacityTableRow }) {
  const pct = row.pct;
  const max = row.max;
  const barWidth = pct == null ? 0 : Math.min(100, Math.max(0, pct));
  const barColor = barColorForPct(pct);

  return (
    <div className="flex items-center gap-3">
      <div className="text-xs text-zinc-300 tabular-nums w-28">
        {fmtCount(row.current)}{" "}
        <span className="text-zinc-500">
          ({pct == null ? "—" : `${pct.toFixed(pct < 10 ? 1 : 0)}%`})
        </span>
      </div>
      <div className="flex-1 h-2 bg-zinc-800 rounded overflow-hidden max-w-xs">
        <div
          className={`h-full rounded ${barColor}`}
          style={{ width: `${barWidth}%` }}
        />
      </div>
      {max != null && (
        <div className="text-[10px] text-zinc-500 tabular-nums">
          of {fmtCount(max)}
        </div>
      )}
    </div>
  );
}

// ----- helpers -----

function deriveOptions(rows: CapacityTableRow[]) {
  const models = Array.from(
    new Set(rows.map((r) => r.model).filter((m): m is string => !!m)),
  ).sort();
  const metricMap = new Map<
    string,
    { metric: string; metric_description: string }
  >();
  for (const r of rows) {
    if (!metricMap.has(r.metric)) {
      metricMap.set(r.metric, {
        metric: r.metric,
        metric_description: r.metric_description,
      });
    }
  }
  const metrics = Array.from(metricMap.values()).sort((a, b) =>
    a.metric_description.localeCompare(b.metric_description),
  );
  const deviceGroups = Array.from(
    new Set(rows.map((r) => r.device_group).filter((g): g is string => !!g)),
  ).sort();
  const templateStacks = Array.from(
    new Set(rows.map((r) => r.template_stack).filter((t): t is string => !!t)),
  ).sort();
  return { models, metrics, deviceGroups, templateStacks };
}

function groupByCategory(
  rows: CapacityTableRow[],
): Record<MetricCategory, CapacityTableRow[]> {
  const out: Record<MetricCategory, CapacityTableRow[]> = {
    config: [],
    system: [],
    traffic: [],
  };
  for (const r of rows) {
    if (r.category === "config" || r.category === "system" || r.category === "traffic") {
      out[r.category].push(r);
    }
  }
  // Within each category, sort by pct desc (null last).
  for (const cat of Object.keys(out) as MetricCategory[]) {
    out[cat].sort((a, b) => {
      const ap = a.pct ?? -1;
      const bp = b.pct ?? -1;
      return bp - ap;
    });
  }
  return out;
}

function describeFilters(f: {
  model: string;
  metric: string;
  deviceGroup: string;
  templateStack: string;
}): string {
  const parts: string[] = [];
  if (f.model) parts.push(`Model ${f.model}`);
  if (f.metric) parts.push(`Metric ${f.metric}`);
  if (f.deviceGroup) parts.push(`Device Group ${f.deviceGroup}`);
  if (f.templateStack) parts.push(`Template Stack ${f.templateStack}`);
  if (parts.length === 0) return "All metrics on all devices";
  return parts.join(", ");
}

function barColorForPct(pct: number | null): string {
  if (pct == null) return "bg-zinc-700";
  if (pct < 60) return "bg-emerald-500";
  if (pct < 80) return "bg-amber-500";
  return "bg-rose-500";
}

function fmtCount(n: number | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function FilterDropdown({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex items-center gap-1.5 text-zinc-400">
      <span>{label}:</span>
      <Select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="text-xs"
      >
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </Select>
    </label>
  );
}
