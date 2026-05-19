import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";

import {
  api,
  AuthPayload,
  Device,
  DeviceInput,
  Panorama,
  PanoramaDevicePreview,
  PanoramaInput,
} from "../api";
import { Button, Card, CardHeader, Empty, Field, Input, Select } from "../components/ui";

export default function Inventory() {
  return (
    <div className="space-y-6">
      <PanoramasSection />
      <DevicesSection />
    </div>
  );
}

// =====================================================================
// Auth fragment — reusable inside Device + Panorama forms.
//
// Two modes:
//   - "userpass": run keygen() against the host, store ONLY the resulting key.
//   - "api_key":  paste a known key.
//
// On EDIT, a third option "leave unchanged" keeps the existing stored key
// untouched. Selecting "Replace authentication" reveals the actual mode picker.
// =====================================================================

type AuthMode = "unchanged" | "userpass" | "api_key";

function AuthSection({
  has_existing,
  value,
  onChange,
  hostHint,
}: {
  has_existing: boolean;
  value: { mode: AuthMode; username: string; password: string; api_key: string };
  onChange: (next: typeof value) => void;
  hostHint?: string;
}) {
  // For creates, "unchanged" doesn't exist — the parent passes mode=userpass
  // by default. We surface "unchanged" only when has_existing is true.
  const options: { v: AuthMode; label: string }[] = [
    ...(has_existing ? [{ v: "unchanged" as AuthMode, label: "Keep existing API key" }] : []),
    { v: "userpass" as AuthMode, label: "Mint API key from username + password (recommended)" },
    { v: "api_key" as AuthMode, label: "Paste API key directly" },
  ];

  return (
    <div className="rounded border border-zinc-800 p-3 grid gap-3">
      <div className="text-xs uppercase tracking-wider text-zinc-500">Authentication</div>
      <Select
        value={value.mode}
        onChange={(e) => onChange({ ...value, mode: e.target.value as AuthMode })}
      >
        {options.map((o) => (
          <option key={o.v} value={o.v}>
            {o.label}
          </option>
        ))}
      </Select>

      {value.mode === "userpass" && (
        <>
          <div className="text-[11px] text-zinc-500">
            We'll call <code>keygen</code> against {hostHint ?? "this host"} once and store only
            the resulting API key. Username and password are never persisted.
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <Field label="Username">
              <Input
                value={value.username}
                onChange={(e) => onChange({ ...value, username: e.target.value })}
                required
                autoComplete="off"
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                value={value.password}
                onChange={(e) => onChange({ ...value, password: e.target.value })}
                required
                autoComplete="new-password"
              />
            </Field>
          </div>
        </>
      )}

      {value.mode === "api_key" && (
        <Field label="API key">
          <Input
            type="password"
            value={value.api_key}
            onChange={(e) => onChange({ ...value, api_key: e.target.value })}
            required
            autoComplete="new-password"
          />
        </Field>
      )}
    </div>
  );
}

function authToPayload(v: {
  mode: AuthMode;
  username: string;
  password: string;
  api_key: string;
}): AuthPayload | null {
  if (v.mode === "unchanged") return null;
  if (v.mode === "userpass") return { mode: "userpass", username: v.username, password: v.password };
  return { mode: "api_key", api_key: v.api_key };
}

// =====================================================================
// Panoramas
// =====================================================================

function PanoramasSection() {
  const qc = useQueryClient();
  const panosQ = useQuery({ queryKey: ["panoramas"], queryFn: api.listPanoramas });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ id: number; ok: boolean; text: string } | null>(null);

  const test = useMutation({
    mutationFn: async (id: number) => ({ id, result: await api.testPanorama(id) }),
    onSuccess: ({ id, result }) => {
      setTestResult({
        id,
        ok: true,
        text: Object.entries(result.info).map(([k, v]) => `${k}: ${v ?? "—"}`).join("\n"),
      });
      qc.invalidateQueries({ queryKey: ["panoramas"] });
    },
    onError: (e: Error, id) => setTestResult({ id, ok: false, text: e.message }),
  });
  const del = useMutation({
    mutationFn: api.deletePanorama,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["panoramas"] }),
  });

  // Inline import picker — opening it on a Panorama fetches its device list.
  const [importingFor, setImportingFor] = useState<number | null>(null);

  const panos: Panorama[] = panosQ.data ?? [];

  return (
    <Card>
      <CardHeader
        title="Panoramas"
        description="Used both as a device source (import managed devices) and as a proxy for API calls to devices we can't reach directly."
        action={
          <Button variant="primary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "Add Panorama"}
          </Button>
        }
      />
      {adding && <PanoramaForm onDone={() => setAdding(false)} />}
      {panos.length === 0 ? (
        <Empty>No Panoramas yet.</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Name</th>
              <th className="text-left px-4 py-2 font-medium">Hostname</th>
              <th className="text-left px-4 py-2 font-medium">Reachable</th>
              <th className="text-left px-4 py-2 font-medium">Last sync</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {panos.map((p) => (
              <Fragment key={p.id}>
                <tr className="border-b border-zinc-800/50 align-top">
                  <td className="px-4 py-2 text-zinc-100">{p.name}</td>
                  <td className="px-4 py-2 text-zinc-400">{p.hostname}</td>
                  <td className="px-4 py-2 text-xs">
                    {p.reachable ? (
                      <span className="text-emerald-400">yes</span>
                    ) : (
                      <span className="text-rose-400" title={p.last_reachability_error ?? ""}>
                        no
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-zinc-500">
                    {p.last_sync_at ? new Date(p.last_sync_at).toLocaleString() : "never"}
                  </td>
                  <td className="px-4 py-2 text-right space-x-2 whitespace-nowrap">
                    <Button onClick={() => test.mutate(p.id)} disabled={test.isPending}>
                      Test
                    </Button>
                    <Button onClick={() => setImportingFor(importingFor === p.id ? null : p.id)}>
                      {importingFor === p.id ? "Close" : "Import devices"}
                    </Button>
                    <Button onClick={() => setEditing(editing === p.id ? null : p.id)}>
                      {editing === p.id ? "Close" : "Edit"}
                    </Button>
                    <Button variant="danger" onClick={() => del.mutate(p.id)}>
                      Delete
                    </Button>
                  </td>
                </tr>
                {testResult?.id === p.id && (
                  <tr className="border-b border-zinc-800/50 bg-zinc-950/60">
                    <td colSpan={5} className="px-4 py-2">
                      <div className="flex items-start justify-between gap-3">
                        <pre className={`text-xs whitespace-pre-wrap ${testResult.ok ? "text-emerald-300" : "text-rose-300"}`}>
                          {testResult.text}
                        </pre>
                        <button className="text-xs text-zinc-500 hover:text-zinc-200" onClick={() => setTestResult(null)}>
                          dismiss
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
                {editing === p.id && (
                  <tr className="bg-zinc-950/60">
                    <td colSpan={5} className="p-0">
                      <PanoramaForm initial={p} onDone={() => setEditing(null)} />
                    </td>
                  </tr>
                )}
                {importingFor === p.id && (
                  <tr className="bg-zinc-950/60">
                    <td colSpan={5} className="p-0">
                      <ImportPicker panoramaId={p.id} onDone={() => setImportingFor(null)} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function PanoramaForm({ initial, onDone }: { initial?: Panorama; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(initial?.name ?? "");
  const [hostname, setHostname] = useState(initial?.hostname ?? "");
  const [verifyTls, setVerifyTls] = useState(initial?.verify_tls ?? true);
  const [auth, setAuth] = useState({
    mode: (initial ? "unchanged" : "userpass") as AuthMode,
    username: "",
    password: "",
    api_key: "",
  });
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const body: PanoramaInput = {
        name,
        hostname,
        verify_tls: verifyTls,
        auth: authToPayload(auth),
      };
      return initial ? api.updatePanorama(initial.id, body) : api.createPanorama(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["panoramas"] });
      onDone();
    },
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setErr(null);
        save.mutate();
      }}
      className="border-b border-zinc-800 bg-zinc-950/40 p-4 grid gap-3"
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} required placeholder="prod-panorama" />
        </Field>
        <Field label="Hostname or IP">
          <Input value={hostname} onChange={(e) => setHostname(e.target.value)} required />
        </Field>
      </div>

      <AuthSection
        has_existing={!!initial?.has_api_key}
        value={auth}
        onChange={setAuth}
        hostHint={hostname || "the Panorama"}
      />

      <label className="flex items-center gap-2 text-xs text-zinc-400">
        <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
        Verify TLS certificate
      </label>

      {err && <div className="text-xs text-rose-400">{err}</div>}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : initial ? "Save changes" : "Save"}
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// =====================================================================
// Devices
// =====================================================================

function DevicesSection() {
  const qc = useQueryClient();
  const devsQ = useQuery({ queryKey: ["devices"], queryFn: api.listDevices });
  const panosQ = useQuery({ queryKey: ["panoramas"], queryFn: api.listPanoramas });
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [testResult, setTestResult] = useState<{ id: number; ok: boolean; text: string } | null>(null);

  const test = useMutation({
    mutationFn: async (id: number) => ({ id, result: await api.testDevice(id) }),
    onSuccess: ({ id, result }) => {
      setTestResult({
        id,
        ok: true,
        text: Object.entries(result.info).map(([k, v]) => `${k}: ${v ?? "—"}`).join("\n"),
      });
      qc.invalidateQueries({ queryKey: ["devices"] });
    },
    onError: (e: Error, id) => setTestResult({ id, ok: false, text: e.message }),
  });
  const togglePolling = useMutation({
    mutationFn: (d: Device) =>
      api.updateDevice(d.id, {
        name: d.name,
        hostname: d.hostname,
        ip_address: d.ip_address,
        panorama_id: d.panorama_id,
        verify_tls: d.verify_tls,
        proxy_via_panorama: d.proxy_via_panorama,
        polling_enabled: !d.polling_enabled,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["devices"] }),
  });
  const del = useMutation({
    mutationFn: api.deleteDevice,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["devices"] }),
  });

  const devs: Device[] = devsQ.data ?? [];

  return (
    <Card>
      <CardHeader
        title="Devices"
        description="Firewalls polled by the capacity analyzer. Switch between direct API access and Panorama-proxied at any time."
        action={
          <Button variant="primary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "Add device"}
          </Button>
        }
      />
      {adding && <DeviceForm panos={panosQ.data ?? []} onDone={() => setAdding(false)} />}
      {devs.length === 0 ? (
        <Empty>No devices yet. Add one directly, or sync a Panorama to import its managed devices.</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Name</th>
              <th className="text-left px-4 py-2 font-medium">Host</th>
              <th className="text-left px-4 py-2 font-medium">Model</th>
              <th className="text-left px-4 py-2 font-medium">Access</th>
              <th className="text-left px-4 py-2 font-medium">Polling</th>
              <th className="text-left px-4 py-2 font-medium">Last poll</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {devs.map((d) => (
              <Fragment key={d.id}>
                <tr className="border-b border-zinc-800/50 align-top">
                  <td className="px-4 py-2 text-zinc-100">{d.name}</td>
                  <td className="px-4 py-2 text-zinc-400">{d.ip_address ?? d.hostname}</td>
                  <td className="px-4 py-2 text-zinc-400">{d.model ?? "—"}</td>
                  <td className="px-4 py-2 text-zinc-500 text-xs">
                    {d.proxy_via_panorama ? "via Panorama" : d.has_api_key ? "direct" : "no key"}
                    <div className="text-[10px] text-zinc-600">{d.source}</div>
                  </td>
                  <td className="px-4 py-2 text-xs">
                    <button
                      onClick={() => togglePolling.mutate(d)}
                      className={`px-1.5 py-0.5 rounded border ${
                        d.polling_enabled
                          ? "border-emerald-700 bg-emerald-900/30 text-emerald-300"
                          : "border-zinc-700 bg-zinc-800 text-zinc-400"
                      }`}
                      title="Toggle polling"
                    >
                      {d.polling_enabled ? "on" : "off"}
                    </button>
                  </td>
                  <td className="px-4 py-2 text-xs text-zinc-500">
                    {d.last_poll_at ? new Date(d.last_poll_at).toLocaleString() : "never"}
                    {d.last_poll_error && (
                      <div className="text-rose-400 mt-0.5" title={d.last_poll_error}>
                        error
                      </div>
                    )}
                  </td>
                  <td className="px-4 py-2 text-right space-x-2 whitespace-nowrap">
                    <Button onClick={() => test.mutate(d.id)} disabled={test.isPending}>
                      Test
                    </Button>
                    <Button onClick={() => setEditing(editing === d.id ? null : d.id)}>
                      {editing === d.id ? "Close" : "Edit"}
                    </Button>
                    <Button variant="danger" onClick={() => del.mutate(d.id)}>
                      Delete
                    </Button>
                  </td>
                </tr>
                {testResult?.id === d.id && (
                  <tr className="border-b border-zinc-800/50 bg-zinc-950/60">
                    <td colSpan={7} className="px-4 py-2">
                      <div className="flex items-start justify-between gap-3">
                        <pre className={`text-xs whitespace-pre-wrap ${testResult.ok ? "text-emerald-300" : "text-rose-300"}`}>
                          {testResult.text}
                        </pre>
                        <button className="text-xs text-zinc-500 hover:text-zinc-200" onClick={() => setTestResult(null)}>
                          dismiss
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
                {editing === d.id && (
                  <tr className="bg-zinc-950/60">
                    <td colSpan={7} className="p-0">
                      <DeviceForm panos={panosQ.data ?? []} initial={d} onDone={() => setEditing(null)} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function ImportPicker({ panoramaId, onDone }: { panoramaId: number; onDone: () => void }) {
  const qc = useQueryClient();
  const previewQ = useQuery({
    queryKey: ["panorama-preview", panoramaId],
    queryFn: () => api.previewPanoramaDevices(panoramaId),
  });

  // Selection map keyed by serial. Initialized once on load: imported devices
  // checked by default (keeps refresh-on-sync working), new devices unchecked.
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [initialized, setInitialized] = useState(false);
  if (previewQ.data && !initialized) {
    const init: Record<string, boolean> = {};
    for (const d of previewQ.data) init[d.serial] = d.already_imported;
    setSelected(init);
    setInitialized(true);
  }

  const sync = useMutation({
    mutationFn: (serials: string[]) => api.syncPanorama(panoramaId, serials),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["panoramas"] });
      qc.invalidateQueries({ queryKey: ["devices"] });
      onDone();
    },
  });

  if (previewQ.isLoading) {
    return <div className="p-4 text-xs text-zinc-500">Fetching devices from Panorama…</div>;
  }
  if (previewQ.error) {
    return (
      <div className="p-4 text-xs text-rose-300">
        Failed to fetch: {(previewQ.error as Error).message}
      </div>
    );
  }

  const rows = previewQ.data ?? [];
  const selectedSerials = Object.entries(selected).filter(([, v]) => v).map(([s]) => s);

  function setAll(value: boolean) {
    const next: Record<string, boolean> = {};
    for (const d of rows) next[d.serial] = value;
    setSelected(next);
  }

  return (
    <div className="border-b border-zinc-800 bg-zinc-950/40 p-4 grid gap-3">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-zinc-500">
          {rows.length} device{rows.length === 1 ? "" : "s"} known to Panorama
        </div>
        <div className="flex gap-2 text-xs">
          <button type="button" className="text-zinc-400 hover:text-zinc-100" onClick={() => setAll(true)}>
            Select all
          </button>
          <button type="button" className="text-zinc-400 hover:text-zinc-100" onClick={() => setAll(false)}>
            Clear
          </button>
        </div>
      </div>

      <div className="rounded border border-zinc-800 max-h-80 overflow-auto">
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase text-zinc-500 sticky top-0 bg-zinc-950 border-b border-zinc-800">
            <tr>
              <th className="px-3 py-2 w-8"></th>
              <th className="text-left px-3 py-2 font-medium">Hostname</th>
              <th className="text-left px-3 py-2 font-medium">Serial</th>
              <th className="text-left px-3 py-2 font-medium">Model</th>
              <th className="text-left px-3 py-2 font-medium">IP</th>
              <th className="text-left px-3 py-2 font-medium">Connected</th>
              <th className="text-left px-3 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((d) => (
              <tr key={d.serial} className="border-b border-zinc-800/40">
                <td className="px-3 py-1.5 text-center">
                  <input
                    type="checkbox"
                    checked={selected[d.serial] ?? false}
                    onChange={(e) =>
                      setSelected((prev) => ({ ...prev, [d.serial]: e.target.checked }))
                    }
                  />
                </td>
                <td className="px-3 py-1.5 text-zinc-100">{d.hostname ?? "—"}</td>
                <td className="px-3 py-1.5 text-zinc-400 font-mono text-xs">{d.serial}</td>
                <td className="px-3 py-1.5 text-zinc-400">{d.model ?? "—"}</td>
                <td className="px-3 py-1.5 text-zinc-400">{d.ip_address ?? "—"}</td>
                <td className="px-3 py-1.5 text-xs">
                  {d.connected ? (
                    <span className="text-emerald-400">yes</span>
                  ) : (
                    <span className="text-zinc-500">no</span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-xs">
                  {d.already_imported ? (
                    <span className="text-zinc-400">imported</span>
                  ) : (
                    <span className="text-blue-400">new</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="text-[11px] text-zinc-500">
        Selected devices will be imported with <span className="text-zinc-300">proxy via Panorama</span> enabled by default — they'll start polling immediately using this Panorama's API key. You can switch individual devices to direct polling later.
      </div>

      <div className="flex items-center gap-2">
        <Button
          variant="primary"
          onClick={() => sync.mutate(selectedSerials)}
          disabled={sync.isPending || selectedSerials.length === 0}
        >
          {sync.isPending ? "Importing…" : `Import ${selectedSerials.length} device${selectedSerials.length === 1 ? "" : "s"}`}
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function DeviceForm({
  panos,
  initial,
  onDone,
}: {
  panos: Panorama[];
  initial?: Device;
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState(initial?.name ?? "");
  const [hostname, setHostname] = useState(initial?.hostname ?? "");
  const [ipAddress, setIpAddress] = useState(initial?.ip_address ?? "");
  const initialMode: "direct" | "panorama" = initial?.proxy_via_panorama ? "panorama" : "direct";
  const [mode, setMode] = useState<"direct" | "panorama">(initialMode);
  const [panoramaId, setPanoramaId] = useState<number | "">(initial?.panorama_id ?? "");
  const [verifyTls, setVerifyTls] = useState(initial?.verify_tls ?? true);
  const [pollingEnabled, setPollingEnabled] = useState(initial?.polling_enabled ?? true);
  const [auth, setAuth] = useState({
    mode: (initial ? "unchanged" : "userpass") as AuthMode,
    username: "",
    password: "",
    api_key: "",
  });
  const [err, setErr] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const body: DeviceInput = {
        name,
        hostname,
        ip_address: ipAddress || null,
        panorama_id: mode === "panorama" ? Number(panoramaId) : null,
        proxy_via_panorama: mode === "panorama",
        verify_tls: verifyTls,
        polling_enabled: pollingEnabled,
        // Auth is irrelevant when proxying through Panorama — the Panorama
        // provides the key.
        auth: mode === "panorama" ? null : authToPayload(auth),
      };
      return initial ? api.updateDevice(initial.id, body) : api.createDevice(body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] });
      onDone();
    },
    onError: (e: Error) => setErr(e.message),
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setErr(null);
        save.mutate();
      }}
      className="border-b border-zinc-800 p-4 grid gap-3"
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} required />
        </Field>
        <Field label="Hostname (FQDN)">
          <Input value={hostname} onChange={(e) => setHostname(e.target.value)} required />
        </Field>
        <Field label="Management IP (optional)" hint="Used in place of hostname when set">
          <Input value={ipAddress} onChange={(e) => setIpAddress(e.target.value)} />
        </Field>
        <Field label="How to reach this device">
          <Select value={mode} onChange={(e) => setMode(e.target.value as "direct" | "panorama")}>
            <option value="direct">Direct (its own API key)</option>
            <option value="panorama">Via Panorama (target-serial proxy)</option>
          </Select>
        </Field>
      </div>

      {mode === "panorama" && (
        <Field label="Panorama">
          <Select
            value={panoramaId}
            onChange={(e) => setPanoramaId(Number(e.target.value))}
            required
          >
            <option value="">— pick one —</option>
            {panos.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.hostname})
              </option>
            ))}
          </Select>
        </Field>
      )}

      {mode === "direct" && (
        <>
          {!initial?.has_api_key && initial?.proxy_via_panorama && (
            <div className="rounded border border-amber-700 bg-amber-900/20 text-amber-200 text-xs p-3">
              This device is switching from <span className="font-semibold">Panorama proxy</span> to{" "}
              <span className="font-semibold">direct polling</span>, but it doesn't have its own
              API key yet. Choose Mint or Paste below — saving without auth will fail.
            </div>
          )}
          <AuthSection
            has_existing={!!initial?.has_api_key}
            value={auth}
            onChange={setAuth}
            hostHint={ipAddress || hostname || "this device"}
          />
          {initial?.has_api_key && initial?.proxy_via_panorama && (
            <div className="text-[11px] text-zinc-500">
              This device already has its own API key stored from a previous direct-mode setup —
              "Keep existing" will reuse it.
            </div>
          )}
        </>
      )}

      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
          Verify TLS certificate
        </label>
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={pollingEnabled}
            onChange={(e) => setPollingEnabled(e.target.checked)}
          />
          Polling enabled
        </label>
      </div>

      {err && <div className="text-xs text-rose-400">{err}</div>}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? "Saving…" : initial ? "Save changes" : "Save"}
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
