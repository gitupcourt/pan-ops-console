# pan-ops-console

An operations console for Palo Alto Networks NGFWs. Capacity tracking and PAN-OS upgrade orchestration run as modules on a shared core for auth, device inventory, Panorama registry, and proxy-first / direct-fallback command routing.

[![CI](https://github.com/gitupcourt/pan-ops-console/actions/workflows/ci.yml/badge.svg)](https://github.com/gitupcourt/pan-ops-console/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Renamed from `pan-capacity-analyzer` on 2026-05-24 as part of a scope expansion into a broader NGFW ops console. Capacity tracking remains a permanent first-class module — the rename signals scope expansion, not a pivot.

## Modules

- **Core / platform**
  - Local auth + OIDC + TOTP, invite-only user model, DB-backed sessions
  - Device inventory with at-rest-encrypted credentials
  - Panorama registry with reachability tracking
  - Command-proxy layer with proxy-first / direct-fallback semantics
- **Capacity** — scheduled polling, time-series persistence, heat-map / table / trend views, and threshold + sustained-breach alerting. Tracks configuration counts (address objects, security policies, IKE peers, …), system pressure (dataplane + management CPU, MP memory), and traffic (decryption sessions, session table). Every metric pairs a "current value" fetch with a "max capacity" fetch from the device itself so the answer is correct per platform and PAN-OS version. The metric catalog (`catalog/metrics.yaml`) is versioned and extensible — adding a metric is usually a YAML edit, not code.
- **Upgrade** — bulk PAN-OS upgrade orchestration: HA-pair-aware phasing, a pre/post-check classifier, snapshot capture + diff, and a non-blocking state machine that survives worker restarts. *(Merging in PR-by-PR; the orchestrator, prechecks, snapshots, and the job/precheck HTTP+UI surface have landed.)*

## Status

Active development. Capacity (with alerting) is in production use; the upgrade module is landed and being hardened. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the pieces fit and [ROADMAP.md](ROADMAP.md) for deferred work.

## Stack

- **Backend** — Python 3.11, FastAPI, SQLAlchemy 2.0, Pydantic v2
- **Async** — Celery (workers + beat) over Redis; polling, alert evaluation, and upgrades all run off the API process
- **Storage** — PostgreSQL (system of record), Alembic migrations. SQLite is used only by the test suite.
- **Frontend** — React 18 + TypeScript, Vite, @tanstack/react-query 5, Tailwind, Recharts
- **Device I/O** — pan-os-python (XML API), panos-upgrade-assurance (readiness checks, snapshots)

> **No single-container / SQLite deployment mode.** Capacity polling, alert evaluation, and upgrade orchestration all run on Celery, so **Redis plus at least one worker are required** — the API alone will start but do nothing.

## Quickstart (Docker Compose)

A working stack — Postgres, Redis, the API, a combined worker, and the SPA — lives in [`deploy/compose/`](deploy/compose/):

```bash
git clone https://github.com/gitupcourt/pan-ops-console.git
cd pan-ops-console/deploy/compose
cp .env.example .env          # set POSTGRES_PASSWORD and FERNET_KEY
docker compose up --build
```

Open <http://localhost:8080> and create the first admin when prompted (first-run bootstrap), then add a Panorama or firewall under **Inventory**. See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for the Kubernetes (production) path, from-source dev, and the configuration reference.

> **Generate a `FERNET_KEY` and back it up.** It encrypts every stored firewall/Panorama credential; regenerating it on an existing install makes them unrecoverable.
> ```bash
> python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
> ```

## Layout

```
backend/
├── app/
│   ├── core/        shared platform: auth, devices, panorama, command_proxy, crypto
│   ├── capacity/    polling, catalog, time-series, aggregates
│   ├── alerts/      threshold + sustained-breach alert rules and evaluation
│   ├── upgrade/     HA-aware upgrade orchestrator + prechecks/snapshots
│   ├── workers/     Celery app, beat schedule, queue routing
│   └── main.py, config.py, db.py
└── alembic/         migrations (run on API startup)

frontend/src/
├── core/{auth,devices,ui}/
├── capacity/  alerts/  upgrade/
└── api.ts, App.tsx, main.tsx
```

A feature module (`capacity/`, `alerts/`, `upgrade/`) may import from `core/*`, but `core/*` never imports a feature module — so a PR scoped to one module doesn't disturb the others, while a `core/` change consciously affects all of them. That's the intended property.

## Container security

Both images run as a non-root user (backend UID `10001`; the frontend uses `nginxinc/nginx-unprivileged`, listening on `:8080`, not `:80`). This matches Kubernetes PodSecurityStandards `restricted` and is generally considered best practice. Application state lives in PostgreSQL (its own volume / managed service), so the app containers need no host bind-mount UID dance.

**Ports inside the containers:**

| Component | Port |
|---|---|
| Backend API | `8000` |
| Frontend (prod nginx) | `8080` |
| Frontend (dev Vite) | `5173` |

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for Kubernetes and configuration details, and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the pieces fit together.
