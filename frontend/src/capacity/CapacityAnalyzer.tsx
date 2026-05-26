import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, HeatmapCell, MetricCategory } from "../api";
import { Button, Card, CardHeader, Select } from "../core/ui/ui";
import { HeatMapTile } from "./HeatMapTile";

/**
 * Capacity Analyzer landing page at `/capacity`.
 *
 * Phase-9 piece of the operator's overhaul. Renders the PA-style heat
 * map: device models down the y-axis, metrics across the x-axis, each
 * tile colored by the highest % observed for that (model, metric)
 * across the fleet.
 *
 * UX flow:
 *   1. Operator scans the grid for red tiles.
 *   2. Hover any tile → popover with top devices by usage.
 *   3. Click a tile → drill to /capacity/table?model=X&metric=Y
 *      (phase 10) → drill to /capacity/trend/{device}/{metric}
 *      (phase 11). The device-detail per-metric view we previously
 *      had at `/capacity` is reachable via the table view's device
 *      column.
 *
 * Filters in scope for phase 9:
 *   - Metric (single metric or all)
 *   - Model Type (single model or all)
 *   - Device Group (Panorama-managed groupings)
 *   - Template Stack
 *   - Capacity Filter (range slider — only show tiles with max% in this band)
 *   - Multicolor / Monochrome view toggle
 */
export default function CapacityAnalyzer() {
  // Filter state. All optional; if everything is unset we show the full grid.
  const [metricFilter, setMetricFilter] = useState<string>("");
  const [modelFilter, setModelFilter] = useState<string>("");
  const [deviceGroup, setDeviceGroup] = useState<string>("");
  const [templateStack, setTemplateStack] = useState<string>("");
  const [pctMin, setPctMin] = useState<number>(0);
  const [pctMax, setPctMax] = useState<number>(100);
  const [colorScheme, setColorScheme] = useState<"multi" | "mono">("multi");

  const heatmapQ = useQuery({
    queryKey: ["capacity-heatmap", deviceGroup, templateStack],
    queryFn: () =>
      api.getCapacityHeatmap({
        device_group: deviceGroup || null,
        template_stack: templateStack || null,
      }),
    refetchInterval: 30_000, // refresh every 30s — capacity changes slowly
  });

  // Surface available filter options from the data so the dropdowns are
  // populated dynamically (no hardcoded model list / metric list).
  const cells = heatmapQ.data ?? [];
  const { models, metricsByCategory, deviceGroups, templateStacks, allMetrics } =
    useMemo(() => deriveOptions(cells), [cells]);

  // Apply client-side filters that the API doesn't (metric, model, pct
  // range) — keeps the API surface narrow and lets the slider feel
  // responsive without round-tripping.
  const visibleCells = useMemo(() => {
    return cells.filter((c) => {
      if (metricFilter && c.metric !== metricFilter) return false;
      if (modelFilter && c.model !== modelFilter) return false;
      const pct = c.max_pct ?? 0;
      if (pct < pctMin || pct > pctMax) return false;
      return true;
    });
  }, [cells, metricFilter, modelFilter, pctMin, pctMax]);

  const reset = () => {
    setMetricFilter("");
    setModelFilter("");
    setDeviceGroup("");
    setTemplateStack("");
    setPctMin(0);
    setPctMax(100);
  };

  const title = useMemo(() => {
    const m = metricFilter || "All Metrics";
    const t = modelFilter || "All Models";
    return `Capacity Overview for ${m} on ${t}`;
  }, [metricFilter, modelFilter]);

  return (
    <div className="space-y-4">
      <h2 className="text-xl font-semibold text-zinc-100">
        Capacity Analyzer
      </h2>

      <Card>
        <div className="px-4 py-3 flex flex-wrap items-center gap-3 text-xs border-b border-zinc-800">
          <FilterDropdown
            label="Metric"
            value={metricFilter}
            onChange={setMetricFilter}
            options={[
              { value: "", label: "All" },
              ...allMetrics.map((m) => ({ value: m.metric, label: m.metric_description })),
            ]}
          />
          <FilterDropdown
            label="Model Type"
            value={modelFilter}
            onChange={setModelFilter}
            options={[
              { value: "", label: "All" },
              ...models.map((m) => ({ value: m, label: m })),
            ]}
          />
          <FilterDropdown
            label="Device Group"
            value={deviceGroup}
            onChange={setDeviceGroup}
            options={[
              { value: "", label: "All" },
              ...deviceGroups.map((g) => ({ value: g, label: g })),
            ]}
          />
          <FilterDropdown
            label="Template Stack"
            value={templateStack}
            onChange={setTemplateStack}
            options={[
              { value: "", label: "All" },
              ...templateStacks.map((t) => ({ value: t, label: t })),
            ]}
          />
          <Button onClick={reset}>Reset</Button>

          <div className="ml-auto flex items-center gap-1">
            <ColorSchemeToggle value={colorScheme} onChange={setColorScheme} />
          </div>
        </div>

        <CardHeader
          title={title}
          description={`${visibleCells.length} of ${cells.length} tiles shown`}
        />

        <CapacityFilterSlider
          min={pctMin}
          max={pctMax}
          onChange={(lo, hi) => {
            setPctMin(lo);
            setPctMax(hi);
          }}
        />

        {heatmapQ.isLoading ? (
          <div className="p-8 text-center text-xs text-zinc-500">Loading…</div>
        ) : visibleCells.length === 0 ? (
          <div className="p-8 text-center text-xs text-zinc-500">
            No tiles match the current filters. Capacity data appears once the
            scheduled poller has touched at least one device.
          </div>
        ) : (
          <HeatMapGrid
            cells={visibleCells}
            models={models}
            metricsByCategory={metricsByCategory}
            colorScheme={colorScheme}
          />
        )}
      </Card>
    </div>
  );
}

// ----- the grid itself -----

type CellsByKey = Map<string, HeatmapCell>;

function HeatMapGrid({
  cells,
  models,
  metricsByCategory,
  colorScheme,
}: {
  cells: HeatmapCell[];
  models: string[];
  metricsByCategory: Record<MetricCategory, MetricInfo[]>;
  colorScheme: "multi" | "mono";
}) {
  // Build a fast lookup from (model, metric) → cell so the grid render
  // can find each tile in O(1).
  const byKey: CellsByKey = useMemo(() => {
    const m = new Map<string, HeatmapCell>();
    for (const c of cells) m.set(`${c.model}${c.metric}`, c);
    return m;
  }, [cells]);

  // Restrict the rendered models + metrics to those present in the
  // filtered cell set — otherwise filtering by Metric would leave empty
  // model rows / metric columns.
  const visibleModels = useMemo(() => {
    const set = new Set(cells.map((c) => c.model));
    return models.filter((m) => set.has(m));
  }, [cells, models]);
  const visibleByCategory = useMemo(() => {
    const visibleMetrics = new Set(cells.map((c) => c.metric));
    const out: Record<MetricCategory, MetricInfo[]> = {
      config: metricsByCategory.config.filter((m) => visibleMetrics.has(m.metric)),
      system: metricsByCategory.system.filter((m) => visibleMetrics.has(m.metric)),
      traffic: metricsByCategory.traffic.filter((m) => visibleMetrics.has(m.metric)),
    };
    return out;
  }, [cells, metricsByCategory]);

  // Flat list of metrics in display order: config columns, then system, then traffic.
  const orderedMetrics: MetricInfo[] = [
    ...visibleByCategory.config,
    ...visibleByCategory.system,
    ...visibleByCategory.traffic,
  ];

  return (
    // Per the CSS spec, ANY non-visible `overflow-x` (auto, scroll,
    // hidden) promotes `overflow-y: visible` to `overflow-y: auto`.
    // The previous fix attempt set both axes explicitly but the
    // promotion happens regardless — there is no combination of
    // (auto-x, visible-y) that the browser will honour as written.
    //
    // The way out is `overflow: clip` on one axis: `clip` doesn't
    // create a scroll container, so the other axis stays exactly as
    // declared. `overflow-y: clip` is widely supported (Chrome 90+,
    // Firefox 81+, Safari 16+).
    //
    // For the heat map specifically the table currently fits inside
    // the card at typical widths — phase-10's table view is where
    // horizontal real estate gets tight. If the grid ever does
    // overflow horizontally, the user can scroll the page. The
    // important thing is NEVER scroll the heat-map area vertically;
    // every model row and the rotated metric labels below should be
    // visible at once.
    //
    // pb-48 (192px) reserves enough room below the table for rotated
    // 45° labels up to ~40 chars long. Combined with shortLabel()
    // stripping any "(...)" parenthetical from descriptions, the
    // longest labels in the current catalog (e.g. "Active sessions
    // vs. session table maximum" at 41 chars) fit cleanly.
    <div
      className="p-4 pb-48"
      style={{ overflowX: "auto", overflowY: "clip" }}
    >
      <table className="border-separate border-spacing-1">
        <thead>
          {/* Row 1: category group headers spanning their metric columns */}
          <tr>
            <th className="w-24" />
            {visibleByCategory.config.length > 0 && (
              <th
                colSpan={visibleByCategory.config.length}
                className="text-left text-[11px] uppercase tracking-wider text-zinc-400 px-2 py-1 bg-zinc-800/40 rounded"
              >
                Configuration Resource
              </th>
            )}
            {visibleByCategory.system.length > 0 && (
              <th
                colSpan={visibleByCategory.system.length}
                className="text-left text-[11px] uppercase tracking-wider text-zinc-400 px-2 py-1 bg-zinc-800/40 rounded"
              >
                System Resource
              </th>
            )}
            {visibleByCategory.traffic.length > 0 && (
              <th
                colSpan={visibleByCategory.traffic.length}
                className="text-left text-[11px] uppercase tracking-wider text-zinc-400 px-2 py-1 bg-zinc-800/40 rounded"
              >
                Traffic Resource
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {visibleModels.map((model) => (
            <tr key={model}>
              <th className="text-right pr-2 text-xs font-medium text-zinc-200 align-middle whitespace-nowrap">
                {model}
              </th>
              {orderedMetrics.map((m) => {
                const cell = byKey.get(`${model}${m.metric}`);
                return (
                  <td key={m.metric} className="p-0">
                    <HeatMapTile cell={cell} colorScheme={colorScheme} />
                  </td>
                );
              })}
            </tr>
          ))}
          {/* Row N+1: metric labels under the columns. Rotated 45° for
              density. The cell uses `position: relative` + absolutely-
              positioned text so the rotated text doesn't push the row's
              intrinsic height and doesn't introduce extra space above
              the labels. The container has `pb-48` to reserve room
              below the table for these labels without clipping.

              The display label is shortLabel(description) — anything
              after " (" is dropped — so verbose catalog descriptions
              like "FQDN address objects (pushed + local; subset of
              address objects)" render as just "FQDN address objects"
              on the axis. Full description is preserved in the title
              hover for operators who need the detail. */}
          <tr>
            <th className="w-24" />
            {orderedMetrics.map((m) => (
              <th
                key={m.metric}
                style={{
                  width: 44,
                  height: 8,
                  position: "relative",
                  verticalAlign: "top",
                }}
              >
                <div
                  className="absolute text-[10px] text-zinc-400 whitespace-nowrap"
                  style={{
                    top: 4,
                    left: "50%",
                    transform: "rotate(45deg)",
                    transformOrigin: "left top",
                  }}
                  title={m.metric_description}
                >
                  {shortLabel(m.metric_description)}
                </div>
              </th>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

// ----- helpers -----

type MetricInfo = {
  metric: string;
  metric_description: string;
  category: MetricCategory | "unknown";
};

function deriveOptions(cells: HeatmapCell[]): {
  models: string[];
  metricsByCategory: Record<MetricCategory, MetricInfo[]>;
  deviceGroups: string[];
  templateStacks: string[];
  allMetrics: MetricInfo[];
} {
  const models = Array.from(new Set(cells.map((c) => c.model))).sort();
  const metricSet = new Map<string, MetricInfo>();
  for (const c of cells) {
    if (!metricSet.has(c.metric)) {
      metricSet.set(c.metric, {
        metric: c.metric,
        metric_description: c.metric_description,
        category: c.category,
      });
    }
  }
  const allMetrics = Array.from(metricSet.values()).sort((a, b) =>
    a.metric_description.localeCompare(b.metric_description),
  );
  const metricsByCategory: Record<MetricCategory, MetricInfo[]> = {
    config: allMetrics.filter((m) => m.category === "config"),
    system: allMetrics.filter((m) => m.category === "system"),
    traffic: allMetrics.filter((m) => m.category === "traffic"),
  };
  // device_group / template_stack aren't currently in the heatmap response
  // (the endpoint filters by them but doesn't expose them per-row). Phase
  // 10's table endpoint does. For now the dropdowns are empty until the
  // operator types something — future polish: dedicated /capacity/filter-options
  // endpoint, or include them in the heatmap response.
  return {
    models,
    metricsByCategory,
    deviceGroups: [],
    templateStacks: [],
    allMetrics,
  };
}

/**
 * Strip any "(...)" parenthetical from a metric description for the
 * compact rotated-axis label. Examples:
 *   "Address objects (pushed + local)" → "Address objects"
 *   "FQDN address objects (pushed + local; subset of ...)" → "FQDN address objects"
 *   "Active sessions vs. session table maximum" → "Active sessions vs. session table maximum"
 *     (no parenthetical, returned as-is)
 *
 * The full description is preserved separately in the cell's `title`
 * attribute so hover still shows the verbose form. This mirrors PA's
 * Capacity Analyzer where axis labels are deliberately terse.
 */
function shortLabel(description: string): string {
  const idx = description.indexOf(" (");
  if (idx === -1) return description;
  return description.slice(0, idx);
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

function ColorSchemeToggle({
  value,
  onChange,
}: {
  value: "multi" | "mono";
  onChange: (v: "multi" | "mono") => void;
}) {
  return (
    <div className="inline-flex border border-zinc-700 rounded overflow-hidden">
      <button
        onClick={() => onChange("multi")}
        className={
          "px-2 py-1 text-[11px] " +
          (value === "multi"
            ? "bg-zinc-700 text-zinc-100"
            : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
        }
      >
        Multicolor
      </button>
      <button
        onClick={() => onChange("mono")}
        className={
          "px-2 py-1 text-[11px] " +
          (value === "mono"
            ? "bg-zinc-700 text-zinc-100"
            : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
        }
      >
        Monochrome
      </button>
    </div>
  );
}

function CapacityFilterSlider({
  min,
  max,
  onChange,
}: {
  min: number;
  max: number;
  onChange: (lo: number, hi: number) => void;
}) {
  // Two-handle range slider built from two range inputs stacked on a
  // colored gradient. Pure CSS — no library needed.
  return (
    <div className="px-4 py-3 border-b border-zinc-800">
      <div className="flex items-center gap-3 text-[11px] text-zinc-500">
        <span>Capacity Filter</span>
        <div className="relative flex-1 h-6">
          {/* Visual gradient track behind the inputs */}
          <div
            className="absolute inset-x-0 top-1/2 -translate-y-1/2 h-2 rounded"
            style={{
              background:
                "linear-gradient(to right, rgb(20 83 45 / 0.6) 0%, rgb(20 83 45 / 0.6) 60%, rgb(120 53 15 / 0.6) 60%, rgb(120 53 15 / 0.6) 80%, rgb(127 29 29 / 0.6) 80%, rgb(127 29 29 / 0.6) 100%)",
            }}
          />
          <input
            type="range"
            min={0}
            max={100}
            value={min}
            onChange={(e) =>
              onChange(Math.min(Number(e.target.value), max - 1), max)
            }
            className="absolute inset-x-0 top-1/2 -translate-y-1/2 w-full appearance-none bg-transparent pointer-events-auto"
            style={{ height: 6 }}
          />
          <input
            type="range"
            min={0}
            max={100}
            value={max}
            onChange={(e) =>
              onChange(min, Math.max(Number(e.target.value), min + 1))
            }
            className="absolute inset-x-0 top-1/2 -translate-y-1/2 w-full appearance-none bg-transparent pointer-events-auto"
            style={{ height: 6 }}
          />
        </div>
        <span className="font-mono text-zinc-300 w-16 text-right">
          {min}% – {max}%
        </span>
      </div>
    </div>
  );
}
