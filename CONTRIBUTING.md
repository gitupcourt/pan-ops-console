# Contributing

Open an issue or a PR. The codebase tries to keep things in obvious places — backend in `backend/app`, frontend in `frontend/src`, Alembic migrations in `backend/alembic/versions`.

## Local dev

```bash
git clone https://github.com/gitupcourt/pan-fw-upgrader.git
cd pan-fw-upgrader
cp .env.example .env
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())" >> .env
# Edit .env to remove the placeholder SECRET_KEY / FERNET_KEY lines.
docker compose up --build
```

The whole stack runs in Docker Compose with hot reload:

- Backend changes → uvicorn reloads (`./backend` is bind-mounted)
- Frontend changes → Vite HMR (`./frontend` is bind-mounted)
- Worker changes → `docker compose restart worker`

Run a one-off in the backend container:

```bash
docker compose exec backend bash
```

## Branching and PRs

Work on a feature branch and open a PR against `main`, even for small changes — it gives CI a chance to run and keeps the history reviewable. Direct pushes to `main` are discouraged.

Commit subjects are imperative ("add HA-pair grouping", not "added"). If the *why* is non-obvious, put it in the body.

## Tests

Backend tests live under `backend/tests/` and use `pytest`. They cover the pre-check classifier rules and the orchestrator's pure helpers — the bits that decide whether to skip work, retry phases, or block on HA state.

First time only:

```bash
docker compose exec backend pip install -r requirements-dev.txt
```

Then:

```bash
docker compose exec backend pytest
```

Tests don't touch the real database (they use `SimpleNamespace` doubles for ORM objects), so they're fast and safe to run continuously.

Frontend type-check:

```bash
docker compose exec frontend npx tsc -b --noEmit
```

CI runs these same two commands; if they pass locally, CI will pass.

## Schema changes

Migrations are Alembic and run automatically on backend startup.

1. Edit the SQLAlchemy model (e.g. `backend/app/models/device.py`).
2. Generate a migration:

   ```bash
   docker compose exec backend alembic revision --autogenerate -m "describe the change"
   ```

3. **Read the generated migration.** `--autogenerate` is excellent but not perfect — it can miss enum value renames, server-default changes, or index subtleties.
4. Apply it: `docker compose restart backend` (or `docker compose exec backend alembic upgrade head`).
5. Commit the migration alongside the model change.

To roll back one step locally: `docker compose exec backend alembic downgrade -1`.

## Adding a pre-check rule

The pre-check classifier wraps `pan-os-upgrade-assurance`'s binary results in a four-state severity (pass / warn / fail / skip). Add a rule by editing `_RULES` in `backend/app/services/precheck_classifier.py`. The check list itself comes from upstream's `CheckType` enum and is exposed at `GET /api/devices/precheck/available`.

## Troubleshooting a device that won't pre-check

If the app says a device is unreachable but you can hit its UI from a browser on the same machine, the worker container is seeing a different source IP than your desktop. Three places to look:

1. Open the device row on the **Devices** page and click **Run test** in the *Connectivity test* panel. The four steps (DNS → TCP → TLS → API) localize the failure.
2. Tail the worker / backend logs while you reproduce:

   ```bash
   docker compose logs -f --tail=200 worker backend
   ```

3. Check the firewall's **management interface profile** for a permitted-IPs ACL that excludes the Docker host's IP. The worker's outbound traffic is NAT'd through the Docker host.

**Docker Desktop subnet collision.** If a firewall's management IP lands inside one of Docker's reserved subnets (most commonly `192.168.65.0/24` for Docker Desktop's vpnkit on Windows/Mac, or the `172.17–172.31.0.0/16` bridge ranges), the container can't reach it — Docker captures the packet before it leaves the host. The connectivity-test panel detects this and flags the DNS step. Fix by changing either the firewall's mgmt IP or Docker's reserved subnets.

## Style

- **Python:** PEP 8, type hints, ruff-friendly. New code should look like the existing code.
- **TypeScript:** strict mode on. TanStack Query for server state, Tailwind classes inline, no CSS modules.
- **Commit messages:** imperative subject, body explains *why* if non-obvious.

## CI

Every push and PR runs `.github/workflows/ci.yml` (backend pytest + frontend typecheck). Push-to-main also builds and pushes both Docker images to GHCR via a reusable workflow in [`gitupcourt/.github`](https://github.com/gitupcourt/.github). If you fork this repo for your own use, either keep that reference, fork that too, or write simpler CI of your own.
