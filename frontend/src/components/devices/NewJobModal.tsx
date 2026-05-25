import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { Loader2, Rocket, X } from "lucide-react";
import { api } from "@/lib/api";
import type { AvailableSoftware, Device } from "@/lib/types";
import { compareVersions, trainOf } from "./VersionPickerModal";

/**
 * Create-upgrade-job modal. Asks for: job name, target version (from each
 * selected device's known version list — same source as the stage picker),
 * reboot opt-in, and (when any selected device is HA-paired) the two HA flags.
 *
 * Surfaces a warning if a selected device's HA peer isn't ALSO selected,
 * since orchestrator correctness requires upgrading both halves of a pair
 * together. We don't block submission — operator can choose to proceed.
 */
export function NewJobModal({
  deviceIds, devices, onClose,
}: {
  deviceIds: number[];
  devices: Device[];
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const selectedDevices = devices.filter((d) => deviceIds.includes(d.id));
  const defaultName = useMemo(() => {
    const stamp = new Date().toISOString().slice(0, 16).replace("T", " ");
    const n = selectedDevices.length;
    return `Upgrade ${n} device${n === 1 ? "" : "s"} — ${stamp}`;
  }, [selectedDevices.length]);

  const [name, setName] = useState(defaultName);
  const [version, setVersion] = useState<string | null>(null);
  const [pauseBetween, setPauseBetween] = useState(true);
  const [autoFailback, setAutoFailback] = useState(false);
  const [autoReboot, setAutoReboot] = useState(false);
  // When set, the orchestrator skips the AWAITING_*_OVERRIDE park step on a
  // FAIL-severity pre/post-check result and proceeds as if the operator had
  // clicked "Proceed anyway." Off by default — this is a real safety bypass.
  const [autoAckPrecheck, setAutoAckPrecheck] = useState(false);
  const [autoAckPostcheck, setAutoAckPostcheck] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Reuse the same software-list endpoint as the staging picker so the
  // version list is exactly what each device knows about + already-downloaded
  // markers stay accurate.
  const versions = useQuery({
    queryKey: ["software-available", deviceIds.join(",")],
    queryFn: () =>
      api<{ results: Record<number, AvailableSoftware> }>(
        "/api/devices/software/available/bulk",
        { method: "POST", body: { device_ids: deviceIds } },
      ),
    enabled: deviceIds.length > 0,
  });

  type Aggregate = {
    version: string;
    deviceIds: number[];
    downloadedOn: number[];
    latestOn: number[];
    released_on: string | null;
  };
  const groups = useMemo(() => {
    const results = versions.data?.results ?? {};
    const byVersion = new Map<string, Aggregate>();
    for (const [id, payload] of Object.entries(results)) {
      const did = Number(id);
      for (const e of payload.available) {
        const prev = byVersion.get(e.version) ?? {
          version: e.version,
          deviceIds: [],
          downloadedOn: [],
          latestOn: [],
          released_on: e.released_on,
        };
        prev.deviceIds.push(did);
        if (e.downloaded || e.current) prev.downloadedOn.push(did);
        if (e.latest) prev.latestOn.push(did);
        if (!prev.released_on && e.released_on) prev.released_on = e.released_on;
        byVersion.set(e.version, prev);
      }
    }
    const aggregates = Array.from(byVersion.values()).sort(
      (a, b) => compareVersions(b.version, a.version),
    );
    const currentTrains = new Set<string>();
    for (const id of deviceIds) {
      const v = devices.find((d) => d.id === id)?.current_version;
      const t = trainOf(v);
      if (t) currentTrains.add(t);
    }
    const m = new Map<string, Aggregate[]>();
    for (const a of aggregates) {
      const t = trainOf(a.version) ?? "(unknown)";
      (m.get(t) ?? m.set(t, []).get(t)!).push(a);
    }
    return Array.from(m.entries())
      .map(([train, items]) => ({ train, isCurrent: currentTrains.has(train), items }))
      .sort((a, b) => {
        if (a.isCurrent !== b.isCurrent) return a.isCurrent ? -1 : 1;
        return compareVersions(b.train + ".0", a.train + ".0");
      });
  }, [versions.data, deviceIds, devices]);

  // A selected device whose HA peer wasn't also selected is a footgun —
  // surface it up front so the user can re-pick. We don't outright block;
  // sometimes that's the intent (e.g. testing single-member upgrade).
  const missingPeers = useMemo(() => {
    const out: { name: string; peerId: number }[] = [];
    for (const d of selectedDevices) {
      if (d.ha_peer_id && !deviceIds.includes(d.ha_peer_id)) {
        out.push({ name: d.name, peerId: d.ha_peer_id });
      }
    }
    return out;
  }, [selectedDevices, deviceIds]);

  const hasAnyHA = selectedDevices.some((d) => d.ha_peer_id != null);

  async function submit() {
    if (!version) return setError("Pick a target version.");
    if (!name.trim()) return setError("Name is required.");
    setError(null);
    setBusy(true);
    try {
      const job = await api<{ id: number }>("/api/jobs", {
        method: "POST",
        body: {
          name: name.trim(),
          target_version: version,
          device_ids: deviceIds,
          workflow: "full",
          require_failover_confirmation: false,        // covered by pause-between flag
          require_primary_upgrade_confirmation: pauseBetween,
          auto_failback: autoFailback,
          auto_reboot_after_install: autoReboot,
          auto_ack_precheck_failures: autoAckPrecheck,
          auto_ack_postcheck_failures: autoAckPostcheck,
        },
      });
      onClose();
      navigate(`/jobs/${job.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create job");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-3xl overflow-hidden rounded-lg border border-slate-700 bg-slate-900 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-white">
              <Rocket className="h-4 w-4 text-emerald-400" /> Create upgrade job
            </div>
            <div className="text-xs text-slate-400">
              {selectedDevices.length} device{selectedDevices.length !== 1 ? "s" : ""} selected
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[70vh] space-y-4 overflow-y-auto px-4 py-4">
          {missingPeers.length > 0 && (
            <div className="rounded border border-amber-700 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
              <div className="font-medium">HA peer not in selection</div>
              <ul className="mt-1 list-disc pl-5">
                {missingPeers.map((p) => (
                  <li key={p.peerId}>
                    <span className="font-mono">{p.name}</span> is HA-paired with a device not selected (id #{p.peerId}).
                    You should usually upgrade both halves together — cancel and reselect.
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div>
            <label className="text-xs uppercase tracking-wide text-slate-500">Job name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
            />
          </div>

          <div>
            <div className="text-xs uppercase tracking-wide text-slate-500">Selected devices</div>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {selectedDevices.map((d) => (
                <span
                  key={d.id}
                  className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-950 px-2 py-0.5 text-xs"
                >
                  <span className="text-slate-200">{d.name}</span>
                  <span className="text-slate-500">{d.current_version ?? "—"}</span>
                  {d.ha_role && d.ha_role !== "standalone" && d.ha_role !== "unknown" && (
                    <span className="rounded bg-slate-800 px-1 text-[10px] uppercase text-slate-400">
                      {d.ha_role}
                    </span>
                  )}
                </span>
              ))}
            </div>
          </div>

          <div>
            <div className="text-xs uppercase tracking-wide text-slate-500">Target version</div>
            {versions.isLoading && (
              <div className="mt-2 flex items-center gap-2 text-sm text-slate-400">
                <Loader2 className="h-4 w-4 animate-spin" /> Querying devices…
              </div>
            )}
            {!versions.isLoading && groups.length === 0 && (
              <div className="mt-2 text-sm text-slate-500">No versions reported. Are the devices reachable?</div>
            )}
            {!versions.isLoading && groups.length > 0 && (
              <div className="mt-2 space-y-3">
                {groups.map((g) => (
                  <div key={g.train} className="space-y-1">
                    <div className="flex items-center gap-2 text-xs">
                      <span className={`font-mono uppercase tracking-wide ${
                        g.isCurrent ? "text-emerald-400" : "text-slate-400"
                      }`}>
                        {g.train} train
                      </span>
                      {g.isCurrent ? (
                        <span className="rounded bg-emerald-900/40 px-1.5 py-0.5 text-emerald-300">current</span>
                      ) : (
                        <span className="rounded bg-amber-900/30 px-1.5 py-0.5 text-amber-300">cross-train</span>
                      )}
                    </div>
                    {g.items.map((a) => {
                      const onAll = a.deviceIds.length === selectedDevices.length;
                      const isPicked = version === a.version;
                      return (
                        <button
                          key={a.version}
                          onClick={() => setVersion(a.version)}
                          className={`flex w-full items-center gap-3 rounded border px-3 py-1.5 text-left text-sm ${
                            isPicked
                              ? "border-emerald-500 bg-emerald-950/40"
                              : g.isCurrent
                                ? "border-slate-800 hover:border-slate-700 hover:bg-slate-900/60"
                                : "border-slate-900 bg-slate-950/40 hover:border-slate-800"
                          }`}
                        >
                          <span className={`flex h-3.5 w-3.5 items-center justify-center rounded-full border ${
                            isPicked ? "border-emerald-500 bg-emerald-600" : "border-slate-600"
                          }`}>
                            {isPicked && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                          </span>
                          <span className="font-mono text-slate-100">{a.version}</span>
                          {a.latestOn.length > 0 && (
                            <span className="rounded bg-emerald-900/50 px-1.5 py-0.5 text-xs text-emerald-300">latest</span>
                          )}
                          {a.downloadedOn.length > 0 && (
                            <span
                              className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300"
                              title={`Already downloaded on ${a.downloadedOn.length} device(s)`}
                            >
                              already on {a.downloadedOn.length}
                            </span>
                          )}
                          {!onAll && (
                            <span className="ml-auto text-xs text-amber-400">
                              won't install on {selectedDevices.length - a.deviceIds.length}
                            </span>
                          )}
                        </button>
                      );
                    })}
                  </div>
                ))}
              </div>
            )}
          </div>

          <div className="rounded border border-slate-800 p-3">
            <div className="text-xs uppercase tracking-wide text-slate-500">Reboot options</div>
            <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={autoReboot}
                onChange={(e) => setAutoReboot(e.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="font-medium">Auto-reboot after install.</span>
                <span className="block text-xs text-slate-400">
                  Off by default. With this off, after the new image is installed
                  the job parks at a gate and you click <span className="text-slate-200">Reboot now</span>{" "}
                  to initiate the actual reboot — gives you a chance to glance at
                  the device before mgmt plane drops. Turn this on only when
                  you're confident in unattended runs (e.g. overnight maintenance).
                </span>
              </span>
            </label>
          </div>

          {hasAnyHA && (
            <div className="rounded border border-slate-800 p-3">
              <div className="text-xs uppercase tracking-wide text-slate-500">HA options</div>
              <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={pauseBetween}
                  onChange={(e) => setPauseBetween(e.target.checked)}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium">Pause before upgrading the second member.</span>
                  <span className="block text-xs text-slate-400">
                    After we upgrade the secondary and trigger failover, the job parks
                    until you click Continue — gives you a chance to validate that the
                    upgraded member is healthy before touching the other one.
                  </span>
                </span>
              </label>
              <label className="mt-3 flex items-start gap-2 text-sm text-slate-300">
                <input
                  type="checkbox"
                  checked={autoFailback}
                  onChange={(e) => setAutoFailback(e.target.checked)}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium">Auto failback after both upgrades complete.</span>
                  <span className="block text-xs text-slate-400">
                    Restore the originally-active member to active. Off by default — most
                    operators prefer to validate the new active first and fail back by hand.
                  </span>
                </span>
              </label>
            </div>
          )}

          <div className="rounded border border-slate-800 p-3">
            <div className="text-xs uppercase tracking-wide text-slate-500">
              Unattended mode (pre-acknowledge gates)
            </div>
            <p className="mt-1 text-xs text-slate-500">
              Off by default. When a pre-check or post-check returns a
              FAIL-severity result, the orchestrator normally parks at an
              override gate so you can review and click "Proceed anyway."
              Enabling these pre-authorizes that override at job-creation —
              useful for overnight or fully-automated runs where you've
              already accepted the risk of known failures.
            </p>
            <label className="mt-2 flex items-start gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={autoAckPrecheck}
                onChange={(e) => setAutoAckPrecheck(e.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="font-medium">Auto-acknowledge pre-check failures.</span>
                <span className="block text-xs text-slate-400">
                  Proceed past any FAIL-severity pre-check without parking.
                  The failure is still recorded in the timeline and the
                  PrecheckRun row — this just skips the human gate.
                </span>
              </span>
            </label>
            <label className="mt-3 flex items-start gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={autoAckPostcheck}
                onChange={(e) => setAutoAckPostcheck(e.target.checked)}
                className="mt-1"
              />
              <span>
                <span className="font-medium">Auto-acknowledge post-check failures.</span>
                <span className="block text-xs text-slate-400">
                  Proceed past any FAIL-severity post-check after install
                  without parking. The job still moves to DONE rather than
                  blocking — useful when a benign content-version mismatch
                  is expected immediately post-upgrade.
                </span>
              </span>
            </label>
          </div>

          {error && <div className="text-sm text-red-400">{error}</div>}
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-slate-800 bg-slate-950 px-4 py-3">
          <button
            onClick={onClose}
            className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={busy || !version || !name.trim()}
            className="rounded bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
          >
            {busy ? "Creating…" : `Create job & start`}
          </button>
        </div>
      </div>
    </div>
  );
}
