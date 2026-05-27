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

export type OIDCProvider = {
  id: number;
  slug: string;
  display_name: string;
  issuer: string;
  client_id: string;
  scopes: string;
  enabled: boolean;
  has_client_secret: boolean;
  created_at: string;
  updated_at: string;
};

export type OIDCProviderCreate = {
  slug: string;
  display_name: string;
  issuer: string;
  client_id: string;
  client_secret: string;
  scopes?: string;
  enabled?: boolean;
};

export type OIDCProviderUpdate = {
  display_name?: string | null;
  issuer?: string | null;
  client_id?: string | null;
  client_secret?: string | null;
  scopes?: string | null;
  enabled?: boolean | null;
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
  // Connection state. Authoritative for Panorama-imported devices —
  // refreshed every Panorama sync. For direct-added devices these fields
  // aren't meaningful (sync doesn't touch direct devices) so UI that
  // shows a "disconnected" indicator MUST gate on `source === "panorama"`
  // to avoid mis-labeling every direct device as offline.
  connected: boolean;
  last_seen_at: string | null;
  last_refresh_at: string | null;
  ha_role: "standalone" | "active" | "passive" | "unknown";
  ha_state: string | null;
  ha_peer_id: number | null;
  // Panorama-managed grouping. Null on direct-added devices.
  device_group: string | null;
  template_stack: string | null;
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

// ---------- Upgrade module ----------
//
// Mirrors `app/upgrade/schemas.py`. Job/task lifecycle is operator-
// driven through the routes: create a job → start it → confirm/override
// at park steps → done or retry on failure.

export type WorkflowType = "full" | "partial";

export type JobState =
  | "pending"
  | "running"
  | "awaiting_confirmation"
  | "completed"
  | "failed"
  | "aborted";

export type TaskPhase =
  | "pending"
  | "precheck"
  | "awaiting_precheck_override"
  | "snapshot"
  | "downloading_image"
  | "suspend_secondary"
  | "upgrade_secondary"
  | "awaiting_reboot_confirm"
  | "postcheck_secondary"
  | "awaiting_postcheck_override"
  | "awaiting_failover_confirm"
  | "failover"
  | "awaiting_primary_upgrade_confirm"
  | "upgrade_primary"
  | "postcheck_primary"
  | "failback"
  | "report"
  | "done"
  | "failed";

export type UpgradeImage = {
  id: number;
  version: string;
  filename: string | null;
  sha256: string | null;
  size_bytes: number | null;
  notes: string | null;
  created_at: string;
  uploaded: boolean;
};

export type UpgradeImageCreate = {
  version: string;
  notes?: string | null;
};

export type UpgradeJob = {
  id: number;
  name: string;
  target_version: string;
  workflow: WorkflowType;
  state: JobState;
  task_count: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type UpgradeTask = {
  id: number;
  job_id: number;
  device_id: number;
  device_name: string;
  ha_pair_key: string;
  phase: TaskPhase;
  error: string | null;
  confirmation_token: string | null;
  tick_count: number;
  created_at: string;
  updated_at: string;
};

export type UpgradeTaskDetail = UpgradeTask & {
  progress: Record<string, unknown> | null;
};

export type UpgradeJobDetail = UpgradeJob & {
  workflow_stages: string[] | null;
  image_id: number | null;
  device_pull_image: boolean;
  require_failover_confirmation: boolean;
  require_primary_upgrade_confirmation: boolean;
  auto_failback: boolean;
  auto_reboot_after_install: boolean;
  auto_ack_precheck_failures: boolean;
  auto_ack_postcheck_failures: boolean;
  tasks: UpgradeTask[];
};

export type UpgradeJobCreate = {
  name: string;
  target_version: string;
  device_ids: number[];
  workflow?: WorkflowType;
  workflow_stages?: string[] | null;
  image_id?: number | null;
  device_pull_image?: boolean;
  require_failover_confirmation?: boolean;
  require_primary_upgrade_confirmation?: boolean;
  auto_failback?: boolean;
  auto_reboot_after_install?: boolean;
  auto_ack_precheck_failures?: boolean;
  auto_ack_postcheck_failures?: boolean;
  precheck_set_id?: number | null;
};

// ---------- Precheck sets (phase 13b) ----------

export type PrecheckSetRead = {
  id: number;
  name: string;
  description: string | null;
  checks: string[];
  is_default: boolean;
  created_at: string;
  updated_at: string;
};

export type PrecheckSetCreate = {
  name: string;
  description?: string | null;
  checks: string[];
  is_default?: boolean;
};

export type PrecheckSetUpdate = {
  name?: string;
  description?: string | null;
  checks?: string[];
  is_default?: boolean;
};

export type AvailableChecks = {
  all: string[];
  default: string[];
};

// ---------- Capacity Analyzer (phase 8 endpoints) ----------
//
// Mirrors `app/capacity/routes/aggregates.py`. The three drill levels:
//   - HeatmapCell        — one per (model, metric); powers /capacity
//   - CapacityTableRow   — one per (device, metric); powers /capacity/table
//   - CapacityTrend      — time-series + prediction; powers /capacity/trend

export type MetricCategory = "config" | "system" | "traffic";

export type HeatmapDeviceTop = {
  device_id: number;
  device_name: string;
  current: number;
  max: number | null;
  pct: number | null;
};

export type HeatmapCell = {
  model: string;
  metric: string;
  category: MetricCategory | "unknown";
  metric_description: string;
  max_pct: number | null;
  device_count: number;
  top_devices: HeatmapDeviceTop[];
};

export type CapacityTableRow = {
  device_id: number;
  device_name: string;
  model: string | null;
  software_version: string | null;
  device_group: string | null;
  template_stack: string | null;
  metric: string;
  metric_description: string;
  category: MetricCategory | "unknown";
  current: number;
  max: number | null;
  pct: number | null;
  predicted_date: string | null;
  last_sample_at: string;
};

export type CapacityTableResponse = {
  rows: CapacityTableRow[];
  total: number;
};

export type TrendSample = {
  ts: string;
  current: number;
  pct: number | null;
};

export type TrendPrediction = {
  predicted_date: string | null;
  days_left: number | null;
  slope_per_day: number | null;
  method: "linear_regression" | "insufficient_data" | "decreasing";
};

export type CapacityTrend = {
  device_id: number;
  device_name: string;
  model: string | null;
  metric: string;
  metric_description: string;
  max_value: number | null;
  samples: TrendSample[];
  prediction: TrendPrediction;
};

export type CapacityTableFilters = {
  model?: string | null;
  metric?: string | null;
  device_group?: string | null;
  template_stack?: string | null;
  pct_min?: number | null;
  pct_max?: number | null;
  limit?: number;
  offset?: number;
};

export type JobsSummary = {
  pending: number;
  running: number;
  awaiting_confirmation: number;
  completed: number;
  failed: number;
  aborted: number;
};

export type VersionDistributionRow = {
  version: string | null;
  count: number;
};

// ---------- Alerts (phase 12) ----------

export type AlertSeverity = "warning" | "critical";

export type AlertRead = {
  id: number;
  severity: AlertSeverity;
  alert_name: string;
  device_id: number;
  device_name: string;
  metric: string;
  threshold_pct: number;
  current_value: number | null;
  max_value: number | null;
  pct: number | null;
  first_seen_at: string;
  last_seen_at: string;
  cleared_at: string | null;
  acknowledged_at: string | null;
  acknowledged_by: string | null;
};

export type AlertRuleCreate = {
  name: string;
  metric: string | null;
  severity: AlertSeverity;
  threshold_pct: number;
  enabled?: boolean;
};

export type AlertRuleUpdate = {
  name?: string;
  threshold_pct?: number;
  enabled?: boolean;
};

export type AlertsSummary = {
  critical: number;
  warning: number;
  acknowledged: number;
  total_active: number;
};

export type AlertRuleRead = {
  id: number;
  name: string;
  metric: string | null;
  severity: AlertSeverity;
  threshold_pct: number;
  enabled: boolean;
  created_at: string;
  updated_at: string;
};

export type AlertListFilters = {
  state?: "active" | "all";
  severity?: AlertSeverity;
  device_id?: number;
  limit?: number;
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

  // OIDC providers (admin)
  listOIDCProviders: () => j<OIDCProvider[]>("/providers/oidc"),
  createOIDCProvider: (body: OIDCProviderCreate) =>
    j<OIDCProvider>("/providers/oidc", { method: "POST", body: JSON.stringify(body) }),
  updateOIDCProvider: (id: number, body: OIDCProviderUpdate) =>
    j<OIDCProvider>(`/providers/oidc/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteOIDCProvider: (id: number) =>
    j<void>(`/providers/oidc/${id}`, { method: "DELETE" }),

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

  // Upgrade: jobs
  listUpgradeJobs: () => j<UpgradeJob[]>("/upgrade/jobs"),
  createUpgradeJob: (body: UpgradeJobCreate) =>
    j<UpgradeJobDetail>("/upgrade/jobs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getUpgradeJob: (id: number) => j<UpgradeJobDetail>(`/upgrade/jobs/${id}`),
  startUpgradeJob: (id: number) =>
    j<UpgradeJobDetail>(`/upgrade/jobs/${id}/start`, { method: "POST" }),
  abortUpgradeJob: (id: number) =>
    j<UpgradeJobDetail>(`/upgrade/jobs/${id}/abort`, { method: "POST" }),
  deleteUpgradeJob: (id: number) =>
    j<void>(`/upgrade/jobs/${id}`, { method: "DELETE" }),

  // Upgrade: precheck sets (phase 13b)
  listPrecheckSets: () => j<PrecheckSetRead[]>("/upgrade/precheck-sets"),
  listAvailableChecks: () => j<AvailableChecks>("/upgrade/precheck-sets/available"),
  createPrecheckSet: (body: PrecheckSetCreate) =>
    j<PrecheckSetRead>("/upgrade/precheck-sets", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updatePrecheckSet: (id: number, body: PrecheckSetUpdate) =>
    j<PrecheckSetRead>(`/upgrade/precheck-sets/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deletePrecheckSet: (id: number) =>
    j<void>(`/upgrade/precheck-sets/${id}`, { method: "DELETE" }),

  // Upgrade: tasks (within a job)
  getUpgradeTask: (id: number) =>
    j<UpgradeTaskDetail>(`/upgrade/tasks/${id}`),
  confirmUpgradeTask: (id: number, token: string) =>
    j<UpgradeTaskDetail>(`/upgrade/tasks/${id}/confirm`, {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  overrideUpgradeTask: (id: number, token: string) =>
    j<UpgradeTaskDetail>(`/upgrade/tasks/${id}/override`, {
      method: "POST",
      body: JSON.stringify({ token }),
    }),
  retryUpgradeTask: (id: number) =>
    j<UpgradeTaskDetail>(`/upgrade/tasks/${id}/retry`, { method: "POST" }),

  // Upgrade: image registry
  listUpgradeImages: () => j<UpgradeImage[]>("/upgrade/images"),
  registerUpgradeImage: (body: UpgradeImageCreate) =>
    j<UpgradeImage>("/upgrade/images", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteUpgradeImage: (id: number) =>
    j<void>(`/upgrade/images/${id}`, { method: "DELETE" }),

  // Capacity Analyzer (phase 8 endpoints).
  getCapacityHeatmap: (filters?: {
    device_group?: string | null;
    template_stack?: string | null;
    top_n?: number;
  }) => {
    const qs = new URLSearchParams();
    if (filters?.device_group) qs.set("device_group", filters.device_group);
    if (filters?.template_stack) qs.set("template_stack", filters.template_stack);
    if (filters?.top_n) qs.set("top_n", String(filters.top_n));
    const suffix = qs.toString();
    return j<HeatmapCell[]>(`/capacity/heatmap${suffix ? "?" + suffix : ""}`);
  },
  getCapacityTable: (filters?: CapacityTableFilters) => {
    const qs = new URLSearchParams();
    if (filters?.model) qs.set("model", filters.model);
    if (filters?.metric) qs.set("metric", filters.metric);
    if (filters?.device_group) qs.set("device_group", filters.device_group);
    if (filters?.template_stack) qs.set("template_stack", filters.template_stack);
    if (filters?.pct_min != null) qs.set("pct_min", String(filters.pct_min));
    if (filters?.pct_max != null) qs.set("pct_max", String(filters.pct_max));
    if (filters?.limit) qs.set("limit", String(filters.limit));
    if (filters?.offset != null) qs.set("offset", String(filters.offset));
    const suffix = qs.toString();
    return j<CapacityTableResponse>(`/capacity/table${suffix ? "?" + suffix : ""}`);
  },
  getCapacityTrend: (deviceId: number, metric: string, days = 30) =>
    j<CapacityTrend>(`/capacity/trend/${deviceId}/${metric}?days=${days}`),

  // Summary endpoints for the home Dashboard frames.
  getJobsSummary: () => j<JobsSummary>("/upgrade/jobs/summary"),
  getVersionDistribution: () =>
    j<VersionDistributionRow[]>("/devices/version-distribution"),

  // Alerts (phase 12)
  listAlerts: (filters?: AlertListFilters) => {
    const qs = new URLSearchParams();
    if (filters?.state) qs.set("state", filters.state);
    if (filters?.severity) qs.set("severity", filters.severity);
    if (filters?.device_id != null) qs.set("device_id", String(filters.device_id));
    if (filters?.limit) qs.set("limit", String(filters.limit));
    const suffix = qs.toString();
    return j<AlertRead[]>(`/alerts${suffix ? "?" + suffix : ""}`);
  },
  getAlertsSummary: () => j<AlertsSummary>("/alerts/summary"),
  acknowledgeAlert: (alertId: number) =>
    j<AlertRead>(`/alerts/${alertId}/acknowledge`, { method: "POST" }),
  unacknowledgeAlert: (alertId: number) =>
    j<AlertRead>(`/alerts/${alertId}/unacknowledge`, { method: "POST" }),
  listAlertRules: () => j<AlertRuleRead[]>("/alerts/rules"),
  createAlertRule: (body: AlertRuleCreate) =>
    j<AlertRuleRead>("/alerts/rules", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateAlertRule: (ruleId: number, body: AlertRuleUpdate) =>
    j<AlertRuleRead>(`/alerts/rules/${ruleId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteAlertRule: (ruleId: number) =>
    j<void>(`/alerts/rules/${ruleId}`, { method: "DELETE" }),
};
