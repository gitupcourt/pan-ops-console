// All API calls go through the Vite dev-server proxy at /api (see vite.config.ts).

const base = "/api";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${base}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!resp.ok) throw new Error(`${resp.status} ${resp.statusText}`);
  return resp.json() as Promise<T>;
}

export type Device = {
  id: number;
  name: string;
  hostname: string;
  ip_address: string | null;
  serial: string | null;
  model: string | null;
  sw_version: string | null;
  source: string;
  panorama_id: number | null;
  credential_id: number | null;
  verify_tls: boolean;
  proxy_via_panorama: boolean;
  polling_enabled: boolean;
  last_poll_at: string | null;
  last_poll_error: string | null;
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

export const api = {
  listDevices: () => j<Device[]>("/devices"),
  listCatalog: () => j<MetricSpec[]>("/metrics/catalog"),
  getSeries: (deviceId: number, metric: string, hours = 24) =>
    j<MetricSeries>(`/metrics/${deviceId}/${metric}?hours=${hours}`),
  pollNow: () => j<{ status: string }>("/metrics/poll/run-now", { method: "POST" }),
};
