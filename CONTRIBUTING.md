# Contributing

This is a small project, so the bar is low: open an issue or a PR. The codebase tries to keep things in obvious places — backend in `backend/app`, frontend in `frontend/src`, metric catalog in `catalog/metrics.yaml`.

## Local dev

```bash
git clone https://github.com/gitupcourt/pan-capacity-analyzer.git
cd pan-capacity-analyzer
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())" >> .env
docker compose up --build
```

For a tight inner loop (HMR on the frontend, `uvicorn --reload` on the backend), see [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — section "3. From source".

## Adding a metric

Most of the time you don't need to touch code — just edit [`catalog/metrics.yaml`](catalog/metrics.yaml). Each entry is a `(current, max)` pair of fetchers; supported extractor types are documented at the top of the file.

If your metric needs a value transformation that isn't supported (e.g. you need to divide one xpath match by another), extend the `Extractor` class in `backend/app/services/catalog.py`. Keep new extractor types opt-in via a `type:` discriminator so existing entries don't break.

## Verifying a metric against a real firewall

The poller's commands can be hand-tested via the backend container:

```bash
docker compose exec backend python -c "
from app.db import SessionLocal
from app.models import Device
from app.services.auth import decrypt_key
from app.services.pan_client import PanDeviceClient
from xml.etree import ElementTree as ET

db = SessionLocal()
d = db.query(Device).first()
key = decrypt_key(d.encrypted_api_key)
client = PanDeviceClient.direct(d.ip_address or d.hostname, key, verify_tls=d.verify_tls)
r = client.op_xml('<show><system><info></info></system></show>')
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
