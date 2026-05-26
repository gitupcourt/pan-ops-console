import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, UpgradeJobCreate } from "../api";
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
export function JobForm({ onDone }: { onDone: () => void }) {
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

  // Form state.
  const [name, setName] = useState("");
  const [targetVersion, setTargetVersion] = useState("");
  const [selectedDeviceIds, setSelectedDeviceIds] = useState<Set<number>>(
    new Set(),
  );
  // Image source: either an existing PanosImage id (when imageMode = "select")
  // or device_pull_image=true (when imageMode = "pull"). Mutually exclusive.
  type ImageMode = "select" | "pull";
  const [imageMode, setImageMode] = useState<ImageMode>("pull");
  const [selectedImageId, setSelectedImageId] = useState<number | null>(null);

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
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-wider text-zinc-500">
            Devices ({selectedDeviceIds.size} selected)
          </div>
          <div className="flex gap-3 text-xs">
            <button
              type="button"
              className="text-zinc-400 hover:text-zinc-100"
              onClick={() =>
                setSelectedDeviceIds(new Set(devices.map((d) => d.id)))
              }
            >
              Select all
            </button>
            <button
              type="button"
              className="text-zinc-400 hover:text-zinc-100"
              onClick={() => setSelectedDeviceIds(new Set())}
            >
              Clear
            </button>
          </div>
        </div>
        <div className="max-h-64 overflow-auto border border-zinc-800 rounded">
          <table className="w-full text-sm">
            <tbody>
              {devices.map((d) => (
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
                    {d.source}
                  </td>
                </tr>
              ))}
              {devices.length === 0 && (
                <tr>
                  <td colSpan={5} className="p-4 text-xs text-zinc-500 text-center">
                    No devices in inventory. Add some first.
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
