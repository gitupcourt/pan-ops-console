# Roadmap

Deferred work — factored into the architecture so it can be added without painful rewrites.

> Several earlier roadmap items have since shipped and moved out of this list: capacity **forecasting** (least-squares "days until max" projection) and **threshold + sustained-breach alerting**; the extraction of auth / device / Panorama management into a shared **`core/`** module; and the move from SQLite to **PostgreSQL on Celery + Redis**. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the current shape.

## Tiered rollups / downsampling

The `samples` table stores raw poll data. For long-range history at fleet scale, downsample into `samples_hourly` / `samples_daily` tables and serve the trend/table views from rollups. The schema is intentionally narrow (device, metric, ts, current, max, pct) so a rollup job is additive. Predicted-date is computed on demand today; the same rollups would let the table precompute it instead of per-request.

## Per-DP / per-slot capacity breakdown

Dataplane CPU rolls every data processor into one average plus a hottest-core figure (`dp_cpu` / `dp_cpu_max`), which the catalog wildcards across all DPs. Multi-DP chassis (e.g. PA-7000) could expose a per-DP — and per-slot — breakdown as distinct metrics, once the XML structure is validated against real chassis hardware.

## Completing the upgrade-module merge

The upgrade orchestrator, prechecks, snapshots, and the job/precheck HTTP + UI surface have landed. Remaining: reconciling the enriched Panorama-sync path, and unifying the chrome (single login, one user/role admin, module-tabbed navigation shared across capacity and upgrade).

## Remote collectors for segmented networks

For environments where one cluster can't reach every firewall, run capacity workers close to the firewalls and ship samples to the central API. The command-proxy / Celery split already separates *how* a device is reached from *where* the worker runs, so this is a deployment-topology addition rather than a rewrite.
