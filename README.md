# pan-capacity-analyzer

Polls firewalls on a schedule, correlates current usage with each device's configured maximum, and persists time-series so you can see trends and forecast when something will hit the wall.

[![CI](https://github.com/gitupcourt/pan-capacity-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/gitupcourt/pan-capacity-analyzer/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why

Quick view of resources to determine if NGFW is nearing functional capacity from either a configuration or performance perspective.

## What it tracks

- **Configuration:** ARP table, GP Clientless VPN, IKE Peers, VPN Tunnels, Address Objects/Groups, FQDN Addresses, Service Objects/Groups, NAT Policies, Security Policies, virtual systems.
- **System:** Dataplane CPU, Management Plane CPU, MP Memory.
- **Traffic:** Concurrent Decryption Sessions, Session Table Utilization.

Each metric pairs a "current value" fetch with a "max capacity" fetch from the device itself (`cfg.general.max-*`), so the answer is always correct per platform and PAN-OS version. The metric catalog (`catalog/metrics.yaml`) is versioned and extensible.

## Status

Early scaffold. See [ROADMAP.md](ROADMAP.md) for deferred work.

## Stack

- Python 3.11 + FastAPI (backend / API)
- SQLite by default for portability (storage layer is swappable — Postgres/TimescaleDB later)
- React + Recharts (frontend)
- Docker Compose for the whole thing

## Quickstart

```bash
cp .env.example .env
# edit .env: set FERNET_KEY (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
docker compose up --build
```

## Container security

Both images run as a non-root user (UID `10001`) and the frontend uses `nginxinc/nginx-unprivileged` (listens on `:8080`, not `:80`). This matches Kubernetes PodSecurityStandards `restricted` and is generally considered best practice.

**For the supplied `docker-compose.yml` you don't need to do anything special** — it uses named Docker volumes for state (`capacity-data`, `frontend-node-modules`) so no host bind-mount UID dance is needed.

**If you write your own `docker run` or `docker-compose.yml` and bind-mount a host directory** for `/app/data` (where the SQLite database lives), make sure the host directory is writable by UID 10001:

```bash
mkdir -p ./data
sudo chown 10001:10001 ./data
```

Otherwise the backend container will fail to create or write the database file on startup.

**Ports inside the containers:**

| Component | Port |
|---|---|
| Backend API | `8000` |
| Frontend (prod nginx) | `8080` |
| Frontend (dev Vite) | `5173` |

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for Kubernetes, from-source, and configuration details, and [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for how the pieces fit together.
