import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api } from "@/lib/api";
import type { Credential, Device } from "@/lib/types";
import { INPUT_CLS } from "@/lib/types";
import { Field } from "@/components/ui/Field";

/**
 * Edit-an-existing-device modal. The backend's PATCH /api/devices/{id}
 * accepts a partial payload and only updates the fields that are present,
 * so this form only sends the keys the operator actually changed (avoids
 * accidentally clobbering Panorama-sourced fields by re-sending stale
 * values).
 *
 * We deliberately limit the editable fields to ones it's safe and useful
 * for an operator to touch:
 *   - name, hostname, ip_address — for direct-attached devices that
 *     moved networks or got renamed
 *   - credential_id — to rotate creds without re-adding the device
 *   - verify_tls — for the self-signed-cert case
 *   - proxy_via_panorama — only meaningful for Panorama-sourced devices
 *
 * Identity fields like `serial` are NOT editable; they're derived from
 * probes and changing them would break Panorama linkage.
 */
export function EditDeviceModal({
  device,
  onClose,
}: {
  device: Device;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const creds = useQuery({
    queryKey: ["credentials"],
    queryFn: () => api<Credential[]>("/api/credentials"),
  });

  // Local form state, seeded from the live device row. We track each field
  // separately so the diff sent to the server only contains keys the user
  // changed — important because PATCH treats undefined as "leave alone" but
  // null/empty-string as "clear it."
  const [name, setName] = useState(device.name);
  const [hostname, setHostname] = useState(device.hostname);
  const [ipAddress, setIpAddress] = useState(device.ip_address ?? "");
  const [credentialId, setCredentialId] = useState<number | "">(
    device.credential_id ?? "",
  );
  const [verifyTls, setVerifyTls] = useState(device.verify_tls);
  const [proxyViaPanorama, setProxyViaPanorama] = useState(device.proxy_via_panorama);

  const save = useMutation({
    mutationFn: () => {
      // Build a delta object — only changed fields go through.
      const body: Record<string, unknown> = {};
      if (name !== device.name) body.name = name.trim();
      if (hostname !== device.hostname) body.hostname = hostname.trim();
      if ((ipAddress || null) !== (device.ip_address || null)) {
        body.ip_address = ipAddress.trim() || null;
      }
      if ((credentialId || null) !== (device.credential_id || null)) {
        body.credential_id = credentialId === "" ? null : credentialId;
      }
      if (verifyTls !== device.verify_tls) body.verify_tls = verifyTls;
      if (proxyViaPanorama !== device.proxy_via_panorama) {
        body.proxy_via_panorama = proxyViaPanorama;
      }
      return api(`/api/devices/${device.id}`, { method: "PATCH", body });
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] });
      onClose();
    },
  });

  // True when nothing has changed — disables Save so the operator can't
  // accidentally trigger a no-op write.
  const dirty =
    name !== device.name
    || hostname !== device.hostname
    || (ipAddress || null) !== (device.ip_address || null)
    || (credentialId || null) !== (device.credential_id || null)
    || verifyTls !== device.verify_tls
    || proxyViaPanorama !== device.proxy_via_panorama;

  const isPanoramaManaged = device.source === "panorama";
  const deviceCreds = (creds.data ?? []).filter((c) => c.scope === "device");

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="my-8 w-full max-w-2xl rounded-lg border border-slate-700 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-slate-800 px-5 py-3">
          <div>
            <h3 className="text-base font-semibold">Edit device</h3>
            <p className="text-xs text-slate-400">
              {device.name}
              {device.serial && (
                <span className="text-slate-500"> · {device.serial}</span>
              )}
            </p>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="space-y-3 p-5">
          <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
            <Field label="Name (display)">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className={INPUT_CLS}
              />
            </Field>
            <Field label="Hostname">
              <input
                value={hostname}
                onChange={(e) => setHostname(e.target.value)}
                className={INPUT_CLS}
                disabled={isPanoramaManaged}
                title={
                  isPanoramaManaged
                    ? "Hostname is synced from Panorama and not editable here"
                    : ""
                }
              />
            </Field>
            <Field label="Management IP (preferred over hostname)">
              <input
                value={ipAddress}
                onChange={(e) => setIpAddress(e.target.value)}
                className={INPUT_CLS}
                placeholder="leave blank to resolve from hostname"
              />
            </Field>
            <Field label="Credential">
              <select
                value={credentialId}
                onChange={(e) =>
                  setCredentialId(e.target.value ? Number(e.target.value) : "")
                }
                className={INPUT_CLS}
              >
                <option value="">— None (use Panorama credential) —</option>
                {deviceCreds.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.auth_type})
                  </option>
                ))}
              </select>
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={verifyTls}
                onChange={(e) => setVerifyTls(e.target.checked)}
              />
              Verify TLS certificate
            </label>
            <label
              className={`flex items-center gap-2 text-sm ${
                isPanoramaManaged ? "text-slate-300" : "text-slate-500"
              }`}
              title={
                isPanoramaManaged
                  ? ""
                  : "Only meaningful for Panorama-managed devices"
              }
            >
              <input
                type="checkbox"
                checked={proxyViaPanorama}
                onChange={(e) => setProxyViaPanorama(e.target.checked)}
                disabled={!isPanoramaManaged}
              />
              Proxy connections via Panorama
            </label>
          </div>

          {save.isError && (
            <div className="rounded border border-red-800 bg-red-950/40 px-3 py-2 text-sm text-red-300">
              Save failed: {(save.error as Error).message}
            </div>
          )}

          <p className="text-xs text-slate-500">
            Tip: setting Management IP directly bypasses DNS — useful when the
            container can resolve the hostname but lands on a stale or
            collision-range IP.
          </p>
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-800 px-5 py-3">
          <button
            onClick={onClose}
            className="rounded border border-slate-700 px-3 py-1.5 text-sm hover:bg-slate-800"
          >
            Cancel
          </button>
          <button
            onClick={() => save.mutate()}
            disabled={!dirty || save.isPending}
            className="rounded bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {save.isPending ? "Saving…" : "Save changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
