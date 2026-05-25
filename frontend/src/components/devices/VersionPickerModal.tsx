import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, X } from "lucide-react";
import { api } from "@/lib/api";
import type { AvailableSoftware, Device } from "@/lib/types";

/**
 * Major.minor train of a PAN-OS version string. "11.2.5-h1" → "11.2".
 * Returns null if the string doesn't start with two dotted integers.
 */
export function trainOf(v: string | null | undefined): string | null {
  if (!v) return null;
  const m = v.match(/^(\d+)\.(\d+)/);
  return m ? `${m[1]}.${m[2]}` : null;
}

/**
 * Simple PAN-OS version comparator. Returns <0, 0, or >0 the way Array.sort
 * wants. Splits on dots and on `-h<n>`.
 */
export function compareVersions(a: string, b: string): number {
  const parse = (v: string) => {
    const [main, hot] = v.split("-h");
    const parts = main.split(".").map((x) => parseInt(x, 10) || 0);
    const hotN = hot ? parseInt(hot, 10) || 0 : 0;
    return [...parts, hotN];
  };
  const av = parse(a);
  const bv = parse(b);
  for (let i = 0; i < Math.max(av.length, bv.length); i++) {
    const x = av[i] ?? 0;
    const y = bv[i] ?? 0;
    if (x !== y) return x - y;
  }
  return 0;
}

type Aggregate = {
  version: string;
  deviceIds: number[];
  downloadedOn: number[];
  latestOn: number[];
  released_on: string | null;
};

type Group = { train: string; isCurrent: boolean; items: Aggregate[] };

/**
 * Modal that asks each selected device what PAN-OS versions it knows about,
 * then aggregates and groups by train. The user picks one and we kick off
 * the staging operation.
 *
 * Train grouping with the current train surfaced first is a real safety
 * feature — picking a cross-train version is a different operation than
 * picking within-train, and the visual separation makes that obvious.
 */
export function VersionPickerModal({
  deviceIds, devices, onClose, onPick,
}: {
  deviceIds: number[];
  devices: Device[];
  onClose: () => void;
  onPick: (version: string) => void;
}) {
  const byId = new Map(devices.map((d) => [d.id, d]));

  const versions = useQuery({
    queryKey: ["software-available", deviceIds.join(",")],
    queryFn: () =>
      api<{ results: Record<number, AvailableSoftware> }>(
        "/api/devices/software/available/bulk",
        { method: "POST", body: { device_ids: deviceIds } },
      ),
    enabled: deviceIds.length > 0,
  });

  // Build a unified version list across all selected devices.
  // Per version: which devices report it, "latest" flag if any, release date.
  const aggregates = useMemo<Aggregate[]>(() => {
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
    return Array.from(byVersion.values()).sort((a, b) => compareVersions(b.version, a.version));
  }, [versions.data]);

  // Trains the SELECTED devices are currently on. Used to (a) put the
  // device's own train at the top and (b) flag cross-train selections so the
  // user goes in eyes-open. Whether PAN-OS lets a given cross-train upgrade
  // proceed directly is the device's call — we surface its error if rejected.
  const currentTrains = useMemo<Set<string>>(() => {
    const out = new Set<string>();
    for (const id of deviceIds) {
      const v = byId.get(id)?.current_version;
      const t = trainOf(v);
      if (t) out.add(t);
    }
    return out;
  }, [deviceIds, byId]);

  const groups = useMemo<Group[]>(() => {
    const m = new Map<string, Aggregate[]>();
    for (const a of aggregates) {
      const t = trainOf(a.version) ?? "(unknown)";
      const arr = m.get(t) ?? [];
      arr.push(a);
      m.set(t, arr);
    }
    const groupArr: Group[] = Array.from(m.entries()).map(([train, items]) => ({
      train,
      isCurrent: currentTrains.has(train),
      items,
    }));
    groupArr.sort((a, b) => {
      if (a.isCurrent !== b.isCurrent) return a.isCurrent ? -1 : 1;
      return compareVersions(b.train + ".0", a.train + ".0");
    });
    return groupArr;
  }, [aggregates, currentTrains]);

  const [picked, setPicked] = useState<string | null>(null);

  const errors = useMemo(() => {
    const results = versions.data?.results ?? {};
    return Object.entries(results)
      .filter(([, v]) => v.error)
      .map(([id, v]) => ({
        id: Number(id),
        name: byId.get(Number(id))?.name ?? `#${id}`,
        error: v.error!,
      }));
  }, [versions.data, byId]);

  const totalDevices = deviceIds.length;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-2xl overflow-hidden rounded-lg border border-slate-700 bg-slate-900 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-white">Pre-stage PAN-OS image</div>
            <div className="text-xs text-slate-400">
              {totalDevices} device{totalDevices !== 1 ? "s" : ""} selected
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[60vh] overflow-y-auto px-4 py-3">
          {versions.isLoading && (
            <div className="flex items-center gap-2 py-6 text-sm text-slate-400">
              <Loader2 className="h-4 w-4 animate-spin" />
              Querying devices for available versions…
            </div>
          )}

          {!versions.isLoading && errors.length > 0 && (
            <div className="mb-3 rounded border border-amber-800 bg-amber-950/30 px-3 py-2 text-xs text-amber-200">
              <div className="font-medium">Couldn't query {errors.length} of {totalDevices} devices:</div>
              <ul className="mt-1 list-disc pl-5">
                {errors.map((e) => (
                  <li key={e.id}><span className="font-mono">{e.name}</span>: {e.error}</li>
                ))}
              </ul>
            </div>
          )}

          {!versions.isLoading && groups.length > 0 && (
            <div className="space-y-4">
              {groups.map((g) => (
                <div key={g.train} className="space-y-1.5">
                  <div className="flex items-center gap-2 text-xs">
                    <span className={`font-mono uppercase tracking-wide ${
                      g.isCurrent ? "text-emerald-400" : "text-slate-400"
                    }`}>
                      {g.train} train
                    </span>
                    {g.isCurrent ? (
                      <span className="rounded bg-emerald-900/40 px-1.5 py-0.5 text-emerald-300">
                        current
                      </span>
                    ) : (
                      <span
                        className="rounded bg-amber-900/30 px-1.5 py-0.5 text-amber-300"
                        title="Different major.minor train than what these devices are on. Cross-train upgrades may have additional PAN-OS requirements; if the device rejects the request it'll show in the run's error."
                      >
                        cross-train
                      </span>
                    )}
                    <span className="text-slate-600">· {g.items.length} version{g.items.length !== 1 ? "s" : ""}</span>
                  </div>
                  {g.items.map((a) => {
                    const onAll = a.deviceIds.length === totalDevices;
                    const isPicked = picked === a.version;
                    return (
                      <button
                        key={a.version}
                        onClick={() => setPicked(a.version)}
                        className={`flex w-full items-center gap-3 rounded border px-3 py-2 text-left text-sm ${
                          isPicked
                            ? "border-sky-500 bg-sky-950/40"
                            : g.isCurrent
                              ? "border-slate-800 hover:border-slate-700 hover:bg-slate-900/60"
                              : "border-slate-900 bg-slate-950/40 hover:border-slate-800"
                        }`}
                      >
                        <span className={`flex h-4 w-4 items-center justify-center rounded-full border ${
                          isPicked ? "border-sky-500 bg-sky-600" : "border-slate-600"
                        }`}>
                          {isPicked && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                        </span>
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
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
                          </div>
                          <div className="text-xs text-slate-500">
                            {a.released_on && <>Released {a.released_on} · </>}
                            Available on {a.deviceIds.length} of {totalDevices} selected
                            {!onAll && (
                              <span className="ml-1 text-amber-400">
                                (won't install on {totalDevices - a.deviceIds.length})
                              </span>
                            )}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </div>
              ))}
            </div>
          )}

          {!versions.isLoading && aggregates.length === 0 && errors.length === 0 && (
            <div className="py-8 text-center text-sm text-slate-500">
              No versions reported by any device. Are they reachable?
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 border-t border-slate-800 bg-slate-950 px-4 py-3">
          <div className="text-xs text-slate-500">
            {picked ? (() => {
              const pickedTrain = trainOf(picked);
              const crossTrain = pickedTrain && !currentTrains.has(pickedTrain);
              return crossTrain ? (
                <span className="text-amber-300">
                  Will stage <span className="font-mono">{picked}</span> — cross-train upgrade.
                  If the device rejects the request, it'll show in the run's error.
                </span>
              ) : (
                <>Will stage <span className="font-mono text-slate-300">{picked}</span>.</>
              );
            })() : (
              "Pick a version above to stage."
            )}
          </div>
          <div className="flex gap-2">
            <button
              onClick={onClose}
              className="rounded border border-slate-700 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
            >
              Cancel
            </button>
            <button
              disabled={!picked}
              onClick={() => picked && onPick(picked)}
              className="rounded bg-sky-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-sky-500 disabled:opacity-50"
            >
              Stage on {totalDevices}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
