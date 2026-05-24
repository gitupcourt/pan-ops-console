# pan-ops-console

An operations console for Palo Alto Networks NGFWs. Capacity tracking is the first module; upgrade orchestration is being merged in next. Shared core for auth, device inventory, Panorama registry, and proxy-first / direct-fallback command routing.

[![CI](https://github.com/gitupcourt/pan-ops-console/actions/workflows/ci.yml/badge.svg)](https://github.com/gitupcourt/pan-ops-console/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> Renamed from `pan-capacity-analyzer` on 2026-05-24 as part of a scope expansion into a broader NGFW ops console. Capacity tracking remains a permanent first-class module — the rename signals scope expansion, not a pivot.

## Modules

- **Core / platform**
  - Local auth + OIDC + TOTP, invite-only user model, DB-backed sessions
  - Device inventory with at-rest-encrypted credentials
  - Panorama registry with reachability tracking
  - Command-proxy layer with proxy-first / direct-fallback semantics
- **Capacity** — scheduled polling, time-series persistence, charts. Tracks configuration counts (address objects, security policies, IKE peers, etc.), system pressure (dataplane CPU, MP memory), and traffic (decryption sessions, session table). Every metric pairs a "current value" fetch with a "max capacity" fetch from the device itself so the answer is correct per platform and PAN-OS version. Metric catalog (`catalog/metrics.yaml`) is versioned and extensible.
- **Upgrade** *(incoming)* — bulk firewall upgrade orchestration with HA-pair-aware phasing, pre/post-check classifier, snapshot capture, and phase-completion-tracked retries.

## Status

Active development. The capacity module is in use; the upgrade module merge is underway. See [ROADMAP.md](ROADMAP.md) for direction and deferred work.

## Layout

```
backend/app/
├── core/
│   ├── auth/          users, sessions, OIDC, TOTP
│   ├── devices/       device inventory + credentials
│   ├── panorama/      Panorama registry + sync
│   ├── command_proxy/ pan-os client builder (proxy-first, direct-fallback)
│   ├── credentials.py shared Fernet-encrypted credential helpers
│   ├── crypto.py      Fernet wrappers
│   └── schema_utils.py
├── capacity/          polling, catalog, time-series storage, metrics API
└── main.py, config.py, db.py

frontend/src/
├── core/{auth,devices,ui}/
├── capacity/
├── api.ts, App.tsx, main.tsx
```

PRs that touch only one module don't affect the others. PRs that touch `core/` consciously affect every module — that's the intended property.

## Stack

- Python 3.11 + FastAPI (backend / API)
- SQLite for portability today; Postgres planned for the merged app
- React + Vite + Recharts + TanStack Query (frontend)
- Docker Compose for the all-in-one local dev path

## Quickstart

```bash
cp .env.example .env
# edit .env: set FERNET_KEY (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
docker compose up --build
```

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for Kubernetes, from-source, and configuration details, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the pieces fit together.
