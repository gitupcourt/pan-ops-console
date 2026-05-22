# Architecture

A single backend container and a single frontend container, with one volume between them. The whole thing is designed to run on a Raspberry Pi or a beefy server with no changes.

```
                       ┌─────────────────────────┐
                       │  Browser                │
                       │  http(s)://capacity.../ │
                       └────────────┬────────────┘
                                    │
                                    ▼
                       ┌─────────────────────────┐
                       │  Frontend container     │
                       │  nginx serving SPA      │
                       │  (port 80)              │
                       └────────────┬────────────┘
                          ┌─────────┴──────────┐
                          │ proxy: /api/* →    │
                          │ backend:8000       │
                          └─────────┬──────────┘
                                    ▼
   ┌────────────────────────────────────────────────────────────┐
   │  Backend container                                         │
   │  - FastAPI HTTP API                  (port 8000)           │
   │  - APScheduler poller loop                                 │
   │  - pan-os-python client                                    │
   │  ┌──────────────────────────────────────────────────────┐  │
   │  │  SQLite file at /app/data/capacity.db                │  │
   │  │  (mounted from PVC / named volume / bind mount)      │  │
   │  └──────────────────────────────────────────────────────┘  │
   │  ┌──────────────────────────────────────────────────────┐  │
   │  │  metrics.yaml at /app/catalog/                       │  │
   │  │  (mounted from ConfigMap or bind mount)              │  │
   │  └──────────────────────────────────────────────────────┘  │
   └─────────────────────────┬──────────────────────────────────┘
                             │
                             ▼ XML API over HTTPS
            ┌────────────────────────────────┐
            │  Firewalls / Panorama          │
            └────────────────────────────────┘
```

## Components

### Backend

Python 3.11 + FastAPI + SQLAlchemy + APScheduler + pan-os-python.

- **HTTP API** under `/devices`, `/panoramas`, `/metrics` — CRUD for the inventory, time-series reads, manual poll trigger
- **APScheduler background job** fires on `POLL_INTERVAL_SECONDS` (default 300). Walks every enabled device, runs every metric in the catalog, writes samples to SQLite
- **Storage layer** behind a `SampleStore` interface. Default SQLAlchemy implementation writes SQLite. Future Postgres/TimescaleDB implementation is a drop-in
- **Encryption** — Fernet-symmetric. Firewall API keys (and Panorama keys) are encrypted at rest. The key lives in the `FERNET_KEY` env var

### Frontend

React + Vite + Recharts + TanStack Query.

- **Dashboard** — per-device charts grouped by category (config / system / traffic). Each chart shows the current value, max capacity reference line, and the % utilization
- **Inventory** — add/edit/delete Panoramas and devices. Auth modes per device: mint-from-userpass (recommended), paste-API-key, or proxy-through-Panorama
- **Capacity viewer** — per-device button that pulls every `cfg.general.max-*` key the device reports, in one table

In production, the frontend is a static nginx-served bundle (`frontend/Dockerfile.prod`). The dev variant runs the vite dev server with HMR (`frontend/Dockerfile`).

### Storage

| Layer | Default | Swap target |
|---|---|---|
| Sample time-series | SQLite | TimescaleDB |
| Encrypted credentials | SQLite | same |
| Metric catalog | YAML file on disk | YAML file in ConfigMap |

SQLite was picked for portability — one container, one volume, no operator burden. The schema (`device_id, metric, ts, current, max, pct`) is deliberately narrow so the Postgres swap is a drop-in implementation of `SampleStore`.

### Poller

The interesting part. Each metric in `catalog/metrics.yaml` is a `(current, max)` pair of fetchers:

```yaml
- name: address_objects
  category: config
  description: Address objects
  current:
    sources:
      - cmd: "<show><config><pushed-shared-policy/></config></show>"
        extract: { type: xpath_count, xpath: ".//address/entry" }
      - cmd: "<show><config><running/></config></show>"
        extract: { type: xpath_count, xpath: ".//vsys/entry/address/entry" }
  max:
    cmd: "<show><system><state><filter>cfg.general.max-address</filter></state></system></show>"
    extract: { type: state_value, key: cfg.general.max-address }
```

Multiple `sources` are summed — so address-object counts combine Panorama-pushed objects and locally-configured objects automatically. The poller caches op() responses across metrics that share a command, so 17 metrics don't mean 17 round-trips per device per poll.

Extractor types: `xpath_count`, `xpath_text`, `xpath_avg`, `state_value`, `text_regex` (with optional `invert` for "100 - value" cases like CPU idle). Adding a new metric usually means editing YAML, not code.

## What you need vs what you don't

**Required:**
- A `FERNET_KEY` env var
- Network reachability from the backend container to at least one firewall or Panorama
- Persistent storage if you want data to survive container restarts (`docker volume` for compose; `PVC` for k8s)

**Not required:**
- An external database
- A message queue / Redis
- An auth provider (the app has no built-in user auth — protect it with your reverse proxy if exposing it publicly)
- Anything else

## Reverse proxy / TLS

Whatever you have works:

- **Local lab:** docker-compose exposes the frontend on port 5174, no TLS, fine
- **Production:** put it behind anything — Traefik, nginx, Caddy, an existing ingress controller. The frontend serves the SPA at `/` and proxies `/api/*` to the backend. The example k8s manifests in [`deploy/kubernetes/`](../deploy/kubernetes/) use Traefik with a `stripPrefix` middleware on `/api`

The frontend's nginx config inside the container does the `/api/*` proxying internally when in production. In compose-with-build-from-source dev mode, the Vite dev server does this via its proxy config.

## Scaling

**Don't** — at least not the backend, and not without swapping storage. SQLite isn't safe for multiple writers, so the backend Deployment in the k8s manifests is `replicas: 1` with `strategy: Recreate`. The frontend is stateless and can scale freely.

When you hit hundreds of firewalls and the sequential poll cycle starts taking minutes, the right next step is:

1. Swap storage to Postgres/TimescaleDB (the `SampleStore` interface is the seam)
2. Parallelize poll calls across devices (one worker per device or a small pool)
3. Optionally split into "central API" and "remote collectors" so one host doesn't have to reach every firewall — see roadmap

Forecasting (linear regression "days until capacity") and tiered rollups (raw 30d, hourly 1y, daily forever) are also on the roadmap and slot in cleanly because the storage interface is narrow.
