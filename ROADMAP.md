# Roadmap

Deferred work — intentionally out of scope for the initial build but factored in to the architecture so it can be added without painful rewrites.

## Remote collector mode

Today the collector and the API run in the same process and write to the same SQLite file. For segmented networks where one host can't reach every firewall, we need an agent-per-site model: lightweight collectors deployed near the firewalls, shipping samples to a central API over HTTPS.

**How we're set up for this now:** the poller talks to storage through a narrow interface (`SampleStore`). The local implementation writes SQLite directly; a future remote-collector implementation will POST samples to the central API. Same interface, different transport.

## Storage backend swap (TimescaleDB / Postgres)

SQLite is the default for portability. For multi-tenant deployments or hundreds of devices, swap the storage backend to TimescaleDB for native retention policies and continuous aggregates.

**How we're set up for this now:** the `SampleStore` interface knows nothing about SQLite. The schema is intentionally narrow (device_id, metric, timestamp, current, max, pct). A `TimescaleSampleStore` is a drop-in replacement.

## Merge with pan-fw-upgrader

Long-term, this app and [pan-fw-upgrader](https://github.com/gitupcourt/pan-fw-upgrader) become tabs in the same UI — same auth, same device inventory, same Panorama integration, two different read/write personalities.

**How we're set up for this now:** the auth, credential, device, and Panorama models / services are deliberately mirrored from the upgrade UI. When we merge, those modules collapse together with minimal churn. The capacity-specific pieces (catalog, samples, poller) are cleanly separated and bolt onto the merged app as a new feature area.

## Forecasting

Once enough samples accumulate, add linear-regression-based "days until capacity" estimates and alert thresholds. Defer until the data pipeline is solid.

## Tiered rollups

Schema supports it from day one (samples table covers raw 5-min data; future `samples_hourly` and `samples_daily` tables for downsampled history). Rollup job itself comes after the basic poller is running cleanly.
