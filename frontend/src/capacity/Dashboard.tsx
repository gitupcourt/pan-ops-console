import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { api, Device, MetricSpec } from "../api";
import { DeviceConnectionStatus } from "../core/devices/DeviceConnectionStatus";
import { Button, Select } from "../core/ui/ui";
import { MetricChart } from "./MetricChart";

const HOURS_OPTIONS = [1, 6, 24, 24 * 7, 24 * 30];

export default function Dashboard() {
  const devicesQ = useQuery({ queryKey: ["devices"], queryFn: api.listDevices });
  const catalogQ = useQuery({ queryKey: ["catalog"], queryFn: api.listCatalog });

  const [deviceId, setDeviceId] = useState<number | null>(null);
  const [hours, setHours] = useState(1);

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

  if (devices.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-zinc-800 p-8 text-center text-sm text-zinc-400">
        <p>No devices yet.</p>
        <p className="mt-2">
          Head to{" "}
          <a href="/inventory" className="text-blue-400 underline">
            Inventory
          </a>{" "}
          to add a credential and a device.
        </p>
      </div>
    );
  }

  return (
    <>
      <div className="flex items-center gap-3 mb-3">
        <Select
          value={activeDeviceId ?? ""}
          onChange={(e) => setDeviceId(Number(e.target.value))}
        >
          {devices.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name} {d.model ? `· ${d.model}` : ""}
            </option>
          ))}
        </Select>
        {selected && <DeviceConnectionStatus device={selected} />}
        <Select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
          {HOURS_OPTIONS.map((h) => (
            <option key={h} value={h}>
              Last {h < 24 ? `${h}h` : `${h / 24}d`}
            </option>
          ))}
        </Select>
        <Button onClick={() => api.pollNow().then(() => devicesQ.refetch())}>
          Poll now
        </Button>
        {selected?.last_poll_error && (
          <span className="text-xs text-rose-400" title={selected.last_poll_error}>
            poll error
          </span>
        )}
      </div>

      {selected && (
        <div className="mb-6">
          <DeviceConnectionStatus device={selected} variant="banner" />
        </div>
      )}

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
  );
}
