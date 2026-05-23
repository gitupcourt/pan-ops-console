// All API calls go through the Vite dev-server proxy at /api (see vite.config.ts).

const base = "/api";

export class ApiError extends Error {
  status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.status = status;
  }
}

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json" },
    // Session cookie auth — always send credentials so the cookie rides.
    credentials: "include",
    ...init,
  });
  if (!resp.ok) {
    let detail = `${resp.status} ${resp.statusText}`;
    try {
      const body = await resp.json();
      if (body?.detail) detail = body.detail;
    } catch {}
    throw new ApiError(resp.status, detail);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json() as Promise<T>;
}

// ---------- Types ----------

export type User = {
  id: number;
  username: string;
  email: string | null;
  is_admin: boolean;
  is_active: boolean;
  totp_enabled: boolean;
  created_at: string;
  last_login_at: string | null;
};

export type BootstrapStatus = {
  needs_bootstrap: boolean;
  oidc_providers: string[];
};

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
  // Auth
  bootstrapStatus: () => j<BootstrapStatus>("/auth/bootstrap-status"),
  signupFirst: (body: { username: string; email?: string | null; password: string }) =>
    j<User>("/auth/signup-first", { method: "POST", body: JSON.stringify(body) }),
  // Returns the User object on success, OR { needs_totp: true } when the
  // password is valid but TOTP is required. Caller should narrow based on
  // the response shape.
  login: (body: { username: string; password: string; totp_code?: string | null }) =>
    j<User | { needs_totp: true }>("/auth/login", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  logout: () => j<void>("/auth/logout", { method: "POST" }),
  me: () => j<User>("/auth/me"),
  changePassword: (body: { current_password: string; new_password: string }) =>
    j<void>("/auth/change-password", { method: "POST", body: JSON.stringify(body) }),
  totpSetup: () =>
    j<{ secret: string; otpauth_uri: string }>("/auth/totp/setup", { method: "POST" }),
  totpVerify: (code: string) =>
    j<{ backup_codes: string[] }>("/auth/totp/verify", {
      method: "POST",
      body: JSON.stringify({ code }),
    }),
  totpDisable: (password: string) =>
    j<void>("/auth/totp/disable", {
      method: "POST",
      body: JSON.stringify({ password }),
    }),

  // Users (admin)
  listUsers: () => j<User[]>("/users"),
  createUser: (body: { username: string; email?: string | null; password: string; is_admin?: boolean }) =>
    j<User>("/users", { method: "POST", body: JSON.stringify(body) }),
  setUserActive: (id: number, active: boolean) =>
    j<User>(`/users/${id}/active?active=${active}`, { method: "PATCH" }),
  setUserAdmin: (id: number, is_admin: boolean) =>
    j<User>(`/users/${id}/admin?is_admin=${is_admin}`, { method: "PATCH" }),
  deleteUser: (id: number) => j<void>(`/users/${id}`, { method: "DELETE" }),

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
  getDeviceCapacity: (id: number) =>
    j<{ items: { key: string; value: number | null; raw: string }[] }>(
      `/devices/${id}/capacity`,
    ),
  deleteDevice: (id: number) =>
    j<void>(`/devices/${id}`, { method: "DELETE" }),

  // Metrics
  listCatalog: () => j<MetricSpec[]>("/metrics/catalog"),
  getSeries: (deviceId: number, metric: string, hours = 24) =>
    j<MetricSeries>(`/metrics/${deviceId}/${metric}?hours=${hours}`),
  pollNow: () => j<{ status: string }>("/metrics/poll/run-now", { method: "POST" }),
};
