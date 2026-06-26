# Deployment

pan-ops-console runs the same shape everywhere: a FastAPI **API**, **Celery**
workers + a **beat** scheduler over **Redis**, **PostgreSQL** as the system of
record, and the **SPA** served behind a web layer.

There is **no in-process poller and no single-container mode**. Capacity
polling, alert evaluation, and upgrade orchestration all run on Celery, so
**Redis plus at least one worker are mandatory** — the API alone will start but
do nothing.

> **Database: PostgreSQL, everywhere.** SQLite is used only by the test suite
> (it's how CI runs without a database service); it is **not** a deployment
> option. Several processes against one SQLite file serialize and throw
> "database is locked", so any real install uses Postgres.

Two supported paths:

- **[Docker Compose](#docker-compose-kick-the-tires)** — evaluate on a PC or single server.
- **[Kubernetes](#kubernetes-production)** — production.

---

## What it consists of

| Component | Role | Image |
|---|---|---|
| **api** | FastAPI HTTP API; runs `alembic upgrade head` on startup | backend |
| **worker** | Celery: capacity polling + Panorama sync (`celery` queue) | backend |
| **upgrade-worker** | Celery: upgrade orchestration (`upgrade` queue) | backend |
| **beat** | Celery scheduler (dispatch + sync ticks) | backend |
| **redis** | Celery broker + coordination locks | `redis:7` |
| **postgres** | system of record | `postgres:16` |
| **frontend** | static SPA | frontend |

The three Celery roles (worker / upgrade-worker / beat) are separate processes
of the **same backend image** — only the launch command differs. Compose
collapses them into one worker; Kubernetes splits them (see
[differences](#docker-vs-kubernetes)).

---

## Required configuration

| Env var | Required | Notes |
|---|---|---|
| `DATABASE_URL` | **yes** | `postgresql+psycopg://user:pass@host:5432/db` (the `+psycopg` selects psycopg3) |
| `FERNET_KEY` | **yes** | encrypts stored firewall/Panorama credentials at rest |
| `REDIS_URL` | **yes** | `redis://host:6379/0` |
| `SESSION_COOKIE_SECURE` | no | `true` (default) behind HTTPS; `false` for plain-HTTP local eval |
| `PUBLIC_BASE_URL` | OIDC only | external base URL, for OIDC redirect callbacks |
| `CATALOG_PATH` | no | metric catalog path; defaults to the image's bundled `metrics.yaml` |

**Generate a `FERNET_KEY`** (no dependencies):

```bash
openssl rand -base64 32 | tr '+/' '-_'
```

> **Never regenerate `FERNET_KEY` on an existing install** — it makes every
> stored credential, TOTP secret, and OIDC client secret unrecoverable. Back it up.

**Run the Celery roles** (same image, different command):

```bash
# capacity polling + Panorama sync
celery -A app.workers.celery_app:celery worker -Q celery
# upgrade orchestration (give it more RAM — snapshot + XML diff work)
celery -A app.workers.celery_app:celery worker -Q upgrade
# scheduler (exactly ONE of these, ever)
celery -A app.workers.celery_app:celery beat
```

---

## Docker Compose (kick-the-tires)

A working stack lives in [`deploy/compose/`](../deploy/compose/): Postgres,
Redis, the API, **one** combined worker (all queues + an embedded beat), and the
SPA.

```bash
git clone https://github.com/gitupcourt/pan-ops-console.git
cd pan-ops-console/deploy/compose
cp .env.example .env          # set POSTGRES_PASSWORD and FERNET_KEY
docker compose up --build
```

Open **http://localhost:8080** and create the first admin when prompted
(first-run bootstrap). Then add a Panorama or firewall under **Inventory**.

How it maps to the components above: the frontend container also **proxies
`/api` to the backend** (via `deploy/compose/web.conf`) — in Kubernetes the
Ingress does that instead. The single `worker` service runs `-Q celery,upgrade`
with `--beat`, so it covers polling, upgrades, and scheduling in one process.

**Not for production:** plain HTTP, a single combined worker, no resource
limits, secrets in a `.env` file, single-node.

---

## Kubernetes (production)

Run each component as its own Deployment, backed by Postgres + Redis, behind an
Ingress that terminates TLS and routes `/api/*` to the API Service. Example
manifests are in [`deploy/kubernetes/`](../deploy/kubernetes/).

Production specifics that differ from the compose eval:

- **Split the Celery roles** into separate Deployments: `worker` (`-Q celery`),
  `upgrade-worker` (`-Q upgrade`, sized with more memory), and a single `beat`.
- **Exactly one beat replica.** Two beats double-schedule every poll.
- **Catalog via ConfigMap.** Mount `metrics.yaml` from a ConfigMap at
  `CATALOG_PATH` — it overrides the image's bundled copy, so catalog changes
  ship without rebuilding the image (and the ConfigMap must be updated when the
  catalog changes — the image bump alone won't carry it).
- **Secrets** (`FERNET_KEY`, `POSTGRES_PASSWORD`, any OIDC client secrets) via a
  Secret / SealedSecret, not a `.env` file.
- **Read-only root filesystem:** point the beat schedule at a writable path
  (`--schedule=/tmp/celerybeat-schedule`); the frontend runs nginx-unprivileged
  on `:8080`.
- **`SESSION_COOKIE_SECURE=true`** (you're behind TLS) and set `PUBLIC_BASE_URL`
  if you use OIDC.

---

## Docker vs Kubernetes

| Concern | Docker Compose | Kubernetes |
|---|---|---|
| `/api` routing | frontend nginx proxies `/api` → backend (`web.conf`) | Ingress routes `/api` (stripPrefix) to the API Service |
| TLS | none (localhost HTTP) | terminated at the Ingress; `SESSION_COOKIE_SECURE=true` |
| Celery layout | one worker, all queues + embedded `--beat` | separate `worker` / `upgrade-worker` / `beat` Deployments |
| Beat | embedded in the worker | its own Deployment, **1 replica** |
| Metric catalog | image's bundled `metrics.yaml` | mounted from a ConfigMap (overrides the image) |
| Secrets | `.env` file | Secret / SealedSecret |
| Postgres / Redis | compose services + named volume | StatefulSets/Deployments + PVCs (or managed services) |
| Filesystem | writable | `readOnlyRootFilesystem`; scratch + beat schedule → `/tmp` |
| Scale | single node | scale `worker` horizontally; keep beat a singleton |

---

## Operational notes

- **Migrations** run automatically on API startup (`alembic upgrade head`). The
  API owns the schema; workers connect once it exists.
- **First run** prompts you to create the first admin (bootstrap); local
  accounts use Argon2id, with optional TOTP, and OIDC is configurable in the UI.
- **Upgrade-worker memory:** the `upgrade` queue does snapshot capture + XML diff
  — give it more RAM than the polling worker in production.
- **Backups:** back up Postgres (and your `FERNET_KEY` — separately, since it
  decrypts what's in the database).
