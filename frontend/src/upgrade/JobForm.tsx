import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, Device, UpgradeJobCreate } from "../api";
import { Button, Field, Input, Select } from "../core/ui/ui";

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
  // Seed the device selection from the inventory handoff (if any).
  // We intentionally init from the prop ONCE; subsequent prop changes
  // don't reset the selection (rare in practice — the form mounts
  // fresh each time the operator opens it).
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(
    () => new Set(initialDeviceIds),
  );
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

  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () => {
      const body: UpgradeJobCreate = {
        name,
        target_version: targetVersion,
        device_ids: Array.from(selectedDeviceIds),
        image_id: imageMode === "select" ? selectedImageId : null,
        device_pull_image: imageMode === "pull",
        require_failover_confirmation: requireFailover,
        require_primary_upgrade_confirmation: requirePrimaryUpgrade,
        auto_failback: autoFailback,
        auto_reboot_after_install: autoReboot,
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
      devices.filter((d) => {
        if (filterModel && d.model !== filterModel) return false;
        if (filterDeviceGroup && d.device_group !== filterDeviceGroup) return false;
        if (filterTemplateStack && d.template_stack !== filterTemplateStack)
          return false;
        return true;
      }),
    [devices, filterModel, filterDeviceGroup, filterTemplateStack],
  );

  const anyFilterActive =
    !!filterModel || !!filterDeviceGroup || !!filterTemplateStack;

  // Note: device.ha_peer_id is not exposed on DeviceRead yet, so we
  // can't visually group HA pairs in this picker. The orchestrator
  // derives pair grouping from ha_peer_id at job-create time anyway
  // (see `_ha_pair_key_for` in routes/jobs.py), so paired devices
  // upgrade together regardless of how the operator picks them here.
  // Future polish: expose ha_peer_id on DeviceRead, render pairs as
  // collapsible groups.

  const canSubmit =
    name.trim().length > 0 &&
    targetVersion.trim().length > 0 &&
    selectedDeviceIds.size > 0 &&
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
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Job name">
          <Input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Q1 fleet refresh"
            required
          />
        </Field>
        <Field label="Target PAN-OS version">
          <Input
            value={targetVersion}
            onChange={(e) => setTargetVersion(e.target.value)}
            placeholder="11.1.4-h7"
            required
          />
        </Field>
      </div>

      {/* Device picker */}
      <div className="rounded border border-zinc-800 p-3 grid gap-2">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Devices ({selectedDeviceIds.size} selected
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
                for (const d of visibleDevices) next.add(d.id);
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
            <tbody>
              {visibleDevices.map((d) => (
                <tr
                  key={d.id}
                  className="border-b border-zinc-800/40 hover:bg-zinc-900/30"
                >
                  <td className="px-3 py-1.5 w-8">
                    <input
                      type="checkbox"
                      checked={selectedDeviceIds.has(d.id)}
                      onChange={(e) => {
                        const next = new Set(selectedDeviceIds);
                        if (e.target.checked) next.add(d.id);
                        else next.delete(d.id);
                        setSelectedDeviceIds(next);
                      }}
                    />
                  </td>
                  <td className="px-3 py-1.5 text-zinc-100">{d.name}</td>
                  <td className="px-3 py-1.5 text-zinc-500 text-xs">
                    {d.model ?? "—"}
                  </td>
                  <td className="px-3 py-1.5 text-zinc-500 text-xs tabular-nums">
                    {d.sw_version ?? "—"}
                  </td>
                  <td className="px-3 py-1.5 text-zinc-500 text-xs">
                    {d.device_group ?? (
                      <span className="text-zinc-700 italic">standalone</span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-zinc-500 text-xs">
                    {d.source}
                  </td>
                </tr>
              ))}
              {visibleDevices.length === 0 && (
                <tr>
                  <td colSpan={6} className="p-4 text-xs text-zinc-500 text-center">
                    {devices.length === 0
                      ? "No devices in inventory. Add some first."
                      : "No devices match the current filters."}
                  </td>
                </tr>
              )}
            </tbody>
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
            href="/upgrade/precheck-sets"
            className="text-blue-400 hover:text-blue-300"
          >
            /upgrade/precheck-sets
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
          {create.isPending ? "Creating…" : "Create job"}
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
