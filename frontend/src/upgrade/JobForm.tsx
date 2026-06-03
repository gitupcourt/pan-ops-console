import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useMemo, useState } from "react";

import {
  api,
  AvailableSoftwareBulkOut,
  Device,
  UpgradeJobCreate,
} from "../api";
import {
  DeviceConnectionStatus,
  deviceConnectionState,
} from "../core/devices/DeviceConnectionStatus";
import { AnimatedEllipsis, Button, Field, Input, Select } from "../core/ui/ui";

/**
 * Create-an-upgrade-job form.
 *
 * Three things the operator picks: WHAT devices to upgrade, TO WHAT
 * version, with WHAT image source. Plus several confirmation/automation
 * toggles that default to "safe" (manual confirmation required at
 * failover) — operators can flip to "automated" once they've reviewed.
 *
 * Image source is xor: either pick a registered PanosImage row (which
 * may be "device-pull" version-only OR uploaded blob) or check
 * `device_pull_image` to skip the registry entirely. Most operators
 * register the version they want once and then pick it; the bare
 * checkbox is the escape hatch for "I haven't pre-registered, just
 * pull it."
 */
export function JobForm({
  onDone,
  initialDeviceIds = [],
}: {
  onDone: () => void;
  /** Pre-checked device IDs when the form is opened — e.g. from
   *  /inventory's "Upgrade selected" handoff via URL params. */
  initialDeviceIds?: number[];
}) {
  const qc = useQueryClient();

  // Fetch the inputs needed to populate the form.
  const devicesQ = useQuery({
    queryKey: ["devices"],
    queryFn: api.listDevices,
  });
  const imagesQ = useQuery({
    queryKey: ["upgrade-images"],
    queryFn: api.listUpgradeImages,
  });
  const precheckSetsQ = useQuery({
    queryKey: ["precheck-sets"],
    queryFn: api.listPrecheckSets,
  });

  // Form state.
  const [name, setName] = useState("");
  const [targetVersion, setTargetVersion] = useState("");
  // "device" = pick from the device-reported list (model-compatible
  // by definition); "custom" = freeform text input as an escape hatch
  // for manually-uploaded images or beta versions not yet in the
  // update server's catalog.
  const [versionMode, setVersionMode] = useState<"device" | "custom">("device");
  // Seed the device selection from the inventory handoff (if any).
  // We intentionally init from the prop ONCE; subsequent prop changes
  // don't reset the selection (rare in practice — the form mounts
  // fresh each time the operator opens it).
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(
    () => new Set(initialDeviceIds),
  );

  // Devices the operator must not upgrade: anything Inventory would show as
  // "offline" (Panorama reports it disconnected, or a direct device's polls
  // are failing). We disable selecting them below AND defensively exclude any
  // that slipped in via the inventory handoff, so a job never launches against
  // a device that's already down. `deviceConnectionState` is the SAME logic
  // the Inventory pill uses, so "offline" means the same thing in both places.
  const offlineDeviceIds = useMemo(() => {
    const ds = devicesQ.data ?? [];
    return new Set(
      ds.filter((d) => deviceConnectionState(d) === "offline").map((d) => d.id),
    );
  }, [devicesQ.data]);
  // The selection minus offline devices — what actually counts, queries, and
  // submits. `selectedDeviceIds` stays the raw checkbox state.
  const effectiveSelectedIds = useMemo(
    () =>
      new Set(
        Array.from(selectedDeviceIds).filter((id) => !offlineDeviceIds.has(id)),
      ),
    [selectedDeviceIds, offlineDeviceIds],
  );

  // Software-availability query for the version picker. Keyed on a
  // stable sorted-CSV of the current selection so any change to the
  // device set re-fetches with fresh model-compatible versions. The
  // device's own check-now is the source of truth for "what can this
  // model install" — no need for us to keep a model → versions table.
  //
  // `enabled` gates the slow per-device check-now call behind both
  //   - operator selected at least one device
  //   - operator chose the device-reported picker (not custom)
  // staleTime: Infinity — call is slow and the catalog changes rarely;
  // a manual "Refresh" button covers post-release re-poll.
  const selectedDeviceIdsArr = useMemo(() => {
    const arr = Array.from(effectiveSelectedIds);
    arr.sort((a, b) => a - b);
    return arr;
  }, [effectiveSelectedIds]);
  const softwareQ = useQuery<AvailableSoftwareBulkOut>({
    queryKey: ["upgrade-software-bulk", selectedDeviceIdsArr.join(",")],
    queryFn: () => api.getDeviceSoftwareBulk(selectedDeviceIdsArr),
    enabled:
      versionMode === "device" && selectedDeviceIdsArr.length > 0,
    staleTime: Infinity,
  });

  // Image source: either an existing PanosImage id (when imageMode = "select")
  // or device_pull_image=true (when imageMode = "pull"). Mutually exclusive.
  type ImageMode = "select" | "pull";
  const [imageMode, setImageMode] = useState<ImageMode>("pull");
  const [selectedImageId, setSelectedImageId] = useState<number | null>(null);

  // Device-picker narrow-down filters. Independent of selection —
  // operators often narrow to a DG, select some devices, then change
  // the filter to a different DG without losing earlier selections.
  // Empty string = "all" for that filter.
  const [filterModel, setFilterModel] = useState<string>("");
  const [filterDeviceGroup, setFilterDeviceGroup] = useState<string>("");
  const [filterTemplateStack, setFilterTemplateStack] = useState<string>("");

  // Precheck set: null = "use the default" (whichever PrecheckSet has
  // is_default=true server-side; seeded as "Standard" by migration
  // 0007). The dropdown's blank/default option leaves the value null.
  const [precheckSetId, setPrecheckSetId] = useState<number | null>(null);

  const [requireFailover, setRequireFailover] = useState(true);
  const [requirePrimaryUpgrade, setRequirePrimaryUpgrade] = useState(false);
  const [autoFailback, setAutoFailback] = useState(false);
  const [autoReboot, setAutoReboot] = useState(false);
  const [autoAckPrecheck, setAutoAckPrecheck] = useState(false);
  const [autoAckPostcheck, setAutoAckPostcheck] = useState(false);
  // "none" = full upgrade; "stage_only" = precheck + image download +
  // pre-snapshot, then stop before install/reboot.
  const [preStageMode, setPreStageMode] = useState<"none" | "stage_only">("none");
  const stageOnly = preStageMode === "stage_only";

  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => {
      const body: UpgradeJobCreate = {
        name,
        target_version: targetVersion,
        device_ids: Array.from(effectiveSelectedIds),
        image_id: imageMode === "select" ? selectedImageId : null,
        device_pull_image: imageMode === "pull",
        require_failover_confirmation: requireFailover,
        require_primary_upgrade_confirmation: requirePrimaryUpgrade,
        auto_failback: autoFailback,
        auto_reboot_after_install: autoReboot,
        pre_stage_mode: preStageMode,
        auto_ack_precheck_failures: autoAckPrecheck,
        auto_ack_postcheck_failures: autoAckPostcheck,
        precheck_set_id: precheckSetId,
      };
      return api.createUpgradeJob(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["upgrade-jobs"] });
      onDone();
    },
    onError: (e: Error) => setErr(e.message),
  });

  const devices = devicesQ.data ?? [];
  const images = imagesQ.data ?? [];

  // Build dropdown options dynamically from the device list — no
  // hardcoded model/DG/TS lists, matching the pattern from the
  // Capacity table view.
  const { models, deviceGroups, templateStacks } = useMemo(
    () => deriveFilterOptions(devices),
    [devices],
  );

  // Filter the visible rows. Selection (selectedDeviceIds) is
  // independent — narrowing the view doesn't deselect anything off-
  // screen, so the operator can build a selection across multiple
  // DG/TS by toggling filters between checks.
  const visibleDevices = useMemo(
    () =>
      devices
        .filter((d) => {
          if (filterModel && d.model !== filterModel) return false;
          if (filterDeviceGroup && d.device_group !== filterDeviceGroup) return false;
          if (filterTemplateStack && d.template_stack !== filterTemplateStack)
            return false;
          return true;
        })
        // Group HA pairs adjacently (same approach as Inventory): sort by a
        // pair key — min(id, ha_peer_id) — so both halves of a pair sit
        // together, then by id for a stable order.
        .sort((a, b) => {
          const ak = a.ha_peer_id == null ? a.id : Math.min(a.id, a.ha_peer_id);
          const bk = b.ha_peer_id == null ? b.id : Math.min(b.id, b.ha_peer_id);
          return ak - bk || a.id - b.id;
        }),
    [devices, filterModel, filterDeviceGroup, filterTemplateStack],
  );

  const anyFilterActive =
    !!filterModel || !!filterDeviceGroup || !!filterTemplateStack;

  // Group the visible rows so HA pairs render as a single bounded, labeled
  // block (not just a left-border tint). Standalone devices render as their
  // own one-row group.
  const pickerGroups = useMemo(
    () => groupDevicesForPicker(visibleDevices),
    [visibleDevices],
  );

  // One device row, shared by the standalone and HA-pair group renderers.
  // Offline devices are dimmed, show a status pill, and can't be checked.
  const renderDeviceRow = (d: Device) => {
    const offline = offlineDeviceIds.has(d.id);
    const role =
      d.ha_role && d.ha_role !== "standalone" && d.ha_role !== "unknown"
        ? d.ha_role
        : null;
    return (
      <tr
        key={d.id}
        className={`border-b border-zinc-800/40 ${
          offline ? "opacity-50" : "hover:bg-zinc-900/30"
        }`}
      >
        <td className="px-3 py-1.5 w-8">
          <input
            type="checkbox"
            checked={effectiveSelectedIds.has(d.id)}
            disabled={offline}
            title={offline ? "Offline — can't be upgraded" : undefined}
            onChange={(e) => {
              const next = new Set(selectedDeviceIds);
              if (e.target.checked) next.add(d.id);
              else next.delete(d.id);
              setSelectedDeviceIds(next);
            }}
          />
        </td>
        <td className="px-3 py-1.5 text-zinc-100">
          {d.name}
          {role && (
            <span className="ml-2 inline-block align-middle rounded border border-blue-800/50 bg-blue-950/50 px-1 py-0.5 text-[9px] uppercase tracking-wide text-blue-300">
              {role}
            </span>
          )}
          {offline && (
            <span className="ml-2 align-middle">
              <DeviceConnectionStatus device={d} variant="pill" />
            </span>
          )}
        </td>
        <td className="px-3 py-1.5 text-zinc-500 text-xs">{d.model ?? "—"}</td>
        <td className="px-3 py-1.5 text-zinc-500 text-xs tabular-nums">
          {d.sw_version ?? "—"}
        </td>
        <td className="px-3 py-1.5 text-zinc-500 text-xs">
          {d.device_group ?? (
            <span className="text-zinc-700 italic">standalone</span>
          )}
        </td>
        <td className="px-3 py-1.5 text-zinc-500 text-xs">{d.source}</td>
      </tr>
    );
  };

  // HA pairs are grouped adjacently in the picker (sorted by pair key above)
  // and visually stitched in the rows below, mirroring the Inventory page.
  // The orchestrator still derives pair grouping from ha_peer_id at job-create
  // time (see `_ha_pair_key_for` in routes/jobs.py), so paired devices upgrade
  // together regardless of how the operator selects them here.

  const canSubmit =
    name.trim().length > 0 &&
    targetVersion.trim().length > 0 &&
    effectiveSelectedIds.size > 0 &&
    (imageMode === "pull" ||
      (imageMode === "select" && selectedImageId != null));

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setErr(null);
        create.mutate();
      }}
      className="border-b border-zinc-800 bg-zinc-950/40 p-4 grid gap-4"
    >
      <Field label="Job name">
        <Input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Q1 fleet refresh"
          required
        />
      </Field>

      <VersionPicker
        mode={versionMode}
        onModeChange={setVersionMode}
        targetVersion={targetVersion}
        onTargetVersionChange={setTargetVersion}
        selectedDeviceCount={effectiveSelectedIds.size}
        softwareQ={softwareQ}
        onDeselectDeviceIds={(ids) =>
          setSelectedDeviceIds((prev) => {
            const next = new Set(prev);
            ids.forEach((id) => next.delete(id));
            return next;
          })
        }
      />

      {/* Device picker */}
      <div className="rounded border border-zinc-800 p-3 grid gap-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Devices ({effectiveSelectedIds.size} selected
            {anyFilterActive
              ? `, ${visibleDevices.length} of ${devices.length} shown`
              : ""}
            )
          </div>
          <div className="flex gap-3 text-xs">
            <button
              type="button"
              className="text-zinc-400 hover:text-zinc-100"
              onClick={() => {
                // "Select all" respects the active filter so the
                // operator can DG-narrow → select-all-in-DG → repeat
                // for another DG.
                const next = new Set(selectedDeviceIds);
                for (const d of visibleDevices)
                  if (!offlineDeviceIds.has(d.id)) next.add(d.id);
                setSelectedDeviceIds(next);
              }}
            >
              Select all visible
            </button>
            <button
              type="button"
              className="text-zinc-400 hover:text-zinc-100"
              onClick={() => setSelectedDeviceIds(new Set())}
            >
              Clear all
            </button>
          </div>
        </div>

        {/* Narrow-down filter row. Filtering is purely a view operation
            — selection persists across filter changes so operators
            can build a selection across multiple DG/TS without
            re-checking each time. */}
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <FilterChip
            label="Model"
            value={filterModel}
            onChange={setFilterModel}
            options={models}
          />
          <FilterChip
            label="Device Group"
            value={filterDeviceGroup}
            onChange={setFilterDeviceGroup}
            options={deviceGroups}
          />
          <FilterChip
            label="Template Stack"
            value={filterTemplateStack}
            onChange={setFilterTemplateStack}
            options={templateStacks}
          />
          {anyFilterActive && (
            <button
              type="button"
              className="text-[11px] text-zinc-500 hover:text-zinc-300 underline"
              onClick={() => {
                setFilterModel("");
                setFilterDeviceGroup("");
                setFilterTemplateStack("");
              }}
            >
              clear filters
            </button>
          )}
        </div>

        <div className="max-h-64 overflow-auto border border-zinc-800 rounded">
          <table className="w-full text-sm">
            {visibleDevices.length === 0 ? (
              <tbody>
                <tr>
                  <td
                    colSpan={6}
                    className="p-4 text-xs text-zinc-500 text-center"
                  >
                    {devices.length === 0
                      ? "No devices in inventory. Add some first."
                      : "No devices match the current filters."}
                  </td>
                </tr>
              </tbody>
            ) : (
              pickerGroups.map((g) =>
                g.kind === "single" ? (
                  <tbody key={`s-${g.device.id}`}>
                    {renderDeviceRow(g.device)}
                  </tbody>
                ) : (
                  // HA pair — a bounded, labeled block, not just a tint.
                  <tbody
                    key={`p-${g.key}`}
                    className="border-y-2 border-blue-500/30 bg-blue-950/10"
                  >
                    <tr>
                      <td colSpan={6} className="px-3 pt-1.5 pb-1">
                        <span className="inline-flex items-center gap-1 rounded border border-blue-800/50 bg-blue-950/50 px-1.5 py-0.5 text-[9px] font-medium uppercase tracking-wider text-blue-300">
                          ⛓ HA pair
                        </span>
                      </td>
                    </tr>
                    {g.members.map((d) => renderDeviceRow(d))}
                  </tbody>
                ),
              )
            )}
          </table>
        </div>
        <p className="text-[11px] text-zinc-500">
          HA-paired devices are upgraded as a unit (secondary first, then
          failover, then primary). Selecting one peer auto-includes the
          other at orchestration time.
        </p>
      </div>

      {/* Image source */}
      <div className="rounded border border-zinc-800 p-3 grid gap-3">
        <div className="text-xs uppercase tracking-wider text-zinc-500">
          Install image source
        </div>
        <Select
          value={imageMode}
          onChange={(e) => setImageMode(e.target.value as ImageMode)}
        >
          <option value="pull">
            Tell each device to pull from updates.paloaltonetworks.com
          </option>
          <option value="select">
            Use a registered image (pre-uploaded or pre-recorded version)
          </option>
        </Select>
        {imageMode === "select" && (
          <Field label="Registered image">
            <Select
              value={selectedImageId ?? ""}
              onChange={(e) =>
                setSelectedImageId(
                  e.target.value === "" ? null : Number(e.target.value),
                )
              }
              required
            >
              <option value="">— pick one —</option>
              {images.map((img) => (
                <option key={img.id} value={img.id}>
                  {img.version}
                  {img.uploaded ? " (uploaded)" : " (device-pull)"}
                  {img.notes ? ` — ${img.notes}` : ""}
                </option>
              ))}
            </Select>
          </Field>
        )}
      </div>

      {/* Precheck set picker */}
      <div className="rounded border border-zinc-800 p-3 grid gap-3">
        <div className="text-xs uppercase tracking-wider text-zinc-500">
          Pre/post-check set
        </div>
        <p className="text-[11px] text-zinc-500">
          Which readiness checks to run before and after each device's
          upgrade. Leave on default to use the system-default set
          (currently "Standard"). Manage available sets at{" "}
          <a
            href="/settings/precheck-sets"
            className="text-blue-400 hover:text-blue-300"
          >
            Settings → Pre/post-check sets
          </a>
          .
        </p>
        <Select
          value={precheckSetId ?? ""}
          onChange={(e) =>
            setPrecheckSetId(
              e.target.value === "" ? null : Number(e.target.value),
            )
          }
        >
          <option value="">— use default —</option>
          {(precheckSetsQ.data ?? []).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
              {s.is_default ? " (default)" : ""}
              {` — ${s.checks.length} check${s.checks.length === 1 ? "" : "s"}`}
            </option>
          ))}
        </Select>
      </div>

      {/* Job mode: full upgrade vs stage-only */}
      <div className="rounded border border-zinc-800 p-3 grid gap-2">
        <div className="text-xs uppercase tracking-wider text-zinc-500">Mode</div>
        <div className="inline-flex w-fit overflow-hidden rounded border border-zinc-700 text-[11px]">
          <button
            type="button"
            onClick={() => setPreStageMode("none")}
            className={
              "px-3 py-1 " +
              (!stageOnly
                ? "bg-zinc-700 text-zinc-100"
                : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
            }
          >
            Full upgrade
          </button>
          <button
            type="button"
            onClick={() => setPreStageMode("stage_only")}
            className={
              "px-3 py-1 " +
              (stageOnly
                ? "bg-zinc-700 text-zinc-100"
                : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
            }
          >
            Stage only
          </button>
        </div>
        <p className="text-[11px] text-zinc-500">
          {stageOnly ? (
            <>
              Runs prechecks, downloads the image, and takes a pre-snapshot on
              each device — then <span className="text-amber-300">stops before
              install/reboot</span>. Use it to pre-stage a fleet ahead of a
              maintenance window; run a normal upgrade later to install (the
              image will already be downloaded). The reboot/failover/postcheck
              settings below don&apos;t apply.
            </>
          ) : (
            "Full upgrade: prechecks → image download → snapshot → install → reboot → postcheck."
          )}
        </p>
      </div>

      {/* Automation / safety toggles */}
      <div className="rounded border border-zinc-800 p-3 grid gap-2">
        <div className="text-xs uppercase tracking-wider text-zinc-500">
          Confirmation gates
        </div>
        <p className="text-[11px] text-zinc-500">
          Off = orchestrator pauses and waits for an operator click at
          this step. On = orchestrator proceeds automatically. Defaults
          err toward "ask the operator."
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5 text-xs">
          <Toggle
            label="Require confirmation before HA failover"
            checked={requireFailover}
            onChange={setRequireFailover}
          />
          <Toggle
            label="Require confirmation before primary HA upgrade"
            checked={requirePrimaryUpgrade}
            onChange={setRequirePrimaryUpgrade}
          />
          <Toggle
            label="Auto-failback to original primary after both upgrade"
            checked={autoFailback}
            onChange={setAutoFailback}
          />
          <Toggle
            label="Auto-reboot devices after install (skip click-to-reboot)"
            checked={autoReboot}
            onChange={setAutoReboot}
          />
          <Toggle
            label="Auto-acknowledge precheck FAIL severities (skip safety gate)"
            checked={autoAckPrecheck}
            onChange={setAutoAckPrecheck}
            danger
          />
          <Toggle
            label="Auto-acknowledge postcheck FAIL severities (skip safety gate)"
            checked={autoAckPostcheck}
            onChange={setAutoAckPostcheck}
            danger
          />
        </div>
      </div>

      {err && (
        <div className="text-xs text-rose-400 whitespace-pre-wrap">{err}</div>
      )}

      <div className="flex gap-2">
        <Button
          type="submit"
          variant="primary"
          disabled={!canSubmit || create.isPending}
        >
          {create.isPending
            ? stageOnly
              ? "Staging…"
              : "Creating…"
            : stageOnly
              ? "Stage devices"
              : "Create job"}
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/**
 * Narrow-down dropdown for the device picker. Renders inline next to
 * its siblings; "All" is the implicit value when nothing is selected.
 * The Select shows up as a chip-style control so the row of chips
 * reads as a compact toolbar above the device list.
 */
function FilterChip({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: string[];
}) {
  return (
    <label className="inline-flex items-center gap-1.5 text-zinc-400">
      <span>{label}:</span>
      <Select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="text-xs"
      >
        <option value="">All</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {o}
          </option>
        ))}
      </Select>
    </label>
  );
}

/**
 * Surface the Model / Device Group / Template Stack values present in
 * the current device list. We dedupe + sort so operators see them
 * in a predictable order. Empty/null values are filtered out — they
 * shouldn't appear in the dropdown because an "all" selection
 * already covers them.
 */
function deriveFilterOptions(devices: Device[]): {
  models: string[];
  deviceGroups: string[];
  templateStacks: string[];
} {
  const models = Array.from(
    new Set(devices.map((d) => d.model).filter((m): m is string => !!m)),
  ).sort();
  const deviceGroups = Array.from(
    new Set(
      devices.map((d) => d.device_group).filter((g): g is string => !!g),
    ),
  ).sort();
  const templateStacks = Array.from(
    new Set(
      devices.map((d) => d.template_stack).filter((t): t is string => !!t),
    ),
  ).sort();
  return { models, deviceGroups, templateStacks };
}

function Toggle({
  label,
  checked,
  onChange,
  danger,
}: {
  label: string;
  checked: boolean;
  onChange: (b: boolean) => void;
  danger?: boolean;
}) {
  return (
    <label
      className={`flex items-center gap-2 cursor-pointer ${
        danger && checked ? "text-amber-300" : "text-zinc-300"
      }`}
    >
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}

// ----- version picker -----

/**
 * Target PAN-OS version picker.
 *
 * Two modes:
 *   - "device" (default) — populate a dropdown from each selected
 *     device's own `request system software check → info` response.
 *     The device only lists versions it can install, so model-
 *     compatibility is automatic (PA-220 won't report 12.x). The
 *     dropdown annotates each version with which/how many selected
 *     devices reported it + downloaded-status + release date.
 *   - "custom" — freeform text input, for operator-uploaded images
 *     or beta versions the update server doesn't yet list.
 *
 * Why a dropdown and not just leave the text input: operators don't
 * remember `12.1.4-h12754`. The dropdown also catches "I picked a
 * version this firewall model can't install" *before* the operator
 * submits — failing fast at form-validate time vs slowly at
 * precheck time.
 */
function VersionPicker({
  mode,
  onModeChange,
  targetVersion,
  onTargetVersionChange,
  selectedDeviceCount,
  softwareQ,
  onDeselectDeviceIds,
}: {
  mode: "device" | "custom";
  onModeChange: (m: "device" | "custom") => void;
  targetVersion: string;
  onTargetVersionChange: (v: string) => void;
  selectedDeviceCount: number;
  softwareQ: UseQueryResult<AvailableSoftwareBulkOut>;
  onDeselectDeviceIds: (ids: number[]) => void;
}) {
  // Aggregate per-version across all selected devices. For each
  // version we collect: which devices report it, which already have
  // it downloaded, and the latest released_on we've seen.
  type Agg = {
    version: string;
    deviceIds: number[];
    downloadedOn: number[];
    latestOn: number[];
    released_on: string | null;
  };
  const aggregates: Agg[] = useMemo(() => {
    const results = softwareQ.data?.results ?? {};
    const byVersion = new Map<string, Agg>();
    for (const [idStr, payload] of Object.entries(results)) {
      const did = Number(idStr);
      for (const e of payload.available) {
        let a = byVersion.get(e.version);
        if (!a) {
          a = {
            version: e.version,
            deviceIds: [],
            downloadedOn: [],
            latestOn: [],
            released_on: e.released_on,
          };
          byVersion.set(e.version, a);
        }
        a.deviceIds.push(did);
        if (e.downloaded) a.downloadedOn.push(did);
        if (e.latest) a.latestOn.push(did);
        if (e.released_on && (!a.released_on || e.released_on > a.released_on)) {
          a.released_on = e.released_on;
        }
      }
    }
    // Sort newest-first by version string. PAN-OS versions don't sort
    // cleanly as plain strings (10.x vs 9.x), so do a tuple compare
    // on the numeric components, then h-suffix.
    const arr = Array.from(byVersion.values());
    arr.sort((a, b) => compareVersionDesc(a.version, b.version));
    return arr;
  }, [softwareQ.data]);

  const deviceCount = Object.keys(softwareQ.data?.results ?? {}).length;
  const errored = Object.values(softwareQ.data?.results ?? {}).filter(
    (r) => r.error,
  );

  // Version-skew guard ("someone forgot to upgrade Panorama first"): which
  // selected devices have a managing Panorama OLDER than the picked target.
  // PAN-OS requires Panorama >= its firewalls, so a target ahead of Panorama
  // is unsupported and can break post-upgrade checks via Panorama.
  // compareVersionDesc(target, pano) < 0 ⟺ target is newer.
  const panoramaSkew = useMemo(() => {
    if (!targetVersion) return [] as { device: string; pano: string }[];
    const results = softwareQ.data?.results ?? {};
    const out: { device: string; pano: string }[] = [];
    for (const r of Object.values(results)) {
      if (
        r.panorama_version &&
        compareVersionDesc(targetVersion, r.panorama_version) < 0
      ) {
        out.push({ device: r.device_name, pano: r.panorama_version });
      }
    }
    return out;
  }, [softwareQ.data, targetVersion]);

  return (
    <div className="rounded border border-zinc-800 p-3 grid gap-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="text-xs uppercase tracking-wider text-zinc-500">
          Target PAN-OS version
        </div>
        <div className="inline-flex border border-zinc-700 rounded overflow-hidden text-[11px]">
          <button
            type="button"
            onClick={() => onModeChange("device")}
            className={
              "px-3 py-1 " +
              (mode === "device"
                ? "bg-zinc-700 text-zinc-100"
                : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
            }
          >
            Pick from device
          </button>
          <button
            type="button"
            onClick={() => onModeChange("custom")}
            className={
              "px-3 py-1 " +
              (mode === "custom"
                ? "bg-zinc-700 text-zinc-100"
                : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
            }
          >
            Custom
          </button>
        </div>
      </div>

      {mode === "custom" ? (
        <>
          <Input
            value={targetVersion}
            onChange={(e) => onTargetVersionChange(e.target.value)}
            placeholder="11.1.4-h7"
            required
          />
          <p className="text-[11px] text-zinc-500">
            Use this for manually-uploaded images or beta versions the
            update server doesn't list. Bypasses the device's
            model-compatibility check — the orchestrator's precheck
            phase will still catch installs that don't apply.
          </p>
        </>
      ) : selectedDeviceCount === 0 ? (
        <p className="text-xs text-zinc-500 italic">
          Select at least one device above to see available versions.
        </p>
      ) : softwareQ.isLoading || softwareQ.isFetching ? (
        <div className="flex items-start gap-2 text-xs text-zinc-400">
          <span
            className="mt-0.5 inline-block h-2 w-2 shrink-0 animate-pulse rounded-full bg-blue-400"
            aria-hidden="true"
          />
          <p>
            Asking {selectedDeviceCount} device
            {selectedDeviceCount === 1 ? "" : "s"} for available versions
            <AnimatedEllipsis />
            <span className="mt-0.5 block text-[11px] text-zinc-500">
              First call is slow — each firewall reaches out to
              updates.paloaltonetworks.com. Hang tight.
            </span>
          </p>
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3">
            <Select
              value={targetVersion}
              onChange={(e) => onTargetVersionChange(e.target.value)}
              required
              className="flex-1"
            >
              <option value="">— pick a version —</option>
              {aggregates.map((a) => {
                const onAll = a.deviceIds.length === deviceCount;
                const dlCount = a.downloadedOn.length;
                const isLatest = a.latestOn.length > 0;
                const compatLabel = onAll
                  ? "all"
                  : `${a.deviceIds.length} of ${deviceCount}`;
                const dlLabel =
                  dlCount === 0
                    ? "not downloaded"
                    : dlCount === deviceCount
                      ? "downloaded on all"
                      : `downloaded on ${dlCount} of ${deviceCount}`;
                const dateLabel = a.released_on
                  ? ` · ${a.released_on}`
                  : "";
                return (
                  <option key={a.version} value={a.version}>
                    {a.version}
                    {isLatest ? " ★" : ""} · {compatLabel} · {dlLabel}
                    {dateLabel}
                  </option>
                );
              })}
            </Select>
            <button
              type="button"
              onClick={() => softwareQ.refetch()}
              className="text-xs text-blue-400 hover:text-blue-300"
              title="Re-poll each device's check-now"
            >
              ↻ refresh
            </button>
          </div>
          {targetVersion && (() => {
            const agg = aggregates.find((a) => a.version === targetVersion);
            const onAll = !!agg && agg.deviceIds.length === deviceCount;
            if (onAll) {
              return (
                <p className="text-[11px] text-emerald-400">
                  ✓ Compatible with all {deviceCount} selected device
                  {deviceCount === 1 ? "" : "s"}.
                </p>
              );
            }
            // Devices that reported successfully but DON'T list the target =
            // model-incompatible. (Errored devices are surfaced separately
            // below — those couldn't report a list at all.)
            const results = softwareQ.data?.results ?? {};
            const incompatible = Object.entries(results).filter(
              ([, r]) =>
                !r.error &&
                !r.available.some((e) => e.version === targetVersion),
            );
            if (incompatible.length === 0) return null;
            const incompatibleIds = incompatible.map(([idStr]) => Number(idStr));
            const names = incompatible.map(([, r]) => r.device_name);
            const n = incompatible.length;
            return (
              <div className="rounded border border-amber-700 bg-amber-900/30 text-amber-200 px-3 py-2 text-[11px]">
                <div className="font-semibold">
                  ⚠ {n} selected device{n === 1 ? "" : "s"} can&apos;t install{" "}
                  {targetVersion}
                </div>
                <div className="mt-0.5">
                  <span className="font-mono text-amber-100">
                    {names.join(", ")}
                  </span>{" "}
                  {n === 1 ? "doesn't" : "don't"} report {targetVersion} as an
                  available version — usually a model-compatibility limit (a
                  given PAN-OS build only supports certain platforms). The
                  orchestrator would fail the install on{" "}
                  {n === 1 ? "it" : "them"}. Deselect {n === 1 ? "it" : "them"}{" "}
                  or run a separate job for the compatible set.
                </div>
                <button
                  type="button"
                  onClick={() => onDeselectDeviceIds(incompatibleIds)}
                  className="mt-1.5 rounded border border-amber-700/60 bg-amber-950/40 px-2 py-0.5 text-[11px] text-amber-100 hover:bg-amber-900/60"
                >
                  Deselect {n} incompatible device{n === 1 ? "" : "s"}
                </button>
              </div>
            );
          })()}
          {errored.length > 0 && (
            <p className="text-[11px] text-rose-400">
              {errored.length} device{errored.length === 1 ? "" : "s"} couldn't
              report available versions:{" "}
              {errored
                .map((e) => `${e.device_name} (${e.error?.split(":")[0]})`)
                .join("; ")}
              . Pick from the union above or use Custom.
            </p>
          )}
        </>
      )}
      {panoramaSkew.length > 0 && (
        <div className="rounded border border-amber-700 bg-amber-900/30 text-amber-200 px-3 py-2 text-[11px]">
          <div className="font-semibold">⚠ Target is newer than Panorama</div>
          <div className="mt-0.5">
            {targetVersion} is newer than the managing Panorama for{" "}
            {panoramaSkew
              .map((s) => `${s.device} (Panorama ${s.pano})`)
              .join(", ")}
            . PAN-OS requires Panorama to run ≥ its firewalls — upgrading a
            firewall ahead of Panorama is unsupported and can break post-upgrade
            checks through Panorama. Consider upgrading Panorama first.
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Compare PAN-OS version strings descending (newest first).
 * Strips leading non-digit, splits on `.` and `-h`, compares
 * numerically. Examples sort: 12.1.4-h2 > 12.1.4 > 11.2.10 > 11.2.9.
 */
function compareVersionDesc(a: string, b: string): number {
  const parse = (v: string): number[] => {
    const m = v.match(/^(\d+)\.(\d+)\.(\d+)(?:-h(\d+))?/);
    if (!m) return [0, 0, 0, 0];
    return [Number(m[1]), Number(m[2]), Number(m[3]), Number(m[4] ?? 0)];
  };
  const pa = parse(a);
  const pb = parse(b);
  for (let i = 0; i < 4; i++) {
    if (pa[i] !== pb[i]) return pb[i] - pa[i]; // desc
  }
  return 0;
}

type PickerGroup =
  | { kind: "single"; device: Device }
  | { kind: "pair"; key: number; members: Device[] };

/**
 * Group the (already pair-key-sorted) visible devices so HA pairs render as
 * one bounded block instead of a row tint. A device with an `ha_peer_id`
 * joins the group keyed by min(id, ha_peer_id); everything else is its own
 * single-row group. If a filter hides one half of a pair, the visible half
 * still renders as a (one-member) HA group — the role badge keeps the
 * membership clear.
 */
function groupDevicesForPicker(devices: Device[]): PickerGroup[] {
  const groups: PickerGroup[] = [];
  const pairByKey = new Map<number, Device[]>();
  for (const d of devices) {
    if (d.ha_peer_id == null) {
      groups.push({ kind: "single", device: d });
      continue;
    }
    const key = Math.min(d.id, d.ha_peer_id);
    let members = pairByKey.get(key);
    if (!members) {
      members = [];
      pairByKey.set(key, members);
      groups.push({ kind: "pair", key, members });
    }
    members.push(d);
  }
  return groups;
}
