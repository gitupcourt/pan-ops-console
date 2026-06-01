import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import { ApiError, PollingConfig, PollingConfigUpdate, api } from "../../api";
import { Button, Card, CardHeader, Empty, Field, Input } from "../ui/ui";

/**
 * Polling subtab (#89 PR-4). Editable dispatcher cadences + per-Panorama
 * concurrency (read by the dispatcher each tick — no redeploy), plus a live
 * per-Panorama pressure readout so an operator can see saturation and tune.
 */
export default function PollingSettings() {
  const qc = useQueryClient();
  const cfgQ = useQuery({ queryKey: ["polling-config"], queryFn: api.getPollingConfig });

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Polling"
          description="How the capacity analyzer samples your fleet. Changes apply within one dispatcher tick — no redeploy."
        />
        {cfgQ.isLoading ? (
          <div className="p-4 text-xs text-zinc-500">Loading…</div>
        ) : cfgQ.data ? (
          <PollingForm
            key={cfgQ.data.updated_at}
            cfg={cfgQ.data}
            onSaved={() => qc.invalidateQueries({ queryKey: ["polling-config"] })}
          />
        ) : (
          <Empty>Couldn't load polling config.</Empty>
        )}
      </Card>
      <PressurePanel />
    </div>
  );
}

function fmtDuration(s: number): string {
  if (s > 0 && s % 86400 === 0) return `${s / 86400} day${s / 86400 > 1 ? "s" : ""}`;
  if (s > 0 && s % 3600 === 0) return `${s / 3600} h`;
  if (s > 0 && s % 60 === 0) return `${s / 60} min`;
  return `${s} s`;
}

function PollingForm({ cfg, onSaved }: { cfg: PollingConfig; onSaved: () => void }) {
  const [system, setSystem] = useState(cfg.system_interval_seconds);
  const [config, setConfig] = useState(cfg.config_interval_seconds);
  const [cap, setCap] = useState(cfg.max_concurrency_per_panorama);
  const [backoff, setBackoff] = useState(cfg.device_retry_backoff_seconds);
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: (body: PollingConfigUpdate) => api.updatePollingConfig(body),
    onSuccess: () => {
      setErr(null);
      onSaved();
    },
    onError: (e) => setErr(e instanceof ApiError ? e.message : "Save failed"),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    save.mutate({
      system_interval_seconds: system,
      config_interval_seconds: config,
      max_concurrency_per_panorama: cap,
      device_retry_backoff_seconds: backoff,
    });
  }

  return (
    <form onSubmit={submit} className="p-4 grid gap-3">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Field
          label="System interval (seconds)"
          hint={`Live telemetry — CPU, sessions, throughput. ≈ ${fmtDuration(system)}.`}
        >
          <Input
            type="number"
            min={30}
            max={86400}
            value={system}
            onChange={(e) => setSystem(Number(e.target.value))}
          />
        </Field>
        <Field
          label="Config interval (seconds)"
          hint={`Object/policy counts — change only on commit. ≈ ${fmtDuration(config)}.`}
        >
          <Input
            type="number"
            min={300}
            max={604800}
            value={config}
            onChange={(e) => setConfig(Number(e.target.value))}
          />
        </Field>
        <Field
          label="Max concurrent polls / Panorama"
          hint="Caps in-flight proxied calls per Panorama. Aggregate throughput scales with the number of Panoramas."
        >
          <Input
            type="number"
            min={1}
            max={50}
            value={cap}
            onChange={(e) => setCap(Number(e.target.value))}
          />
        </Field>
        <Field
          label="Retry backoff (seconds)"
          hint="After a failed device poll, retry in about this long instead of waiting a full interval."
        >
          <Input
            type="number"
            min={0}
            max={3600}
            value={backoff}
            onChange={(e) => setBackoff(Number(e.target.value))}
          />
        </Field>
      </div>

      <p className="text-[11px] text-zinc-500">
        Dispatcher tick: every {cfg.dispatch_tick_seconds}s (set server-side; changing the
        cadence the dispatcher <span className="italic">runs</span> at needs a restart — the
        intervals above, which it <span className="italic">enforces</span>, are live).
      </p>

      {err && <div className="text-xs text-rose-400">{err}</div>}

      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
        <span className="text-[11px] text-zinc-600">
          Last updated {new Date(cfg.updated_at).toLocaleString()}
        </span>
      </div>
    </form>
  );
}

function PressurePanel() {
  const q = useQuery({
    queryKey: ["polling-pressure"],
    queryFn: api.getPollingPressure,
    refetchInterval: 10000,
  });
  const rows = q.data?.per_panorama ?? [];

  return (
    <Card>
      <CardHeader
        title="Polling pressure"
        description="Per-Panorama in-flight polls (refreshes every 10s). If in-flight sits at the cap, that Panorama is the bottleneck — raise its interval or add capacity."
      />
      {rows.length === 0 ? (
        <Empty>No enabled devices to poll yet.</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Panorama</th>
              <th className="text-left px-4 py-2 font-medium">In-flight</th>
              <th className="text-left px-4 py-2 font-medium">Devices</th>
              <th className="text-left px-4 py-2 font-medium">In error</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.pano_key} className="border-b border-zinc-800/50">
                <td className="px-4 py-2 text-zinc-200">{r.label}</td>
                <td className="px-4 py-2 tabular-nums text-zinc-300">
                  {r.in_flight === null ? "—" : `${r.in_flight} / ${r.cap}`}
                </td>
                <td className="px-4 py-2 tabular-nums text-zinc-400">{r.devices}</td>
                <td className="px-4 py-2 tabular-nums">
                  {r.devices_in_error > 0 ? (
                    <span className="text-rose-400">{r.devices_in_error}</span>
                  ) : (
                    <span className="text-zinc-600">0</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}
