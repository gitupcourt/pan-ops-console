# pan-fw-upgrader

Web UI for bulk-upgrading Palo Alto Networks firewalls. HA-aware, Panorama-integrated, with pause/resume gates, pre/post snapshots, and a connectivity diagnostic for the inevitable "why can't the worker reach this device" moment. Built on top of [`pan-os-upgrade-assurance`](https://github.com/PaloAltoNetworks/pan-os-upgrade-assurance).

[![CI](https://github.com/gitupcourt/pan-fw-upgrader/actions/workflows/ci.yml/badge.svg)](https://github.com/gitupcourt/pan-fw-upgrader/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Why

Upgrading firewalls one-at-a-time through the GUI doesn't scale past a handful of devices, and the existing tooling either skips the HA dance or requires a CLI ceremony per pair. This puts the same orchestration behind a web UI: bulk select devices, run pre-checks, stage images, run the actual upgrade with HA-aware gating, capture snapshots before/after, and resume from the last incomplete phase if anything trips.

## What it does

- **HA-aware bulk upgrades.** Backup → confirm → primary, never both halves down.
- **Pre-check / snapshot / upgrade / post-check / diff** workflow with persisted history.
- **Pre-staging** with version picker, train grouping, and already-downloaded detection.
- **Direct-to-device or Panorama-proxied** connections per device, with automatic direct-fallback when Panorama is unreachable.
- **Phase-completion markers** so Retry truly resumes from where it stopped, not from scratch.
- **Per-device disk-space self-help** — list and delete downloaded PAN-OS images from the UI.
- **Connectivity diagnostic** (DNS → TCP → TLS → API) to localize why a device looks unreachable.
- **Encrypted credential storage** (Fernet) for device and Panorama API credentials.
- **Local auth** (argon2 + JWT) with a pluggable seam for SAML/SSO.

See [`ROADMAP.md`](ROADMAP.md) for what's deferred.

## Stack

- Python 3.11 + FastAPI (API)
- Celery + Redis (job orchestration, durable, supports pause/resume)
- PostgreSQL 16 (state)
- React + Vite + Tailwind (frontend)
- Docker Compose for the whole thing

Python 3.11 specifically because `pan-os-python` still imports `distutils.version`, removed in 3.12.

## Quickstart

```bash
git clone https://github.com/gitupcourt/pan-fw-upgrader.git
cd pan-fw-upgrader
cp .env.example .env

# Generate the two secrets and append them to .env
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())" >> .env
# Then edit .env to remove the placeholder SECRET_KEY / FERNET_KEY lines.

docker compose up --build
```

## Container security

All published images run as a non-root user — the backend (api/worker/beat all share the image) as UID `10001`, and the frontend uses `nginxinc/nginx-unprivileged` (listens on `:8080`, runs as UID `101`). This matches Kubernetes PodSecurityStandards `restricted` and is generally considered best practice.

**The supplied `docker-compose.yml` works out of the box** — named volumes (`postgres-data`, `redis-data`, `images-data`) are auto-initialized with the correct ownership by Docker, and bind-mounted source code is read-only from the container's perspective (Python's `PYTHONDONTWRITEBYTECODE` keeps `__pycache__` writes from failing).

**If you bring up the stack and then deploy a newer hardened image on top of pre-existing volumes**, the `images-data` named volume created by an earlier (root-owned) run will be owned by root and the new non-root container won't be able to write PAN-OS images to it. Recreate the volume:

```bash
docker compose down -v   # destroys named volumes
docker compose up --build
```

(Postgres + Redis volumes are managed by their respective images at first boot and aren't affected.)

**If you write your own `docker run` with a bind-mounted host directory** for `/var/lib/panfw/images`, make sure the host directory is writable by UID 10001:

```bash
mkdir -p ./panfw-images
sudo chown 10001:10001 ./panfw-images
```

**Ports inside the containers:**

| Component | Port |
|---|---|
| Backend API | `8000` |
| Frontend (prod nginx) | `8080` |
| Frontend (dev Vite) | `5173` |
| Postgres | `5432` |
| Redis | `6379` |

Then open:

- **Frontend:** http://localhost:5173
- **API docs:** http://localhost:8000/docs

Default login: username `admin`, password from `INITIAL_ADMIN_PASSWORD` in `.env`. Change it immediately after first login.

See [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for Kubernetes, sealed-secrets, and configuration details, and [`docs/architecture.md`](docs/architecture.md) for how the pieces fit together.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the dev loop, how to add a pre-check rule, and how to verify orchestrator behavior against a real firewall.

## License

[MIT](LICENSE).
