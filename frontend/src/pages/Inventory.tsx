import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FormEvent, useState } from "react";

import { api, Credential, Device, Panorama } from "../api";
import { Button, Card, CardHeader, Empty, Field, Input, Select } from "../components/ui";

export default function Inventory() {
  return (
    <div className="space-y-6">
      <CredentialsSection />
      <PanoramasSection />
      <DevicesSection />
    </div>
  );
}

// =====================================================================
// Credentials
// =====================================================================

type CredMode = "from_userpass" | "store_userpass" | "api_key";

function CredentialsSection() {
  const qc = useQueryClient();
  const credsQ = useQuery({ queryKey: ["credentials"], queryFn: api.listCredentials });
  const [adding, setAdding] = useState(false);

  const del = useMutation({
    mutationFn: api.deleteCredential,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });

  const creds: Credential[] = credsQ.data ?? [];

  return (
    <Card>
      <CardHeader
        title="Credentials"
        description="Stored secrets used to talk to firewalls and Panoramas. Username/password is decrypted only in memory; API keys are encrypted at rest."
        action={
          <Button variant="primary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "Add credential"}
          </Button>
        }
      />
      {adding && <CredentialForm onDone={() => setAdding(false)} />}
      {creds.length === 0 ? (
        <Empty>No credentials yet.</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Name</th>
              <th className="text-left px-4 py-2 font-medium">Type</th>
              <th className="text-left px-4 py-2 font-medium">Scope</th>
              <th className="text-left px-4 py-2 font-medium">Description</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {creds.map((c) => (
              <tr key={c.id} className="border-b border-zinc-800/50">
                <td className="px-4 py-2 text-zinc-100">{c.name}</td>
                <td className="px-4 py-2 text-zinc-400">{c.auth_type}</td>
                <td className="px-4 py-2 text-zinc-400">{c.scope}</td>
                <td className="px-4 py-2 text-zinc-500 text-xs">{c.description ?? "—"}</td>
                <td className="px-4 py-2 text-right">
                  <Button variant="danger" onClick={() => del.mutate(c.id)}>
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function CredentialForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [mode, setMode] = useState<CredMode>("from_userpass");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState<"device" | "panorama">("panorama");
  const [hostname, setHostname] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [verifyTls, setVerifyTls] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: async () => {
      if (mode === "from_userpass") {
        return api.createCredentialFromUserpass({
          name, description, scope,
          target_hostname: hostname,
          username, password,
          verify_tls: verifyTls,
        });
      }
      if (mode === "store_userpass") {
        return api.createCredential({
          name, description, scope,
          auth_type: "userpass",
          username, password,
        });
      }
      return api.createCredential({
        name, description, scope,
        auth_type: "api_key",
        api_key: apiKey,
      });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["credentials"] });
      onDone();
    },
    onError: (e: Error) => setErr(e.message),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setErr(null);
    create.mutate();
  }

  return (
    <form onSubmit={submit} className="border-b border-zinc-800 bg-zinc-950/40 p-4 grid gap-3">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Field label="Mode" hint="How this credential is created and stored">
          <Select value={mode} onChange={(e) => setMode(e.target.value as CredMode)}>
            <option value="from_userpass">Mint API key from username/password (recommended)</option>
            <option value="store_userpass">Store username/password</option>
            <option value="api_key">Paste API key directly</option>
          </Select>
        </Field>
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} required placeholder="prod-panorama-key" />
        </Field>
        <Field label="Scope">
          <Select value={scope} onChange={(e) => setScope(e.target.value as "device" | "panorama")}>
            <option value="panorama">Panorama</option>
            <option value="device">Device</option>
          </Select>
        </Field>
      </div>

      {mode === "from_userpass" && (
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <Field label="Target hostname or IP" hint="The Panorama or firewall we'll call keygen against">
            <Input value={hostname} onChange={(e) => setHostname(e.target.value)} required />
          </Field>
          <Field label="Username">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="off" />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
            />
          </Field>
        </div>
      )}

      {mode === "store_userpass" && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Field label="Username">
            <Input value={username} onChange={(e) => setUsername(e.target.value)} required autoComplete="off" />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              autoComplete="new-password"
            />
          </Field>
        </div>
      )}

      {mode === "api_key" && (
        <Field label="API key">
          <Input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            required
            autoComplete="new-password"
          />
        </Field>
      )}

      <Field label="Description (optional)">
        <Input value={description} onChange={(e) => setDescription(e.target.value)} />
      </Field>

      {mode === "from_userpass" && (
        <label className="flex items-center gap-2 text-xs text-zinc-400">
          <input
            type="checkbox"
            checked={verifyTls}
            onChange={(e) => setVerifyTls(e.target.checked)}
          />
          Verify TLS certificate of the target during keygen
        </label>
      )}

      {err && <div className="text-xs text-rose-400">{err}</div>}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Save"}
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

// =====================================================================
// Panoramas
// =====================================================================

function PanoramasSection() {
  const qc = useQueryClient();
  const panosQ = useQuery({ queryKey: ["panoramas"], queryFn: api.listPanoramas });
  const credsQ = useQuery({ queryKey: ["credentials"], queryFn: api.listCredentials });
  const [adding, setAdding] = useState(false);

  const test = useMutation({
    mutationFn: api.testPanorama,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["panoramas"] }),
  });
  const sync = useMutation({
    mutationFn: api.syncPanorama,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["panoramas"] });
      qc.invalidateQueries({ queryKey: ["devices"] });
    },
  });
  const del = useMutation({
    mutationFn: api.deletePanorama,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["panoramas"] }),
  });

  const panos: Panorama[] = panosQ.data ?? [];
  const panoCreds = (credsQ.data ?? []).filter((c) => c.scope === "panorama" && c.auth_type === "api_key");

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
      {adding && (
        <PanoramaForm
          creds={panoCreds}
          onDone={() => setAdding(false)}
        />
      )}
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
              <tr key={p.id} className="border-b border-zinc-800/50 align-top">
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
                  <Button onClick={() => sync.mutate(p.id)} disabled={sync.isPending}>
                    Sync devices
                  </Button>
                  <Button variant="danger" onClick={() => del.mutate(p.id)}>
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function PanoramaForm({ creds, onDone }: { creds: Credential[]; onDone: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [credentialId, setCredentialId] = useState<number | "">("");
  const [verifyTls, setVerifyTls] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.createPanorama({
        name,
        hostname,
        credential_id: Number(credentialId),
        verify_tls: verifyTls,
      }),
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
        create.mutate();
      }}
      className="border-b border-zinc-800 bg-zinc-950/40 p-4 grid gap-3"
    >
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} required />
        </Field>
        <Field label="Hostname or IP">
          <Input value={hostname} onChange={(e) => setHostname(e.target.value)} required />
        </Field>
        <Field label="Credential" hint="Must be an API-key credential scoped 'panorama'">
          <Select
            value={credentialId}
            onChange={(e) => setCredentialId(Number(e.target.value))}
            required
          >
            <option value="">— pick one —</option>
            {creds.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
      </div>
      <label className="flex items-center gap-2 text-xs text-zinc-400">
        <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
        Verify TLS certificate
      </label>
      {err && <div className="text-xs text-rose-400">{err}</div>}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Save"}
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
  const credsQ = useQuery({ queryKey: ["credentials"], queryFn: api.listCredentials });
  const panosQ = useQuery({ queryKey: ["panoramas"], queryFn: api.listPanoramas });
  const [adding, setAdding] = useState(false);

  const test = useMutation({
    mutationFn: api.testDevice,
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
        description="Firewalls polled by the capacity analyzer. Devices imported via Panorama sync show source=panorama and can be configured to proxy through it."
        action={
          <Button variant="primary" onClick={() => setAdding((v) => !v)}>
            {adding ? "Cancel" : "Add device"}
          </Button>
        }
      />
      {adding && (
        <DeviceForm
          creds={(credsQ.data ?? []).filter((c) => c.scope === "device" && c.auth_type === "api_key")}
          panos={panosQ.data ?? []}
          onDone={() => setAdding(false)}
        />
      )}
      {devs.length === 0 ? (
        <Empty>No devices yet.</Empty>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs uppercase text-zinc-500 border-b border-zinc-800">
            <tr>
              <th className="text-left px-4 py-2 font-medium">Name</th>
              <th className="text-left px-4 py-2 font-medium">Host</th>
              <th className="text-left px-4 py-2 font-medium">Model</th>
              <th className="text-left px-4 py-2 font-medium">Source</th>
              <th className="text-left px-4 py-2 font-medium">Polling</th>
              <th className="text-left px-4 py-2 font-medium">Last poll</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {devs.map((d) => (
              <tr key={d.id} className="border-b border-zinc-800/50 align-top">
                <td className="px-4 py-2 text-zinc-100">{d.name}</td>
                <td className="px-4 py-2 text-zinc-400">{d.ip_address ?? d.hostname}</td>
                <td className="px-4 py-2 text-zinc-400">{d.model ?? "—"}</td>
                <td className="px-4 py-2 text-zinc-500 text-xs">
                  {d.source}
                  {d.proxy_via_panorama ? " (proxied)" : ""}
                </td>
                <td className="px-4 py-2 text-xs">
                  {d.polling_enabled ? (
                    <span className="text-emerald-400">on</span>
                  ) : (
                    <span className="text-zinc-500">off</span>
                  )}
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
                  <Button variant="danger" onClick={() => del.mutate(d.id)}>
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

function DeviceForm({
  creds,
  panos,
  onDone,
}: {
  creds: Credential[];
  panos: Panorama[];
  onDone: () => void;
}) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [ipAddress, setIpAddress] = useState("");
  const [mode, setMode] = useState<"direct" | "panorama">("direct");
  const [credentialId, setCredentialId] = useState<number | "">("");
  const [panoramaId, setPanoramaId] = useState<number | "">("");
  const [proxyViaPanorama, setProxyViaPanorama] = useState(false);
  const [verifyTls, setVerifyTls] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: () =>
      api.createDevice({
        name,
        hostname,
        ip_address: ipAddress || null,
        credential_id: mode === "direct" ? Number(credentialId) : null,
        panorama_id: mode === "panorama" ? Number(panoramaId) : null,
        proxy_via_panorama: mode === "panorama" && proxyViaPanorama,
        verify_tls: verifyTls,
      }),
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
        create.mutate();
      }}
      className="border-b border-zinc-800 bg-zinc-950/40 p-4 grid gap-3"
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
            <option value="direct">Direct (use a device credential)</option>
            <option value="panorama">Via Panorama (target-serial proxy)</option>
          </Select>
        </Field>
      </div>

      {mode === "direct" && (
        <Field label="Credential" hint="API-key credentials scoped 'device'">
          <Select
            value={credentialId}
            onChange={(e) => setCredentialId(Number(e.target.value))}
            required
          >
            <option value="">— pick one —</option>
            {creds.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </Select>
        </Field>
      )}

      {mode === "panorama" && (
        <>
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
          <label className="flex items-center gap-2 text-xs text-zinc-400">
            <input
              type="checkbox"
              checked={proxyViaPanorama}
              onChange={(e) => setProxyViaPanorama(e.target.checked)}
            />
            Proxy all API calls through Panorama (required if the device's mgmt plane is not reachable from this host)
          </label>
        </>
      )}

      <label className="flex items-center gap-2 text-xs text-zinc-400">
        <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
        Verify TLS certificate
      </label>

      {err && <div className="text-xs text-rose-400">{err}</div>}

      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={create.isPending}>
          {create.isPending ? "Saving…" : "Save"}
        </Button>
        <Button type="button" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
