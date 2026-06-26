# Contributing

This is a small project, so the bar is low: open an issue or a PR. The codebase tries to keep things in obvious places — backend in `backend/app`, frontend in `frontend/src`, metric catalog in `catalog/metrics.yaml`.

## Local dev

```bash
git clone https://github.com/gitupcourt/pan-ops-console.git
cd pan-ops-console/deploy/compose
cp .env.example .env          # set POSTGRES_PASSWORD
python -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())" >> .env
docker compose up --build
```

This stack runs Postgres, Redis, the API, a combined Celery worker, and the SPA — the API alone does nothing, since polling/alerts/upgrades run on Celery. For a tight inner loop (HMR on the frontend, `uvicorn --reload` on the backend), see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).

## Adding a metric

Most of the time you don't need to touch code — just edit [`catalog/metrics.yaml`](catalog/metrics.yaml). Each entry is a `(current, max)` pair of fetchers; supported extractor types are documented at the top of the file.

If your metric needs a value transformation that isn't supported (e.g. you need to divide one xpath match by another), extend the `Extractor` class in `backend/app/capacity/services/catalog.py`. Keep new extractor types opt-in via a `type:` discriminator so existing entries don't break.

## Verifying a metric against a real firewall

The poller's commands can be hand-tested via the backend container. This uses the same proxy-first / direct-fallback client the poller does, so it works whether the device is reached through Panorama or directly:

```bash
docker compose exec backend python -c "
# register all mappers so a bare Device query doesn't trip the relationship resolver
import app.core.panorama.models.panorama, app.core.devices.models.device  # noqa
from sqlalchemy.orm import configure_mappers; configure_mappers()
from app.db import SessionLocal
from app.core.command_proxy.builder import build_client_with_fallback
from app.core.devices.models.device import Device
from xml.etree import ElementTree as ET

with SessionLocal() as db:
    d = db.query(Device).first()
    client, route = build_client_with_fallback(db, d)
    r = client.op_xml('<show><system><info></info></system></show>')
    print('route:', route)
    print(ET.tostring(r, encoding='unicode')[:800])
"
```

Swap the command and use that to iterate on a new metric's XML / XPath.

## CI

Every push to `main` triggers a GitHub Actions workflow that builds both images and pushes them to GHCR. See [`.github/workflows/ci.yml`](.github/workflows/ci.yml). The workflow itself delegates to a reusable workflow in [`gitupcourt/.github`](https://github.com/gitupcourt/.github) — if you fork this repo for your own use, either keep that reference, fork that too, or write your own simpler CI.

## Style

- Python: ruff-friendly. No formatter pinned yet; new code should look like the existing code (PEP 8, type hints, dataclasses for typed value objects)
- TypeScript: strict mode on, the existing components show the pattern (React Query for data, Tailwind classes inline, no CSS modules)
- Commit messages: imperative subject line, then a body that explains *why* if it's non-obvious
