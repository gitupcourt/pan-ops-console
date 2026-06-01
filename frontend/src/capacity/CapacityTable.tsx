import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  NavLink,
  useNavigate,
  useSearchParams,
} from "react-router-dom";

import { api, CapacityTableRow, MetricCategory } from "../api";
import { Button, Card, CardHeader, Input, Select, TableScroll } from "../core/ui/ui";
import { CapacityViewToggle } from "./CapacityViewToggle";

// Sortable columns. "alert" and "predicted" aren't sortable yet
// because they don't have data — phase 12 / phase 15 add those.
type SortKey = "metric" | "pct" | "sw" | "host";
type SortDir = "asc" | "desc";

const DEFAULT_SORT: SortKey = "pct";
const DEFAULT_DIR: SortDir = "desc";

function isSortKey(s: string): s is SortKey {
  return s === "metric" || s === "pct" || s === "sw" || s === "host";
}
function isSortDir(s: string): s is SortDir {
  return s === "asc" || s === "desc";
}

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
 * Traffic), with each category collapsible.
 * Within each category, sort is operator-controlled via clickable
 * column headers (Metric / Amount Used / SW / Host); sort key + dir
 * persist in URL params so a sorted view is sharable. Default is
 * Amount Used desc — what's on fire surfaces first.
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
  // Free-text search (client-side over the fetched rows). Matches a
  // resource check (metric / description) OR a device (name / IP /
  // serial). In the URL so a searched view is shareable.
  const queryText = params.get("q") ?? "";

  // Sort state also lives in URL params so a sorted view is sharable.
  // Default is pct desc — what's on fire surfaces first — matching the
  // pre-sortable behaviour.
  const sortParam = params.get("sort") ?? "";
  const dirParam = params.get("dir") ?? "";
  const sortBy: SortKey = isSortKey(sortParam) ? sortParam : DEFAULT_SORT;
  const sortDir: SortDir = isSortDir(dirParam) ? dirParam : DEFAULT_DIR;

  const handleHeaderClick = (col: SortKey) => {
    const next = new URLSearchParams(params);
    if (sortBy === col) {
      // Toggle direction in-place
      next.set("sort", col);
      next.set("dir", sortDir === "asc" ? "desc" : "asc");
    } else {
      // Switch column with a sensible default direction —
      // numeric % defaults desc (most used first), string columns
      // default asc (A → Z).
      next.set("sort", col);
      next.set("dir", col === "pct" ? "desc" : "asc");
    }
    setParams(next);
  };

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

  // Surface available models / metrics / DGs / TSs from the FULL result
  // set (not the searched subset) so the dropdowns don't collapse as you
  // type. Like the heat map: no hardcoded lists.
  const { models, metrics, deviceGroups, templateStacks } = useMemo(
    () => deriveOptions(rows),
    [rows],
  );

  // Apply the free-text search client-side. Matches resource check
  // (metric id + human description) OR device identity (name / IP /
  // serial) — the same identifiers Inventory searches on.
  const searchedRows = useMemo(() => {
    const needle = queryText.trim().toLowerCase();
    if (!needle) return rows;
    return rows.filter((r) => {
      const hay = [
        r.metric,
        r.metric_description,
        r.device_name,
        r.ip_address,
        r.serial,
        r.model,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return hay.includes(needle);
    });
  }, [rows, queryText]);

  // Group rows by category for the section layout, with the active
  // sort applied within each category.
  const byCategory = useMemo(
    () => groupByCategory(searchedRows, sortBy, sortDir),
    [searchedRows, sortBy, sortDir],
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

      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-zinc-100">
          Capacity Analyzer
        </h2>
        <CapacityViewToggle view="table" />
      </div>

      <Card>
        <div className="px-4 py-3 flex flex-wrap items-center gap-3 text-xs border-b border-zinc-800">
          <Input
            placeholder="Search check or device (name, IP, serial)…"
            value={queryText}
            onChange={(e) => updateFilter("q", e.target.value)}
            className="w-72 text-xs"
          />
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
            queryText
              ? `${searchedRows.length} row${searchedRows.length === 1 ? "" : "s"} match “${queryText}”${
                  rows.length === total ? "" : ` (of ${total} before backend cap)`
                }`
              : rows.length === total
                ? `${rows.length} row${rows.length === 1 ? "" : "s"}`
                : `Showing ${rows.length} of ${total} rows (backend cap)`
          }
        />

        {tableQ.isLoading ? (
          <div className="p-8 text-center text-xs text-zinc-500">Loading…</div>
        ) : searchedRows.length === 0 ? (
          <div className="p-8 text-center text-xs text-zinc-500">
            {queryText
              ? `No checks or devices match “${queryText}”. Clear the search or broaden it.`
              : "No rows match the current filters. Try Clear all filters above, or check the heat map for what models / metrics have samples."}
          </div>
        ) : (
          <>
            <CategorySection
              label="Configuration Resource"
              rows={byCategory.config}
              sortBy={sortBy}
              sortDir={sortDir}
              onSort={handleHeaderClick}
              onMetricClick={(deviceId, metricName) =>
                navigate(`/capacity/trend/${deviceId}/${encodeURIComponent(metricName)}`)
              }
            />
            <CategorySection
              label="System Resource"
              rows={byCategory.system}
              sortBy={sortBy}
              sortDir={sortDir}
              onSort={handleHeaderClick}
              onMetricClick={(deviceId, metricName) =>
                navigate(`/capacity/trend/${deviceId}/${encodeURIComponent(metricName)}`)
              }
            />
            <CategorySection
              label="Traffic Resource"
              rows={byCategory.traffic}
              sortBy={sortBy}
              sortDir={sortDir}
              onSort={handleHeaderClick}
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
  rows,
  sortBy,
  sortDir,
  onSort,
  onMetricClick,
}: {
  label: string;
  rows: CapacityTableRow[];
  sortBy: SortKey;
  sortDir: SortDir;
  onSort: (col: SortKey) => void;
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

      {!collapsed && (
        <CategoryTable
          rows={rows}
          sortBy={sortBy}
          sortDir={sortDir}
          onSort={onSort}
          onMetricClick={onMetricClick}
        />
      )}
    </div>
  );
}

function CategoryTable({
  rows,
  sortBy,
  sortDir,
  onSort,
  onMetricClick,
}: {
  rows: CapacityTableRow[];
  sortBy: SortKey;
  sortDir: SortDir;
  onSort: (col: SortKey) => void;
  onMetricClick: (deviceId: number, metric: string) => void;
}) {
  return (
    <TableScroll>
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase text-zinc-500 border-b border-zinc-800">
        <tr>
          <SortableHeader col="metric" label="Metric" sortBy={sortBy} sortDir={sortDir} onSort={onSort} />
          <SortableHeader col="pct" label="Amount Used" sortBy={sortBy} sortDir={sortDir} onSort={onSort} />
          {/* Alert and Predicted to Hit aren't sortable yet — they
              have no data until phase 12 / phase 15 land. */}
          <th className="text-left px-4 py-2 font-medium">Alert</th>
          <th className="text-left px-4 py-2 font-medium">Predicted to Hit</th>
          <SortableHeader col="sw" label="SW" sortBy={sortBy} sortDir={sortDir} onSort={onSort} />
          <SortableHeader col="host" label="Host" sortBy={sortBy} sortDir={sortDir} onSort={onSort} />
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
                to={`/capacity/device/${r.device_id}`}
                className="text-blue-400 hover:text-blue-300 text-xs"
                title={`Open device-level view for ${r.device_name}`}
              >
                {r.device_name}
              </NavLink>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
    </TableScroll>
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
  sortBy: SortKey,
  sortDir: SortDir,
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
  // Apply the active sort within each category. Empty/null values
  // (e.g. pct=null on a metric whose max we don't know yet) always
  // sort to the bottom regardless of direction — those rows are
  // noise relative to the data the operator cares about.
  for (const cat of Object.keys(out) as MetricCategory[]) {
    out[cat] = sortRows(out[cat], sortBy, sortDir);
  }
  return out;
}

function sortRows(
  rows: CapacityTableRow[],
  sortBy: SortKey,
  sortDir: SortDir,
): CapacityTableRow[] {
  const arr = [...rows];
  arr.sort((a, b) => {
    const aEmpty = isSortEmpty(a, sortBy);
    const bEmpty = isSortEmpty(b, sortBy);
    if (aEmpty && bEmpty) return 0;
    if (aEmpty) return 1;
    if (bEmpty) return -1;
    const cmp = compareRows(a, b, sortBy);
    return sortDir === "asc" ? cmp : -cmp;
  });
  return arr;
}

function isSortEmpty(r: CapacityTableRow, key: SortKey): boolean {
  switch (key) {
    case "pct":
      return r.pct == null;
    case "sw":
      return !r.software_version;
    case "metric":
    case "host":
      return false;
  }
}

function compareRows(
  a: CapacityTableRow,
  b: CapacityTableRow,
  key: SortKey,
): number {
  switch (key) {
    case "metric":
      return a.metric_description.localeCompare(b.metric_description);
    case "pct":
      // Both sides are non-null here (isSortEmpty filter above).
      return (a.pct as number) - (b.pct as number);
    case "sw":
      return (a.software_version ?? "").localeCompare(
        b.software_version ?? "",
      );
    case "host":
      return a.device_name.localeCompare(b.device_name);
  }
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

/**
 * Clickable column header for the sortable table.
 *
 * - When this column is the active sort, shows a ▲ / ▼ indicator and
 *   in the column-header color to mark it.
 * - Clicking the active column toggles direction.
 * - Clicking a different column switches to that column with a
 *   sensible default direction (set by the caller).
 */
function SortableHeader({
  col,
  label,
  sortBy,
  sortDir,
  onSort,
}: {
  col: SortKey;
  label: string;
  sortBy: SortKey;
  sortDir: SortDir;
  onSort: (col: SortKey) => void;
}) {
  const active = sortBy === col;
  return (
    <th className="text-left px-4 py-2 font-medium">
      <button
        type="button"
        onClick={() => onSort(col)}
        className={
          "inline-flex items-center gap-1 hover:text-zinc-200 " +
          (active ? "text-zinc-200" : "text-zinc-500")
        }
        title={
          active
            ? `Sorted ${sortDir === "asc" ? "ascending" : "descending"} — click to flip`
            : `Sort by ${label}`
        }
      >
        <span>{label}</span>
        <span className="text-[9px] w-2 inline-block">
          {active ? (sortDir === "asc" ? "▲" : "▼") : ""}
        </span>
      </button>
    </th>
  );
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
