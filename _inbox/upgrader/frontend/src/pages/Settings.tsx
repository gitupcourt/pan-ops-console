import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";

const PANORAMA_KEYGEN_DOCS =
  "https://docs.paloaltonetworks.com/pan-os/11-1/pan-os-panorama-api/get-started-with-the-pan-os-xml-api/get-your-api-key";

type Credential = {
  id: number;
  name: string;
  description: string | null;
  scope: "device" | "panorama";
  auth_type: "api_key" | "userpass";
};

type Panorama = {
  id: number;
  name: string;
  hostname: string;
  credential_id: number;
  proxy_upgrades: boolean;
  verify_tls: boolean;
  last_sync_at: string | null;
  reachable: boolean;
  last_reachability_at: string | null;
  last_reachability_error: string | null;
};

export default function Settings() {
  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <CredentialsSection />
      <PanoramasSection />
      <PrecheckSetsSection />
    </div>
  );
}

// ---------- Pre-check sets ----------

type PrecheckSet = {
  id: number;
  name: string;
  description: string | null;
  checks: string[];
  is_default: boolean;
};

type AvailablePrechecks = { all: string[]; default: string[] };

function PrecheckSetsSection() {
  const qc = useQueryClient();
  const sets = useQuery({
    queryKey: ["precheck-sets"],
    queryFn: () => api<PrecheckSet[]>("/api/precheck-sets"),
  });
  const available = useQuery({
    queryKey: ["precheck-available"],
    queryFn: () => api<AvailablePrechecks>("/api/devices/precheck/available"),
  });

  const [editing, setEditing] = useState<PrecheckSet | "new" | null>(null);

  const del = useMutation({
    mutationFn: (id: number) => api(`/api/precheck-sets/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["precheck-sets"] }),
  });

  return (
    <section className="space-y-3">
      <header className="flex items-start justify-between">
        <div>
          <h2 className="text-lg font-semibold">Pre-check sets</h2>
          <p className="text-sm text-slate-400">
            Save named subsets of readiness checks. Operators pick one from the
            Pre-check menu on the Devices page; one set can be marked default.
          </p>
        </div>
        <button
          onClick={() => setEditing("new")}
          className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500"
        >
          New set
        </button>
      </header>

      <div className="rounded-lg border border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-3 py-2 text-left">Name</th>
              <th className="px-3 py-2 text-left">Checks</th>
              <th className="px-3 py-2 text-left">Default</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {(sets.data ?? []).map((s) => (
              <tr key={s.id} className="border-t border-slate-800">
                <td className="px-3 py-2">
                  <div className="font-medium text-slate-100">{s.name}</div>
                  {s.description && (
                    <div className="text-xs text-slate-500">{s.description}</div>
                  )}
                </td>
                <td className="px-3 py-2 text-xs text-slate-300">
                  <div className="flex flex-wrap gap-1">
                    {s.checks.map((c) => (
                      <span
                        key={c}
                        className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 font-mono"
                      >
                        {c}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-3 py-2">
                  {s.is_default ? (
                    <span className="text-emerald-300">default</span>
                  ) : (
                    <span className="text-slate-500">—</span>
                  )}
                </td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={() => setEditing(s)}
                    className="rounded border border-slate-700 px-2 py-0.5 text-xs hover:bg-slate-800"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => {
                      if (window.confirm(`Delete pre-check set "${s.name}"?`)) del.mutate(s.id);
                    }}
                    className="ml-1 rounded border border-slate-700 px-2 py-0.5 text-xs text-red-300 hover:bg-red-950"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {(sets.data ?? []).length === 0 && !sets.isLoading && (
              <tr>
                <td colSpan={4} className="px-3 py-6 text-center text-slate-500">
                  No custom sets. The built-in default check list is used
                  until you create one.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {editing && available.data && (
        <PrecheckSetEditor
          initial={editing === "new" ? null : editing}
          available={available.data}
          onClose={() => setEditing(null)}
          onSaved={() => {
            qc.invalidateQueries({ queryKey: ["precheck-sets"] });
            setEditing(null);
          }}
        />
      )}
    </section>
  );
}

function PrecheckSetEditor({
  initial,
  available,
  onClose,
  onSaved,
}: {
  initial: PrecheckSet | null;
  available: AvailablePrechecks;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  // Use a Set for O(1) toggle. Seed with the existing set's checks, or the
  // backend's "default" list when creating new — gives the operator a
  // sensible starting point.
  const [checks, setChecks] = useState<Set<string>>(
    new Set(initial?.checks ?? available.default),
  );
  const [isDefault, setIsDefault] = useState(initial?.is_default ?? false);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name,
        description: description || null,
        checks: available.all.filter((c) => checks.has(c)),
        is_default: isDefault,
      };
      return initial
        ? api(`/api/precheck-sets/${initial.id}`, { method: "PUT", body })
        : api("/api/precheck-sets", { method: "POST", body });
    },
    onSuccess: onSaved,
  });

  const toggle = (c: string) => {
    const next = new Set(checks);
    next.has(c) ? next.delete(c) : next.add(c);
    setChecks(next);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 p-4" onClick={onClose}>
      <div
        className="my-8 w-full max-w-2xl rounded-lg border border-slate-700 bg-slate-900 p-5"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-3 text-lg font-semibold">
          {initial ? `Edit "${initial.name}"` : "New pre-check set"}
        </h3>
        <div className="space-y-3">
          <div>
            <label className="text-xs uppercase tracking-wide text-slate-400">Name</label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
              placeholder="e.g. quick-pre-upgrade"
            />
          </div>
          <div>
            <label className="text-xs uppercase tracking-wide text-slate-400">Description</label>
            <input
              value={description ?? ""}
              onChange={(e) => setDescription(e.target.value)}
              className="mt-1 w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm"
              placeholder="optional"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
              Checks ({checks.size} of {available.all.length})
            </label>
            <div className="grid grid-cols-2 gap-1 rounded border border-slate-800 bg-slate-950 p-2 md:grid-cols-3">
              {available.all.map((c) => (
                <label
                  key={c}
                  className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 hover:bg-slate-800"
                >
                  <input type="checkbox" checked={checks.has(c)} onChange={() => toggle(c)} />
                  <span className="font-mono text-xs">{c}</span>
                </label>
              ))}
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={isDefault}
              onChange={(e) => setIsDefault(e.target.checked)}
            />
            Use as default when running pre-checks
          </label>
          {save.isError && (
            <div className="text-xs text-red-400">
              Save failed: {(save.error as Error).message}
            </div>
          )}
        </div>
        <div className="mt-4 flex justify-end gap-2">
          <button onClick={onClose} className="rounded border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800">
            Cancel
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={save.isPending || !name.trim() || checks.size === 0}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : initial ? "Save" : "Create"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------- Credentials ----------

function CredentialsSection() {
  const qc = useQueryClient();
  const creds = useQuery({ queryKey: ["credentials"], queryFn: () => api<Credential[]>("/api/credentials") });

  return (
    <section className="space-y-3">
      <header>
        <h2 className="text-lg font-semibold">Credentials</h2>
        <p className="text-sm text-slate-400">
          API keys or username+password used to authenticate to firewalls and Panorama.
          Stored encrypted at rest with Fernet.
        </p>
      </header>

      <CredentialForm onCreated={() => qc.invalidateQueries({ queryKey: ["credentials"] })} />

      <div className="rounded-lg border border-slate-800">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400">
            <tr>
              <th className="px-3 py-2 text-left">Name</th>
              <th className="px-3 py-2 text-left">Scope</th>
              <th className="px-3 py-2 text-left">Type</th>
              <th className="px-3 py-2 text-left">Description</th>
              <th className="px-3 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {(creds.data ?? []).map((c) => (
              <tr key={c.id} className="border-t border-slate-800">
                <td className="px-3 py-2">{c.name}</td>
                <td className="px-3 py-2">{c.scope}</td>
                <td className="px-3 py-2">{c.auth_type}</td>
                <td className="px-3 py-2 text-slate-400">{c.description ?? "—"}</td>
                <td className="px-3 py-2 text-right">
                  <button
                    onClick={async () => {
                      if (!confirm(`Delete credential "${c.name}"?`)) return;
                      await api(`/api/credentials/${c.id}`, { method: "DELETE" });
                      qc.invalidateQueries({ queryKey: ["credentials"] });
                    }}
                    className="rounded border border-slate-700 px-2 py-1 text-xs hover:bg-red-900/30 hover:border-red-700"
                  >
                    Delete
                  </button>
                </td>
              </tr>
            ))}
            {(creds.data ?? []).length === 0 && (
              <tr><td colSpan={5} className="px-3 py-6 text-center text-slate-500">No credentials yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function CredentialForm({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [scope, setScope] = useState<"device" | "panorama">("panorama");
  const [authType, setAuthType] = useState<"api_key" | "userpass">("api_key");
  const [apiKey, setApiKey] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName(""); setDescription(""); setScope("panorama"); setAuthType("api_key");
    setApiKey(""); setUsername(""); setPassword(""); setError(null);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await api("/api/credentials", {
        method: "POST",
        body: {
          name, description: description || null,
          scope, auth_type: authType,
          api_key: authType === "api_key" ? apiKey : null,
          username: authType === "userpass" ? username : null,
          password: authType === "userpass" ? password : null,
        },
      });
      reset();
      setOpen(false);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create credential");
    }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500">
        Add credential
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} required className={inputCls} /></Field>
        <Field label="Description (optional)"><input value={description} onChange={(e) => setDescription(e.target.value)} className={inputCls} /></Field>
        <Field label="Scope">
          <select value={scope} onChange={(e) => setScope(e.target.value as "device" | "panorama")} className={inputCls}>
            <option value="panorama">Panorama</option>
            <option value="device">Device</option>
          </select>
        </Field>
        <Field label="Auth type">
          <select value={authType} onChange={(e) => setAuthType(e.target.value as "api_key" | "userpass")} className={inputCls}>
            <option value="api_key">API key</option>
            <option value="userpass">Username + password</option>
          </select>
        </Field>
      </div>

      {authType === "api_key" ? (
        <Field label="API key">
          <input value={apiKey} onChange={(e) => setApiKey(e.target.value)} required type="password" className={inputCls} placeholder="LUFRPT0..." />
          <p className="mt-1 text-xs text-slate-500">
            How to generate one:{" "}
            <a href={PANORAMA_KEYGEN_DOCS} target="_blank" rel="noreferrer" className="text-indigo-400 underline">
              Palo Alto docs — Get Your API Key
            </a>
          </p>
        </Field>
      ) : (
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="Username"><input value={username} onChange={(e) => setUsername(e.target.value)} required className={inputCls} /></Field>
          <Field label="Password"><input value={password} onChange={(e) => setPassword(e.target.value)} required type="password" className={inputCls} /></Field>
        </div>
      )}

      {error && <div className="text-sm text-red-400">{error}</div>}

      <div className="flex gap-2">
        <button type="submit" className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500">
          Save credential
        </button>
        <button type="button" onClick={() => { reset(); setOpen(false); }} className="rounded-md border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800">
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---------- Panoramas ----------

function PanoramasSection() {
  const qc = useQueryClient();
  const panos = useQuery({ queryKey: ["panoramas"], queryFn: () => api<Panorama[]>("/api/panoramas") });
  const creds = useQuery({ queryKey: ["credentials"], queryFn: () => api<Credential[]>("/api/credentials") });

  return (
    <section className="space-y-3">
      <header>
        <h2 className="text-lg font-semibold">Panoramas</h2>
        <p className="text-sm text-slate-400">Connections used to discover devices and (optionally) proxy upgrades.</p>
      </header>

      <PanoramaForm
        credentials={(creds.data ?? []).filter((c) => c.scope === "panorama")}
        onCreated={() => qc.invalidateQueries({ queryKey: ["panoramas"] })}
      />

      <div className="space-y-2">
        {(panos.data ?? []).map((p) => (
          <PanoramaCard key={p.id} pano={p} />
        ))}
        {(panos.data ?? []).length === 0 && (
          <div className="rounded-lg border border-slate-800 bg-slate-900 p-4 text-sm text-slate-500">
            No Panoramas configured yet.
          </div>
        )}
      </div>
    </section>
  );
}

function PanoramaCard({ pano }: { pano: Panorama }) {
  const qc = useQueryClient();
  const [status, setStatus] = useState<{ kind: "info" | "error"; text: string } | null>(null);

  const test = useMutation({
    mutationFn: () => api<Record<string, string>>(`/api/panoramas/${pano.id}/test-connection`, { method: "POST" }),
    onSuccess: (info) => setStatus({ kind: "info", text: `Connected. ${info.hostname ?? ""} · ${info.model ?? ""} · PAN-OS ${info.sw_version ?? "?"}` }),
    onError: (err) => setStatus({ kind: "error", text: (err as Error).message }),
  });

  const importDevices = useMutation({
    mutationFn: () => api<unknown[]>(`/api/panoramas/${pano.id}/import`, { method: "POST" }),
    onSuccess: (devices) => {
      setStatus({ kind: "info", text: `Imported ${devices.length} device(s).` });
      qc.invalidateQueries({ queryKey: ["devices"] });
      qc.invalidateQueries({ queryKey: ["panoramas"] });
    },
    onError: (err) => setStatus({ kind: "error", text: (err as Error).message }),
  });

  const remove = useMutation({
    mutationFn: () => api(`/api/panoramas/${pano.id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["panoramas"] }),
  });

  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-medium">{pano.name}</span>
            <span
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] uppercase tracking-wide ${
                pano.reachable
                  ? "bg-emerald-900/40 text-emerald-300"
                  : "bg-red-900/40 text-red-300"
              }`}
              title={
                pano.last_reachability_at
                  ? `Last check: ${new Date(pano.last_reachability_at).toLocaleString()}${
                      pano.last_reachability_error ? ` — ${pano.last_reachability_error}` : ""
                    }`
                  : "Reachability not yet checked"
              }
            >
              {pano.reachable ? "reachable" : "unreachable"}
            </span>
          </div>
          <div className="text-sm text-slate-400">{pano.hostname}</div>
          <div className="mt-1 text-xs text-slate-500">
            verify_tls: {pano.verify_tls ? "on" : "off"} · proxy_upgrades: {pano.proxy_upgrades ? "on" : "off"}
            {pano.last_sync_at && ` · last sync ${new Date(pano.last_sync_at).toLocaleString()}`}
          </div>
          {!pano.reachable && pano.last_reachability_error && (
            <div className="mt-2 rounded border border-red-800 bg-red-950/30 px-2 py-1 text-xs text-red-300 max-w-md">
              {pano.last_reachability_error}
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={() => test.mutate()} disabled={test.isPending} className="rounded-md border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800 disabled:opacity-50">
            {test.isPending ? "Testing…" : "Test connection"}
          </button>
          <button onClick={() => importDevices.mutate()} disabled={importDevices.isPending} className="rounded-md bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
            {importDevices.isPending ? "Importing…" : "Import devices"}
          </button>
          <button
            onClick={() => { if (confirm(`Delete Panorama "${pano.name}"?`)) remove.mutate(); }}
            className="rounded-md border border-slate-700 px-3 py-1.5 text-sm hover:bg-red-900/30 hover:border-red-700"
          >
            Delete
          </button>
        </div>
      </div>
      {status && (
        <div className={`mt-3 rounded-md border px-3 py-2 text-sm ${
          status.kind === "error"
            ? "border-red-800 bg-red-950/40 text-red-300"
            : "border-slate-700 bg-slate-950 text-slate-300"
        }`}>
          {status.text}
        </div>
      )}
    </div>
  );
}

function PanoramaForm({ credentials, onCreated }: { credentials: Credential[]; onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [credentialId, setCredentialId] = useState<number | "">("");
  const [verifyTls, setVerifyTls] = useState(true);
  const [proxyUpgrades, setProxyUpgrades] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reset = () => {
    setName(""); setHostname(""); setCredentialId(""); setVerifyTls(true); setProxyUpgrades(false); setError(null);
  };

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!credentialId) return setError("Pick a credential.");
    try {
      await api("/api/panoramas", {
        method: "POST",
        body: {
          name, hostname, credential_id: credentialId,
          verify_tls: verifyTls, proxy_upgrades: proxyUpgrades,
        },
      });
      reset();
      setOpen(false);
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add Panorama");
    }
  }

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500">
        Add Panorama
      </button>
    );
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} required className={inputCls} placeholder="Production Panorama" /></Field>
        <Field label="Hostname"><input value={hostname} onChange={(e) => setHostname(e.target.value)} required className={inputCls} placeholder="panorama.example.com" /></Field>
        <Field label="Credential">
          <select
            value={credentialId}
            onChange={(e) => setCredentialId(e.target.value ? Number(e.target.value) : "")}
            required
            className={inputCls}
          >
            <option value="">— Pick a Panorama-scoped credential —</option>
            {credentials.map((c) => (<option key={c.id} value={c.id}>{c.name} ({c.auth_type})</option>))}
          </select>
          {credentials.length === 0 && (
            <p className="mt-1 text-xs text-amber-400">No Panorama-scoped credentials. Create one above first.</p>
          )}
        </Field>
        <div className="flex flex-col gap-2 pt-6">
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
            Verify TLS certificate
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={proxyUpgrades} onChange={(e) => setProxyUpgrades(e.target.checked)} />
            Proxy upgrades through this Panorama
          </label>
        </div>
      </div>

      {error && <div className="text-sm text-red-400">{error}</div>}

      <div className="flex gap-2">
        <button type="submit" className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500">
          Save Panorama
        </button>
        <button type="button" onClick={() => { reset(); setOpen(false); }} className="rounded-md border border-slate-700 px-3 py-2 text-sm hover:bg-slate-800">
          Cancel
        </button>
      </div>
    </form>
  );
}

// ---------- shared bits ----------

const inputCls = "w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="block text-sm text-slate-300">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}
