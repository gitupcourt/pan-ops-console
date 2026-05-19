// All API calls go through the Vite dev-server proxy at /api (see vite.config.ts).

const base = "/api";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {}
    throw new Error(detail);
  }
  // 204 No Content
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// ---------- Types ----------

export type AuthType = "api_key" | "userpass";
export type CredentialScope = "device" | "panorama";

export type Credential = {
  id: number;
  name: string;
  description: string | null;
  auth_type: AuthType;
  scope: CredentialScope;
  created_at: string;
  updated_at: string;
};

export type CredentialCreate = {
  name: string;
  description?: string | null;
  auth_type: AuthType;
  scope: CredentialScope;
  api_key?: string;
  username?: string;
  password?: string;
};

export type CredentialFromUserpass = {
  name: string;
  description?: string | null;
  scope: CredentialScope;
  target_hostname: string;
  username: string;
  password: string;
  verify_tls?: boolean;
};

export type Panorama = {
  id: number;
  name: string;
  hostname: string;
  credential_id: number;
  verify_tls: boolean;
  reachable: boolean;
  last_sync_at: string | null;
  last_reachability_at: string | null;
  last_reachability_error: string | null;
};

export type PanoramaCreate = {
  name: string;
  hostname: string;
  credential_id: number;
  verify_tls?: boolean;
};

export type Device = {
  id: number;
  name: string;
  hostname: string;
  ip_address: string | null;
  serial: string | null;
  model: string | null;
  sw_version: string | null;
  source: "direct" | "panorama";
  panorama_id: number | null;
  credential_id: number | null;
  verify_tls: boolean;
  proxy_via_panorama: boolean;
  polling_enabled: boolean;
  last_poll_at: string | null;
  last_poll_error: string | null;
};

export type DeviceCreate = {
  name: string;
  hostname: string;
  ip_address?: string | null;
  credential_id?: number | null;
  panorama_id?: number | null;
  verify_tls?: boolean;
  proxy_via_panorama?: boolean;
  polling_enabled?: boolean;
};

export type MetricSpec = {
  name: string;
  category: "config" | "system" | "traffic";
  description: string;
  has_max: boolean;
  status: "verified" | "probable" | "needs_work";
};

export type Sample = {
  ts: string;
  current: number;
  max: number | null;
  pct: number | null;
};

export type MetricSeries = {
  device_id: number;
  metric: string;
  samples: Sample[];
};

// ---------- API ----------

export const api = {
  // Credentials
  listCredentials: () => j<Credential[]>("/credentials"),
  createCredential: (body: CredentialCreate) =>
    j<Credential>("/credentials", { method: "POST", body: JSON.stringify(body) }),
  createCredentialFromUserpass: (body: CredentialFromUserpass) =>
    j<Credential>("/credentials/from-userpass", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteCredential: (id: number) =>
    j<void>(`/credentials/${id}`, { method: "DELETE" }),

  // Panoramas
  listPanoramas: () => j<Panorama[]>("/panoramas"),
  createPanorama: (body: PanoramaCreate) =>
    j<Panorama>("/panoramas", { method: "POST", body: JSON.stringify(body) }),
  testPanorama: (id: number) =>
    j<{ ok: boolean; info: Record<string, string> }>(
      `/panoramas/${id}/test-connection`,
      { method: "POST" },
    ),
  syncPanorama: (id: number) =>
    j<Panorama>(`/panoramas/${id}/sync`, { method: "POST" }),
  deletePanorama: (id: number) =>
    j<void>(`/panoramas/${id}`, { method: "DELETE" }),

  // Devices
  listDevices: () => j<Device[]>("/devices"),
  createDevice: (body: DeviceCreate) =>
    j<Device>("/devices", { method: "POST", body: JSON.stringify(body) }),
  updateDevice: (id: number, body: Partial<DeviceCreate>) =>
    j<Device>(`/devices/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  testDevice: (id: number) =>
    j<{ ok: boolean; info: Record<string, string> }>(
      `/devices/${id}/test-connection`,
      { method: "POST" },
    ),
  deleteDevice: (id: number) =>
    j<void>(`/devices/${id}`, { method: "DELETE" }),

  // Metrics
  listCatalog: () => j<MetricSpec[]>("/metrics/catalog"),
  getSeries: (deviceId: number, metric: string, hours = 24) =>
    j<MetricSeries>(`/metrics/${deviceId}/${metric}?hours=${hours}`),
  pollNow: () => j<{ status: string }>("/metrics/poll/run-now", { method: "POST" }),
};
