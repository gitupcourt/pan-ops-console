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
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// ---------- Types ----------

export type AuthFromUserpass = { mode: "userpass"; username: string; password: string };
export type AuthFromApiKey = { mode: "api_key"; api_key: string };
export type AuthPayload = AuthFromUserpass | AuthFromApiKey;

export type Panorama = {
  id: number;
  name: string;
  hostname: string;
  has_api_key: boolean;
  verify_tls: boolean;
  reachable: boolean;
  last_sync_at: string | null;
  last_reachability_at: string | null;
  last_reachability_error: string | null;
};

export type PanoramaInput = {
  name: string;
  hostname: string;
  verify_tls?: boolean;
  auth?: AuthPayload | null;
};

export type PanoramaDevicePreview = {
  serial: string;
  hostname: string | null;
  ip_address: string | null;
  model: string | null;
  sw_version: string | null;
  connected: boolean;
  already_imported: boolean;
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
  has_api_key: boolean;
  verify_tls: boolean;
  proxy_via_panorama: boolean;
  polling_enabled: boolean;
  last_poll_at: string | null;
  last_poll_error: string | null;
};

export type DeviceInput = {
  name: string;
  hostname: string;
  ip_address?: string | null;
  panorama_id?: number | null;
  verify_tls?: boolean;
  proxy_via_panorama?: boolean;
  polling_enabled?: boolean;
  auth?: AuthPayload | null;
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
  // Panoramas
  listPanoramas: () => j<Panorama[]>("/panoramas"),
  createPanorama: (body: PanoramaInput) =>
    j<Panorama>("/panoramas", { method: "POST", body: JSON.stringify(body) }),
  updatePanorama: (id: number, body: PanoramaInput) =>
    j<Panorama>(`/panoramas/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  testPanorama: (id: number) =>
    j<{ ok: boolean; info: Record<string, string> }>(
      `/panoramas/${id}/test-connection`,
      { method: "POST" },
    ),
  previewPanoramaDevices: (id: number) =>
    j<PanoramaDevicePreview[]>(`/panoramas/${id}/preview-devices`),
  syncPanorama: (id: number, serials?: string[]) =>
    j<Panorama>(`/panoramas/${id}/sync`, {
      method: "POST",
      body: JSON.stringify({ serials: serials ?? null }),
    }),
  deletePanorama: (id: number) =>
    j<void>(`/panoramas/${id}`, { method: "DELETE" }),

  // Devices
  listDevices: () => j<Device[]>("/devices"),
  createDevice: (body: DeviceInput) =>
    j<Device>("/devices", { method: "POST", body: JSON.stringify(body) }),
  updateDevice: (id: number, body: DeviceInput) =>
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
