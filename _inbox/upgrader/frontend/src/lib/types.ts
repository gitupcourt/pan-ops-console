// Shared types used across the Devices page, its components, and elsewhere.
// Kept here (and not co-located with components) because multiple components
// reference the same Device shape; one source of truth avoids drift.

export type Severity = "pass" | "warn" | "fail" | "skip";

export type PreCheckSummary = {
  id: number;
  ran_at: string;
  overall_severity: Severity;
  pass_count: number;
  warn_count: number;
  fail_count: number;
  skip_count: number;
};

export type Device = {
  id: number;
  name: string;
  hostname: string;
  ip_address: string | null;
  serial: string | null;
  source: string;
  ha_role: string;
  ha_state: string | null;
  ha_sync_state: string | null;
  ha_peer_id: number | null;
  current_version: string | null;
  model: string | null;
  device_group: string | null;
  template_stack: string | null;
  connected: boolean;
  uptime: string | null;
  app_version: string | null;
  threat_version: string | null;
  av_version: string | null;
  wildfire_version: string | null;
  url_filtering_version: string | null;
  gp_client_version: string | null;
  last_seen_at: string | null;
  last_refresh_at: string | null;
  verify_tls: boolean;
  proxy_via_panorama: boolean;
  credential_id: number | null;
  latest_precheck: PreCheckSummary | null;
  staged_version: string | null;
  staged_at: string | null;
  staged_error: string | null;
  downloaded_versions: string[] | null;
};

export type Panorama = {
  id: number;
  name: string;
  hostname?: string;
  reachable?: boolean;
  last_reachability_at?: string | null;
  last_reachability_error?: string | null;
};

export type Credential = {
  id: number;
  name: string;
  scope: string;
  auth_type: string;
};

export type ClassifiedCheck = {
  raw_state: boolean;
  raw_reason: string;
  severity: Severity;
  reason: string;
};

export type PreCheckResult = {
  id: number | null;
  device_id: number;
  ran_at: string;
  overall_severity: Severity;
  pass_count: number;
  warn_count: number;
  fail_count: number;
  skip_count: number;
  results: Record<string, ClassifiedCheck>;
  error: string | null;
};

export type BulkSummary = {
  bulk_run_id: number;
  started_at: string;
  finished_at: string | null;
  target_count: number;
  completed_count: number;
  pending_count: number;
  pass_count: number;
  warn_count: number;
  fail_count: number;
  error_count: number;
  cancelled: boolean;
  results: Record<number, PreCheckResult>;
};

export type BulkStageRunOut = {
  id: number;
  device_id: number;
  version: string;
  started_at: string;
  finished_at: string | null;
  outcome: Severity;
  error: string | null;
};

export type BulkStageSummary = {
  bulk_run_id: number;
  started_at: string;
  finished_at: string | null;
  target_count: number;
  completed_count: number;
  pending_count: number;
  success_count: number;
  failure_count: number;
  cancelled: boolean;
  version: string;
  results: Record<number, BulkStageRunOut>;
};

export type SoftwareEntry = {
  version: string;
  downloaded: boolean;
  current: boolean;
  latest: boolean;
  uploaded: boolean;
  filename: string | null;
  released_on: string | null;
  size_kb: string | null;
};

export type AvailableSoftware = {
  device_id: number;
  current_version: string | null;
  available: SoftwareEntry[];
  error: string | null;
};

// ---- Disk space ----
export type DiskSpaceRow = {
  filesystem: string;
  size: string;
  used: string;
  avail: string;
  use_pct: string;       // numeric string, no "%" suffix
  mounted_on: string;
};

// ---- Active upgrade tasks ----
// One row from GET /api/jobs/active-tasks. Drives the upgrade-in-progress
// badge on each Device row + the Dashboard "in flight" tile.
export type ActiveTask = {
  device_id: number;
  task_id: number;
  job_id: number;
  job_name: string;
  target_version: string;
  phase: string;
  download_progress: number | null;
  install_progress: number | null;
  awaiting: boolean;
};

// ---- Snapshots / diffs ----
// Match the SnapshotKind enum on the backend.
export type SnapshotKind = "pre_upgrade" | "post_upgrade" | "ad_hoc";

export type SnapshotSummary = {
  id: number;
  device_id: number;
  task_id: number | null;
  kind: SnapshotKind;
  taken_at: string;
  pan_os_version: string | null;
  error: string | null;
  areas: string[];
};

export type Snapshot = SnapshotSummary & { data: Record<string, unknown> };

// Per-area entry inside the SnapshotCompare report. The library returns
// per-comparison-type shapes; we type-loosely so any future additions don't
// break the UI.
export type SnapshotAreaReport = {
  passed?: boolean;
  added?: { passed?: boolean; added_keys?: string[] };
  missing?: { passed?: boolean; missing_keys?: string[] };
  changed?: { passed?: boolean; changed_raw?: Record<string, unknown> };
  count_change_percentage?: { passed?: boolean; change_percentage?: number };
} & Record<string, unknown>;

export type SnapshotDiff = {
  id: number;
  left_snapshot_id: number;
  right_snapshot_id: number;
  task_id: number | null;
  computed_at: string;
  all_passed: boolean;
  failing_areas: string | null;
  report: Record<string, SnapshotAreaReport>;
  left: SnapshotSummary;
  right: SnapshotSummary;
};

// Small shared className used by every form input across the device modals.
export const INPUT_CLS =
  "w-full rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm";
