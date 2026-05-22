# Roadmap

Deferred work — intentionally out of scope for the initial build but factored in to the architecture so it can be added without painful rewrites.

## Remote collector mode

Today the collector and the API run in the same process and write to the same SQLite file. For segmented networks where one host can't reach every firewall, we need an agent-per-site model: lightweight collectors deployed near the firewalls, shipping samples to a central API over HTTPS.

**How we're set up for this now:** the poller talks to storage through a narrow interface (`SampleStore`). The local implementation writes SQLite directly; a future remote-collector implementation will POST samples to the central API. Same interface, different transport.

## Storage backend swap (TimescaleDB / Postgres)

SQLite is the default for portability. For multi-tenant deployments or hundreds of devices, swap the storage backend to TimescaleDB for native retention policies and continuous aggregates.

**How we're set up for this now:** the `SampleStore` interface knows nothing about SQLite. The schema is intentionally narrow (device_id, metric, timestamp, current, max, pct). A `TimescaleSampleStore` is a drop-in replacement.

## Extract the auth / inventory module

The Panorama + device + credential management surface (mint API key from user/pass, encrypted-at-rest storage, Panorama device import with selection, direct vs proxy polling) is deliberately written so it could be extracted as a standalone module and reused by other PAN-OS-facing apps. Today it lives in `backend/app/{models,services}/` and `frontend/src/pages/Inventory.tsx`; promoting it to its own package is the next step before that reuse happens.

## Forecasting

Once enough samples accumulate, add linear-regression-based "days until capacity" estimates and alert thresholds. Defer until the data pipeline is solid.

## Tiered rollups

Schema supports it from day one (samples table covers raw 5-min data; future `samples_hourly` and `samples_daily` tables for downsampled history). Rollup job itself comes after the basic poller is running cleanly.
