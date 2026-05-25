import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { Package, Plus, RefreshCw, Rocket, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import type {
  ActiveTask,
  BulkStageSummary,
  BulkSummary,
  Credential,
  Device,
  Panorama,
  PreCheckResult,
  SnapshotDiff,
} from "@/lib/types";
import { MultiSelectFilter } from "@/components/ui/MultiSelectFilter";
import { relTime } from "@/components/ui/Field";
import { DeviceRow } from "@/components/devices/DeviceRow";
import { PanoramaHealthBanner } from "@/components/devices/PanoramaHealthBanner";
import { BulkPrecheckCard } from "@/components/devices/BulkPrecheckCard";
import { BulkStageCard } from "@/components/devices/BulkStageCard";
import { VersionPickerModal } from "@/components/devices/VersionPickerModal";
import { NewJobModal } from "@/components/devices/NewJobModal";
import { AddDeviceForm } from "@/components/devices/AddDeviceForm";
import { SnapshotDiffViewer } from "@/components/devices/SnapshotDiffViewer";
import { EditDeviceModal } from "@/components/devices/EditDeviceModal";
import {
  ColumnsMenu,
  OPTIONAL_COLUMNS,
  type OptionalColumnKey,
} from "@/components/devices/ColumnsMenu";
import { ResizableTh } from "@/components/devices/ResizableTh";
import { useColumnWidths, type ColumnKey } from "@/components/devices/useColumnWidths";

/**
 * The Devices page: top-level state, the queries that drive everything else,
 * the toolbar (filters + bulk actions + refresh), and the table itself.
 * Heavier components (row + expansion, modals, progress cards, banner) live
 * in src/components/devices/. Reusable bits (filter, label helpers) live in
 * src/components/ui/. Shared types in src/lib/types.ts.
 *
 * State stored here that the kids don't need to know about:
 *   - which rows are expanded
 *   - which devices are selected (for bulk actions)
 *   - which bulk run / stage run is currently active (for live progress)
 *   - which modals are open
 *   - per-row pre-check state (running / latest result this session)
 *
 * Filters live in the URL so deep-links from the dashboard tiles work.
 */
export default function Devices() {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useSearchParams();

  const filterDg = (searchParams.get("dg") ?? "").split(",").filter(Boolean);
  const filterTs = (searchParams.get("ts") ?? "").split(",").filter(Boolean);
  const filterPc = (searchParams.get("precheck") ?? "").split(",").filter(Boolean);
  // HA-pair grouping is on by default — it's the safe choice for a fleet
  // with HA. Operators who prefer raw alphabetical order can toggle it off.
  const groupHA = (searchParams.get("groupHA") ?? "1") !== "0";

  const updateFilter = (key: "dg" | "ts" | "precheck", values: string[]) => {
    const next = new URLSearchParams(searchParams);
    if (values.length === 0) next.delete(key);
    else next.set(key, values.join(","));
    setSearchParams(next, { replace: true });
  };

  const setGroupHA = (on: boolean) => {
    const next = new URLSearchParams(searchParams);
    if (on) next.delete("groupHA");
    else next.set("groupHA", "0");
    setSearchParams(next, { replace: true });
  };

  // Hidden columns persisted to the URL as `?hide=ts,dg`. Tracking the
  // HIDDEN set rather than the visible set keeps URLs clean when nothing
  // is hidden (the common case). The full set of valid keys is restricted
  // to OPTIONAL_COLUMNS so a stale URL with a renamed column doesn't
  // accidentally hide something unrelated.
  const validKeys = new Set(OPTIONAL_COLUMNS.map((c) => c.key));
  const hiddenCols = new Set<OptionalColumnKey>(
    (searchParams.get("hide") ?? "")
      .split(",")
      .filter((k): k is OptionalColumnKey => validKeys.has(k as OptionalColumnKey)),
  );
  const setHiddenCols = (next: Set<OptionalColumnKey>) => {
    const nextParams = new URLSearchParams(searchParams);
    if (next.size === 0) nextParams.delete("hide");
    else nextParams.set("hide", [...next].join(","));
    setSearchParams(nextParams, { replace: true });
  };

  const colVisible = (key: OptionalColumnKey) => !hiddenCols.has(key);
  // Count of visible columns — used for the expanded-row colSpan so the
  // detail panel always stretches across the full visible table.
  const visibleColCount =
    /* checkbox */ 1
    + /* expander */ 1
    + /* Name */ 1
    + OPTIONAL_COLUMNS.filter((c) => colVisible(c.key)).length
    + /* Actions */ 1;

  // ---- column widths (drag-to-resize + auto-fit) ----
  const { widths: colWidths, setWidth: setColWidth, setAll: setAllColWidths, reset: resetColWidths } = useColumnWidths();
  const hasCustomWidths = Object.keys(colWidths).length > 0;

  // The table ref lets us measure every <th>'s current rendered width when
  // the user clicks Auto-fit. We capture them with data-col-key markers
  // (set on each ResizableTh / static th below) so we can correlate the
  // measured DOM with our ColumnKey enum without holding a ref per column.
  const tableRef = useRef<HTMLTableElement>(null);

  const autoFitColumns = () => {
    const tbl = tableRef.current;
    if (!tbl) return;
    const next: Record<string, number> = {};
    tbl.querySelectorAll<HTMLTableCellElement>("thead th[data-col-key]").forEach((th) => {
      const key = th.dataset.colKey;
      if (!key) return;
      // round to whole pixels so we don't drift sub-pixel values into
      // localStorage and re-render endlessly.
      next[key] = Math.round(th.getBoundingClientRect().width);
    });
    setAllColWidths(next as typeof colWidths);
  };

  // When the user starts dragging a single column, pin every OTHER visible
  // column to its current rendered width first. Otherwise growing one
  // column would cause the rest to silently redistribute and the operator
  // sees a confusing slinky effect. After this pin, only the dragged
  // column actually changes during the drag.
  const pinSiblingsIfNeeded = (draggingKey: ColumnKey) => {
    if (Object.keys(colWidths).length > 1) return; // already pinned
    const tbl = tableRef.current;
    if (!tbl) return;
    const next: Record<string, number> = { ...colWidths };
    tbl.querySelectorAll<HTMLTableCellElement>("thead th[data-col-key]").forEach((th) => {
      const key = th.dataset.colKey;
      if (!key || key === draggingKey) return;
      if (next[key] == null) {
        next[key] = Math.round(th.getBoundingClientRect().width);
      }
    });
    setAllColWidths(next as typeof colWidths);
  };

  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [showAdd, setShowAdd] = useState(false);
  const [precheckResults, setPrecheckResults] = useState<Record<number, PreCheckResult>>({});
  const [precheckRunning, setPrecheckRunning] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [activeBulkId, setActiveBulkId] = useState<number | null>(null);
  const [activeBulkTargets, setActiveBulkTargets] = useState<number[]>([]);
  const [activeStageId, setActiveStageId] = useState<number | null>(null);
  const [activeStageTargets, setActiveStageTargets] = useState<number[]>([]);
  const [showNewJob, setShowNewJob] = useState(false);
  const [versionPickerOpen, setVersionPickerOpen] = useState(false);
  const [diffViewer, setDiffViewer] = useState<SnapshotDiff | null>(null);
  const [editingDeviceId, setEditingDeviceId] = useState<number | null>(null);

  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => api<Device[]>("/api/devices"),
    refetchInterval: 30_000,
  });
  const panos = useQuery({ queryKey: ["panoramas"], queryFn: () => api<Panorama[]>("/api/panoramas") });
  const creds = useQuery({ queryKey: ["credentials"], queryFn: () => api<Credential[]>("/api/credentials") });

  // Cheap poll: drives the per-row in-progress badge so an operator
  // glancing at the Devices page sees that an upgrade is mid-flight without
  // having to navigate to Jobs. 5s is the same cadence as the Jobs page.
  const activeTasks = useQuery({
    queryKey: ["active-tasks"],
    queryFn: () => api<ActiveTask[]>("/api/jobs/active-tasks"),
    refetchInterval: 5000,
  });

  const activeByDevice = useMemo(() => {
    const m = new Map<number, ActiveTask>();
    for (const t of activeTasks.data ?? []) m.set(t.device_id, t);
    return m;
  }, [activeTasks.data]);

  // Named pre-check sets, managed in Settings. `selectedSetId === null`
  // means "use the backend's built-in default" — what we did before custom
  // sets existed. When a set is marked is_default by the operator, we
  // auto-pick it on first load.
  type PrecheckSet = {
    id: number;
    name: string;
    checks: string[];
    is_default: boolean;
  };
  const precheckSets = useQuery({
    queryKey: ["precheck-sets"],
    queryFn: () => api<PrecheckSet[]>("/api/precheck-sets"),
    staleTime: 60_000,
  });
  const [selectedSetId, setSelectedSetId] = useState<number | null>(null);
  useEffect(() => {
    if (selectedSetId == null && precheckSets.data) {
      const def = precheckSets.data.find((s) => s.is_default);
      if (def) setSelectedSetId(def.id);
    }
  }, [precheckSets.data, selectedSetId]);
  const activeChecks = useMemo(() => {
    if (selectedSetId == null) return null;
    return precheckSets.data?.find((s) => s.id === selectedSetId)?.checks ?? null;
  }, [selectedSetId, precheckSets.data]);

  const refreshAll = useMutation({
    mutationFn: () => api<{ results: Record<string, number> }>("/api/panoramas/refresh-all", { method: "POST" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["devices"] }),
  });

  // Bulk pre-check: kick off → poll status → invalidate device list when done
  // so per-row badges pick up the new latest_precheck.
  const bulkStatus = useQuery({
    queryKey: ["bulk-precheck", activeBulkId],
    queryFn: () => api<BulkSummary>(`/api/devices/precheck/bulk/${activeBulkId}`),
    enabled: activeBulkId !== null,
    refetchInterval: (q) => {
      const data = q.state.data as BulkSummary | undefined;
      return data && data.pending_count === 0 ? false : 2000;
    },
  });
  useEffect(() => {
    if (bulkStatus.data && bulkStatus.data.pending_count === 0) {
      qc.invalidateQueries({ queryKey: ["devices"] });
    }
  }, [bulkStatus.data?.pending_count, qc, bulkStatus.data]);

  const bulkStart = useMutation({
    mutationFn: (deviceIds: number[]) =>
      api<BulkSummary>("/api/devices/precheck/bulk", {
        method: "POST",
        // Pass `checks` only when the operator has picked a set. Omitting
        // it lets the backend fall back to DEFAULT_READINESS_CHECKS.
        body: { device_ids: deviceIds, ...(activeChecks ? { checks: activeChecks } : {}) },
      }).then((s) => ({ summary: s, deviceIds })),
    onSuccess: ({ summary, deviceIds }) => {
      setActiveBulkId(summary.bulk_run_id);
      setActiveBulkTargets(deviceIds);
      setSelected(new Set());
    },
  });

  // Same shape for bulk pre-stage. Longer poll interval because downloads take
  // several minutes per device — no need to hammer.
  const stageStatus = useQuery({
    queryKey: ["bulk-stage", activeStageId],
    queryFn: () => api<BulkStageSummary>(`/api/devices/stage/bulk/${activeStageId}`),
    enabled: activeStageId !== null,
    refetchInterval: (q) => {
      const data = q.state.data as BulkStageSummary | undefined;
      return data && data.pending_count === 0 ? false : 5000;
    },
  });
  useEffect(() => {
    if (stageStatus.data && stageStatus.data.pending_count === 0) {
      qc.invalidateQueries({ queryKey: ["devices"] });
    }
  }, [stageStatus.data?.pending_count, qc, stageStatus.data]);

  const stageStart = useMutation({
    mutationFn: ({ deviceIds, version }: { deviceIds: number[]; version: string }) =>
      api<BulkStageSummary>("/api/devices/stage/bulk", {
        method: "POST",
        body: { device_ids: deviceIds, version },
      }).then((s) => ({ summary: s, deviceIds })),
    onSuccess: ({ summary, deviceIds }) => {
      setActiveStageId(summary.bulk_run_id);
      setActiveStageTargets(deviceIds);
      setSelected(new Set());
    },
  });

  function openVersionPicker() {
    if (selected.size === 0) return;
    setVersionPickerOpen(true);
  }
  function startStageWithVersion(version: string) {
    setVersionPickerOpen(false);
    stageStart.mutate({ deviceIds: [...selected], version });
  }

  async function runPrecheck(deviceId: number) {
    setPrecheckRunning((s) => new Set(s).add(deviceId));
    setExpanded((s) => new Set(s).add(deviceId));
    try {
      const result = await api<PreCheckResult>(`/api/devices/${deviceId}/precheck`, {
        method: "POST",
        body: activeChecks ? { checks: activeChecks } : {},
      });
      setPrecheckResults((m) => ({ ...m, [deviceId]: result }));
      // refresh devices so the badge picks up the new latest_precheck
      qc.invalidateQueries({ queryKey: ["devices"] });
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Pre-check failed";
      setPrecheckResults((m) => ({
        ...m,
        [deviceId]: {
          id: null,
          device_id: deviceId,
          ran_at: new Date().toISOString(),
          overall_severity: "fail",
          pass_count: 0, warn_count: 0, fail_count: 1, skip_count: 0,
          results: { error: { raw_state: false, raw_reason: msg, severity: "fail", reason: msg } },
          error: msg,
        },
      }));
    } finally {
      setPrecheckRunning((s) => {
        const next = new Set(s);
        next.delete(deviceId);
        return next;
      });
    }
  }

  async function probeDevice(deviceId: number) {
    try {
      await api(`/api/devices/${deviceId}/probe`, { method: "POST" });
      qc.invalidateQueries({ queryKey: ["devices"] });
    } catch (err) {
      alert(`Probe failed: ${err instanceof Error ? err.message : err}`);
    }
  }

  async function patchDevice(deviceId: number, body: Partial<Device>) {
    await api(`/api/devices/${deviceId}`, { method: "PATCH", body });
    qc.invalidateQueries({ queryKey: ["devices"] });
  }

  const all = devices.data ?? [];
  const dgs = useMemo(
    () => Array.from(new Set(all.map((d) => d.device_group).filter((x): x is string => !!x))).sort(),
    [all],
  );
  const tss = useMemo(
    () => Array.from(new Set(all.map((d) => d.template_stack).filter((x): x is string => !!x))).sort(),
    [all],
  );

  const baseFiltered = all.filter((d) => {
    if (filterDg.length > 0 && !(d.device_group && filterDg.includes(d.device_group))) return false;
    if (filterTs.length > 0 && !(d.template_stack && filterTs.includes(d.template_stack))) return false;
    if (filterPc.length > 0) {
      const sev = d.latest_precheck?.overall_severity ?? "none";
      if (!filterPc.includes(sev)) return false;
    }
    return true;
  });

  // Index of every device (filtered OR not) by id, so the grouping logic
  // can look up an HA peer even when the peer was filtered out (we still
  // want to surface "this device's peer is hidden by your filter").
  const byId = useMemo(() => new Map(all.map((d) => [d.id, d])), [all]);

  // Build a stable HA-pair-key for each device. Paired devices share a key
  // (the smaller of the two ids); standalone devices get a unique key.
  const haPairKey = (d: Device): string =>
    d.ha_peer_id != null ? `pair-${Math.min(d.id, d.ha_peer_id)}` : `solo-${d.id}`;

  // Sort filtered devices so HA peers are adjacent (active first, passive
  // second). When grouping is off, fall back to alphabetical.
  const filtered = useMemo(() => {
    const list = [...baseFiltered];
    if (!groupHA) {
      list.sort((a, b) => a.name.localeCompare(b.name));
      return list;
    }
    // Map: pair-key -> sort key (alphabetic name of the pair's lowest-name
    // member). Two devices in the same pair land next to each other; pairs
    // sort against each other by that lowest name.
    const groupSortKey = new Map<string, string>();
    for (const d of list) {
      const key = haPairKey(d);
      const prev = groupSortKey.get(key);
      if (prev == null || d.name.localeCompare(prev) < 0) {
        groupSortKey.set(key, d.name);
      }
    }
    list.sort((a, b) => {
      const ka = haPairKey(a);
      const kb = haPairKey(b);
      if (ka !== kb) {
        return (groupSortKey.get(ka) ?? "").localeCompare(groupSortKey.get(kb) ?? "");
      }
      // Within a pair: active first, then passive, then unknown — gives the
      // operator a consistent "who's running prod right now" reading order.
      const roleRank = (r: string) =>
        r === "active" ? 0 : r === "passive" ? 1 : 2;
      return roleRank(a.ha_role) - roleRank(b.ha_role);
    });
    return list;
  }, [baseFiltered, groupHA]);

  // For each row index, compute pair metadata used for visual grouping +
  // selection helpers. `pairColor` is a deterministic accent so two members
  // of the same pair get the same stripe; `pairPosition` is "first" or
  // "second" so we can round borders nicely; `pairPeerVisible` tells the
  // row whether its peer is currently visible (drives the "select pair"
  // hint).
  const pairMeta = useMemo(() => {
    const result = new Map<
      number,
      { color: string; position: "first" | "second" | "solo"; peerVisible: boolean }
    >();
    const visibleIds = new Set(filtered.map((d) => d.id));
    // 6 muted accents — cycled by pair index so adjacent pairs are visually distinct.
    const palette = [
      "border-l-sky-700",
      "border-l-emerald-700",
      "border-l-fuchsia-700",
      "border-l-amber-700",
      "border-l-indigo-700",
      "border-l-rose-700",
    ];
    let pairCount = 0;
    const colorByKey = new Map<string, string>();
    filtered.forEach((d, idx) => {
      const key = haPairKey(d);
      const isPair = d.ha_peer_id != null;
      if (!isPair) {
        result.set(d.id, { color: "", position: "solo", peerVisible: false });
        return;
      }
      if (!colorByKey.has(key)) {
        colorByKey.set(key, palette[pairCount % palette.length]);
        pairCount += 1;
      }
      const color = colorByKey.get(key) ?? "";
      const prev = filtered[idx - 1];
      const next = filtered[idx + 1];
      const sameAsPrev = prev && haPairKey(prev) === key;
      const sameAsNext = next && haPairKey(next) === key;
      const position: "first" | "second" | "solo" =
        sameAsPrev ? "second" : sameAsNext ? "first" : "solo";
      result.set(d.id, {
        color,
        position,
        peerVisible: d.ha_peer_id != null && visibleIds.has(d.ha_peer_id),
      });
    });
    return result;
  }, [filtered]);

  const lastRefresh = useMemo(() => {
    const times = all.map((d) => d.last_refresh_at).filter((x): x is string => !!x).map((t) => new Date(t).getTime());
    return times.length ? new Date(Math.max(...times)) : null;
  }, [all]);

  const toggle = (id: number) => {
    const next = new Set(expanded);
    next.has(id) ? next.delete(id) : next.add(id);
    setExpanded(next);
  };

  const toggleSelected = (id: number) => {
    const next = new Set(selected);
    next.has(id) ? next.delete(id) : next.add(id);
    setSelected(next);
  };

  // Select-or-deselect both members of an HA pair as a unit. Convenient
  // when you want to upgrade a whole pair at once — clicking either box +
  // this button selects the partner too.
  const togglePairSelected = (id: number) => {
    const d = byId.get(id);
    if (!d || d.ha_peer_id == null) return toggleSelected(id);
    const peer = byId.get(d.ha_peer_id);
    const both = new Set(selected);
    const anySelected = both.has(d.id) || (peer && both.has(peer.id));
    if (anySelected) {
      both.delete(d.id);
      if (peer) both.delete(peer.id);
    } else {
      both.add(d.id);
      if (peer) both.add(peer.id);
    }
    setSelected(both);
  };

  const filteredIds = filtered.map((d) => d.id);
  const allFilteredSelected = filteredIds.length > 0 && filteredIds.every((id) => selected.has(id));
  const toggleSelectAllFiltered = () => {
    if (allFilteredSelected) {
      const next = new Set(selected);
      filteredIds.forEach((id) => next.delete(id));
      setSelected(next);
    } else {
      const next = new Set(selected);
      filteredIds.forEach((id) => next.add(id));
      setSelected(next);
    }
  };

  const deviceCreds = (creds.data ?? []).filter((c) => c.scope === "device");

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Devices</h1>
          <p className="text-sm text-slate-400">
            {lastRefresh ? `Last refreshed ${relTime(lastRefresh)}` : "Not refreshed yet"} ·
            {" "}{all.filter((d) => d.connected).length}/{all.length} connected
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <MultiSelectFilter
            label="Device groups"
            options={dgs.map((d) => ({ value: d, label: d }))}
            selected={filterDg}
            onChange={(vals) => updateFilter("dg", vals)}
          />
          <MultiSelectFilter
            label="Template stacks"
            options={tss.map((d) => ({ value: d, label: d }))}
            selected={filterTs}
            onChange={(vals) => updateFilter("ts", vals)}
          />
          <ColumnsMenu
            hidden={hiddenCols}
            onChange={setHiddenCols}
            hasCustomWidths={hasCustomWidths}
            onAutoFit={autoFitColumns}
            onResetWidths={resetColWidths}
          />
          <button
            onClick={() => setGroupHA(!groupHA)}
            className={`inline-flex items-center gap-1.5 rounded-md border px-3 py-2 text-xs font-medium ${
              groupHA
                ? "border-indigo-600 bg-indigo-950/40 text-indigo-200"
                : "border-slate-700 text-slate-300 hover:bg-slate-800"
            }`}
            title={
              groupHA
                ? "HA pairs are shown adjacent with a colored accent. Click to disable."
                : "Show devices alphabetically (HA pairs not grouped). Click to enable grouping."
            }
          >
            {groupHA ? "Grouping: HA pairs" : "Grouping: off"}
          </button>
          <MultiSelectFilter
            label="Pre-check"
            options={[
              { value: "pass", label: "Pass" },
              { value: "warn", label: "Warning" },
              { value: "fail", label: "Failing" },
              { value: "skip", label: "All-skipped" },
              { value: "none", label: "Never checked" },
            ]}
            selected={filterPc}
            onChange={(vals) => updateFilter("precheck", vals)}
          />
          <select
            value={selectedSetId ?? ""}
            onChange={(e) => setSelectedSetId(e.target.value ? Number(e.target.value) : null)}
            className="rounded-md border border-slate-700 bg-slate-950 px-2 py-2 text-xs text-slate-300"
            title="Pre-check set to use when running pre-checks. Manage in Settings."
          >
            <option value="">Default checks</option>
            {(precheckSets.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
                {s.is_default ? " ★" : ""}
              </option>
            ))}
          </select>
          {selected.size > 0 && (
            <>
              <button
                onClick={() => bulkStart.mutate([...selected])}
                disabled={bulkStart.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-amber-600 px-3 py-2 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
              >
                <ShieldCheck className="h-4 w-4" />
                {bulkStart.isPending ? "Starting…" : `Pre-check ${selected.size}`}
              </button>
              <button
                onClick={openVersionPicker}
                disabled={stageStart.isPending}
                className="inline-flex items-center gap-1.5 rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
                title="Download a PAN-OS image to selected devices without installing"
              >
                <Package className="h-4 w-4" />
                {stageStart.isPending ? "Starting…" : `Stage on ${selected.size}`}
              </button>
              <button
                onClick={() => setShowNewJob(true)}
                className="inline-flex items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-500"
                title="Run the full upgrade flow on selected devices"
              >
                <Rocket className="h-4 w-4" />
                Upgrade {selected.size}
              </button>
            </>
          )}
          <button
            onClick={() => setShowAdd((s) => !s)}
            className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800"
          >
            <Plus className="h-4 w-4" />
            Add device
          </button>
          <button
            onClick={() => refreshAll.mutate()}
            disabled={refreshAll.isPending || (panos.data ?? []).length === 0}
            className="inline-flex items-center gap-1.5 rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
            title={
              (panos.data ?? []).length === 0
                ? "Add a Panorama in Settings first"
                : "Pull device list from each Panorama. Uses Panorama's cached managed-device view — for live state on a specific device, expand its row and click Probe."
            }
          >
            <RefreshCw className={`h-4 w-4 ${refreshAll.isPending ? "animate-spin" : ""}`} />
            {refreshAll.isPending ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      </div>

      {showAdd && (
        <AddDeviceForm
          credentials={deviceCreds}
          onClose={() => setShowAdd(false)}
          onCreated={() => {
            setShowAdd(false);
            qc.invalidateQueries({ queryKey: ["devices"] });
          }}
        />
      )}

      {refreshAll.error && (
        <div className="rounded-md border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          Refresh failed: {(refreshAll.error as Error).message}
        </div>
      )}

      <PanoramaHealthBanner panoramas={panos.data ?? []} />

      {bulkStatus.data && (
        <BulkPrecheckCard
          summary={bulkStatus.data}
          devices={all}
          targetIds={activeBulkTargets}
          onDismiss={() => { setActiveBulkId(null); setActiveBulkTargets([]); }}
          onRefresh={() => bulkStatus.refetch()}
        />
      )}

      {stageStatus.data && (
        <BulkStageCard
          summary={stageStatus.data}
          devices={all}
          targetIds={activeStageTargets}
          onDismiss={() => { setActiveStageId(null); setActiveStageTargets([]); }}
          onRefresh={() => stageStatus.refetch()}
        />
      )}

      {versionPickerOpen && (
        <VersionPickerModal
          deviceIds={[...selected]}
          devices={all}
          onClose={() => setVersionPickerOpen(false)}
          onPick={startStageWithVersion}
        />
      )}

      {showNewJob && (
        <NewJobModal
          deviceIds={[...selected]}
          devices={all}
          onClose={() => setShowNewJob(false)}
        />
      )}

      <div className="rounded-lg border border-slate-800 overflow-x-auto">
        <table
          ref={tableRef}
          className="w-full min-w-[800px] text-sm"
          // table-layout: fixed once any custom width is set, so the explicit
          // widths actually take effect. Auto-layout otherwise lets the
          // browser size to content (the default before any drag/auto-fit).
          style={{ tableLayout: hasCustomWidths ? "fixed" : "auto" }}
        >
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <ResizableTh
                colKey="select"
                width={colWidths.select}
                className="px-2"
                onResize={setColWidth}
                onResizeStart={() => pinSiblingsIfNeeded("select")}
              >
                <input
                  type="checkbox"
                  checked={allFilteredSelected}
                  onChange={toggleSelectAllFiltered}
                  title={allFilteredSelected ? "Deselect filtered" : "Select all filtered"}
                />
              </ResizableTh>
              <ResizableTh
                colKey="expand"
                width={colWidths.expand}
                className="px-2"
                onResize={setColWidth}
                onResizeStart={() => pinSiblingsIfNeeded("expand")}
              />
              <ResizableTh
                colKey="name"
                width={colWidths.name}
                onResize={setColWidth}
                onResizeStart={() => pinSiblingsIfNeeded("name")}
              >
                Name
              </ResizableTh>
              {colVisible("model") && (
                <ResizableTh
                  colKey="model"
                  width={colWidths.model}
                  onResize={setColWidth}
                  onResizeStart={() => pinSiblingsIfNeeded("model")}
                >
                  Model
                </ResizableTh>
              )}
              {colVisible("panos") && (
                <ResizableTh
                  colKey="panos"
                  width={colWidths.panos}
                  onResize={setColWidth}
                  onResizeStart={() => pinSiblingsIfNeeded("panos")}
                >
                  PAN-OS
                </ResizableTh>
              )}
              {colVisible("ha") && (
                <ResizableTh
                  colKey="ha"
                  width={colWidths.ha}
                  onResize={setColWidth}
                  onResizeStart={() => pinSiblingsIfNeeded("ha")}
                >
                  HA
                </ResizableTh>
              )}
              {colVisible("dg") && (
                <ResizableTh
                  colKey="dg"
                  width={colWidths.dg}
                  onResize={setColWidth}
                  onResizeStart={() => pinSiblingsIfNeeded("dg")}
                >
                  Device Group
                </ResizableTh>
              )}
              {colVisible("ts") && (
                <ResizableTh
                  colKey="ts"
                  width={colWidths.ts}
                  onResize={setColWidth}
                  onResizeStart={() => pinSiblingsIfNeeded("ts")}
                >
                  Template Stack
                </ResizableTh>
              )}
              {colVisible("status") && (
                <ResizableTh
                  colKey="status"
                  width={colWidths.status}
                  onResize={setColWidth}
                  onResizeStart={() => pinSiblingsIfNeeded("status")}
                >
                  Status
                </ResizableTh>
              )}
              {colVisible("precheck") && (
                <ResizableTh
                  colKey="precheck"
                  width={colWidths.precheck}
                  onResize={setColWidth}
                  onResizeStart={() => pinSiblingsIfNeeded("precheck")}
                >
                  Pre-check
                </ResizableTh>
              )}
              <ResizableTh
                colKey="actions"
                width={colWidths.actions}
                align="right"
                onResize={setColWidth}
                onResizeStart={() => pinSiblingsIfNeeded("actions")}
              >
                Actions
              </ResizableTh>
            </tr>
          </thead>
          <tbody>
            {filtered.map((d) => {
              const peer = d.ha_peer_id != null ? byId.get(d.ha_peer_id) : undefined;
              const meta = pairMeta.get(d.id);
              return (
                <DeviceRow
                  key={d.id}
                  d={d}
                  isOpen={expanded.has(d.id)}
                  onToggle={() => toggle(d.id)}
                  onPrecheck={() => runPrecheck(d.id)}
                  onProbe={() => probeDevice(d.id)}
                  onPatch={(body) => patchDevice(d.id, body)}
                  precheck={precheckResults[d.id]}
                  precheckRunning={precheckRunning.has(d.id)}
                  isSelected={selected.has(d.id)}
                  onToggleSelect={() => toggleSelected(d.id)}
                  onViewDiff={(diff) => setDiffViewer(diff)}
                  haPeerName={peer?.name ?? null}
                  pairAccentClass={meta?.color ?? ""}
                  pairPosition={meta?.position ?? "solo"}
                  onTogglePair={
                    d.ha_peer_id != null
                      ? () => togglePairSelected(d.id)
                      : undefined
                  }
                  activeUpgrade={activeByDevice.get(d.id)}
                  onEdit={() => setEditingDeviceId(d.id)}
                  hiddenCols={hiddenCols}
                  visibleColCount={visibleColCount}
                />
              );
            })}
            {filtered.length === 0 && !devices.isLoading && (
              <tr><td colSpan={visibleColCount} className="px-3 py-6 text-center text-slate-500">
                {all.length === 0 ? "No devices yet. Add a Panorama in Settings or click 'Add device' to add a standalone." : "No devices match the current filters."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      {diffViewer && (
        <SnapshotDiffViewer
          diff={diffViewer}
          onClose={() => setDiffViewer(null)}
        />
      )}

      {editingDeviceId != null && (() => {
        const d = byId.get(editingDeviceId);
        if (!d) return null;
        return (
          <EditDeviceModal
            device={d}
            onClose={() => setEditingDeviceId(null)}
          />
        );
      })()}
    </div>
  );
}
