import { useState } from "react";
import { api } from "@/lib/api";
import type { Credential } from "@/lib/types";
import { INPUT_CLS } from "@/lib/types";
import { Field } from "@/components/ui/Field";

/**
 * Form to add a standalone (non-Panorama-managed) firewall. After save the
 * backend probes the device to fill in version/model/HA, so the new row
 * shows up with real data within seconds. `credentials` is the list of
 * device-scoped creds the operator can pick from.
 */
export function AddDeviceForm({
  credentials, onClose, onCreated,
}: {
  credentials: Credential[];
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [hostname, setHostname] = useState("");
  const [credentialId, setCredentialId] = useState<number | "">("");
  const [verifyTls, setVerifyTls] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (!credentialId) return setError("Pick a device-scoped credential.");
    setBusy(true);
    try {
      await api("/api/devices/direct", {
        method: "POST",
        body: { name, hostname, credential_id: credentialId, verify_tls: verifyTls },
      });
      onCreated();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to add device");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-3 rounded-lg border border-slate-800 bg-slate-900 p-4">
      <div className="flex items-center justify-between">
        <h3 className="font-semibold">Add standalone device</h3>
        <button type="button" onClick={onClose} className="text-sm text-slate-400 hover:text-slate-200">Cancel</button>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field label="Name (display)">
          <input value={name} onChange={(e) => setName(e.target.value)} required className={INPUT_CLS} placeholder="dmz-fw1" />
        </Field>
        <Field label="Hostname / IP">
          <input value={hostname} onChange={(e) => setHostname(e.target.value)} required className={INPUT_CLS} placeholder="fw1.example.com" />
        </Field>
        <Field label="Credential">
          <select value={credentialId} onChange={(e) => setCredentialId(e.target.value ? Number(e.target.value) : "")} required className={INPUT_CLS}>
            <option value="">— Pick a device-scoped credential —</option>
            {credentials.map((c) => (<option key={c.id} value={c.id}>{c.name} ({c.auth_type})</option>))}
          </select>
          {credentials.length === 0 && (
            <p className="mt-1 text-xs text-amber-400">No device-scoped credentials. Add one in Settings first.</p>
          )}
        </Field>
        <div className="flex items-center pt-6">
          <label className="flex items-center gap-2 text-sm text-slate-300">
            <input type="checkbox" checked={verifyTls} onChange={(e) => setVerifyTls(e.target.checked)} />
            Verify TLS certificate
          </label>
        </div>
      </div>

      {error && <div className="text-sm text-red-400">{error}</div>}
      <p className="text-xs text-slate-500">
        After adding, the app probes the device to populate version, model, content, and HA info.
      </p>

      <div className="flex gap-2">
        <button type="submit" disabled={busy} className="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50">
          {busy ? "Adding…" : "Add device"}
        </button>
      </div>
    </form>
  );
}
