# pan-capacity-analyzer

Polls firewalls on a schedule, correlates current usage with each device's configured maximum, and persists time-series so you can see trends and forecast when something will hit the wall.

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

- Python 3.12 + FastAPI (backend / API)
- SQLite by default for portability (storage layer is swappable — Postgres/TimescaleDB later)
- React + Recharts (frontend, coming)
- Docker Compose for the whole thing

## Quickstart

```bash
cp .env.example .env
# edit .env: set FERNET_KEY (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
docker compose up --build
```
