import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, Device, MetricSpec } from "./api";
import { MetricChart } from "./components/MetricChart";

const HOURS_OPTIONS = [1, 6, 24, 24 * 7, 24 * 30];

export default function App() {
  const devicesQ = useQuery({ queryKey: ["devices"], queryFn: api.listDevices });
  const catalogQ = useQuery({ queryKey: ["catalog"], queryFn: api.listCatalog });

  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [hours, setHours] = useState(24);

  const devices: Device[] = devicesQ.data ?? [];
  const catalog: MetricSpec[] = catalogQ.data ?? [];

  const selected = useMemo(
    () => devices.find((d) => d.id === deviceId) ?? devices[0] ?? null,
    [devices, deviceId],
  );
  const activeDeviceId = selected?.id ?? null;

  const byCategory = useMemo(() => {
    const groups: Record<string, MetricSpec[]> = { config: [], system: [], traffic: [] };
    for (const m of catalog) (groups[m.category] ??= []).push(m);
    return groups;
  }, [catalog]);

  return (
    <div className="min-h-full">
      <header className="border-b border-zinc-800 bg-zinc-950/60 backdrop-blur">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center gap-4">
          <h1 className="text-base font-semibold text-zinc-100">
            PAN Capacity Analyzer
          </h1>
          <div className="text-xs text-zinc-500">
            {devices.length} device{devices.length === 1 ? "" : "s"} · {catalog.length} metrics
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              onClick={() => api.pollNow().then(() => devicesQ.refetch())}
              className="text-xs rounded border border-zinc-700 bg-zinc-800 hover:bg-zinc-700 px-2 py-1"
            >
              Poll now
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-6">
        {devices.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            <div className="flex items-center gap-3 mb-6">
              <select
                value={activeDeviceId ?? ""}
                onChange={(e) => setDeviceId(Number(e.target.value))}
                className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
              >
                {devices.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} {d.model ? `· ${d.model}` : ""}
                  </option>
                ))}
              </select>
              <select
                value={hours}
                onChange={(e) => setHours(Number(e.target.value))}
                className="bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm"
              >
                {HOURS_OPTIONS.map((h) => (
                  <option key={h} value={h}>
                    Last {h < 24 ? `${h}h` : `${h / 24}d`}
                  </option>
                ))}
              </select>
              {selected?.last_poll_error && (
                <span className="text-xs text-rose-400" title={selected.last_poll_error}>
                  poll error
                </span>
              )}
            </div>

            {activeDeviceId == null ? null : (
              <div className="space-y-8">
                {(["config", "system", "traffic"] as const).map((cat) =>
                  byCategory[cat]?.length ? (
                    <section key={cat}>
                      <h2 className="text-xs uppercase tracking-wider text-zinc-500 mb-2">
                        {cat}
                      </h2>
                      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                        {byCategory[cat].map((spec) => (
                          <MetricChart
                            key={spec.name}
                            deviceId={activeDeviceId}
                            spec={spec}
                            hours={hours}
                          />
                        ))}
                      </div>
                    </section>
                  ) : null,
                )}
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function EmptyState() {
  return (
    <div className="rounded-lg border border-dashed border-zinc-800 p-8 text-center text-sm text-zinc-400">
      <p>No devices yet.</p>
      <p className="mt-2">
        Add a credential, then a device via the API at{" "}
        <a href="/api/docs" className="text-blue-400 underline">
          /api/docs
        </a>
        .
      </p>
    </div>
  );
}
