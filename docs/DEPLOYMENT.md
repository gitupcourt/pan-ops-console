# Deployment

Three ways to run pan-capacity-analyzer. Pick one.

## 1. Docker Compose (simplest)

One host, two containers, one named volume. Good for a homelab, a VM, a Raspberry Pi.

```bash
git clone https://github.com/gitupcourt/pan-capacity-analyzer.git
cd pan-capacity-analyzer
cp .env.example .env

# Generate a Fernet key — encrypts firewall API keys at rest in SQLite.
# Without this, the backend won't start. Losing it makes existing stored
# credentials unrecoverable, so back it up somewhere safe.
python -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())" >> .env

docker compose up -d
```

The frontend is now at <http://localhost:5174>. The backend's OpenAPI docs are reachable at <http://localhost:5174/api/docs> through the frontend's proxy.

### Production-ish compose

The default `docker-compose.yml` pulls pre-built images from GHCR. If you want TLS or a real hostname, put a reverse proxy (Caddy, Traefik, nginx) in front:

```yaml
# Snippet for ./Caddyfile
capacity.example.com {
    reverse_proxy localhost:5174
}
```

Or extend the compose file with a `caddy:` service. Up to you.

### Persistent data

Samples and the inventory DB live in the `capacity-data` Docker volume. To back up:

```bash
docker run --rm -v capacity-data:/data -v "$PWD:/backup" alpine \
    tar czf /backup/capacity-backup-$(date +%F).tar.gz -C /data .
```

To wipe and start fresh: `docker compose down -v` (note the `-v` removes the volume).

## 2. Kubernetes

See [`deploy/kubernetes/README.md`](../deploy/kubernetes/README.md) — full walkthrough lives there. Short version:

```bash
# 1. Edit deploy/kubernetes/01-secret.yaml — paste your FERNET_KEY
# 2. Edit deploy/kubernetes/40-ingress-traefik.yaml — replace capacity.example.com
# 3. Apply
kubectl apply -f deploy/kubernetes/
```

The example manifests are tested on k3s with Traefik. nginx-ingress, Gateway API, etc. work — you just need the equivalent of "route `/api/*` to backend with prefix stripped, everything else to frontend."

## 3. From source

For development or if you want to modify the code.

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate    # or .venv/Scripts/activate on Windows
pip install -r requirements.txt
export FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
uvicorn app.main:app --reload

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Frontend will be at <http://localhost:5173>; it proxies `/api/*` to <http://localhost:8000> via the vite config. Backend's OpenAPI docs are at <http://localhost:8000/docs>.

To produce production images:

```bash
docker build -t pan-capacity-backend  backend/
docker build -f frontend/Dockerfile.prod -t pan-capacity-frontend frontend/
```

## Configuration

Everything is environment variables on the backend:

| Variable | Default | Notes |
|---|---|---|
| `FERNET_KEY` | *(required)* | Generated once, kept forever. Encrypts API credentials at rest. |
| `DATABASE_URL` | `sqlite:///data/capacity.db` | Anything SQLAlchemy supports. Postgres tested but unsupported until storage swap lands. |
| `CATALOG_PATH` | `/app/catalog/metrics.yaml` | Where to load the metric catalog from at startup. |
| `POLL_INTERVAL_SECONDS` | `300` | Polling cadence. Each full cycle takes ~10s × number-of-devices. |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated. Set to your public frontend origin in prod. |

Frontend (build-time and dev-server only):

| Variable | Default | Notes |
|---|---|---|
| `VITE_ALLOWED_HOSTS` | *(permissive)* | Comma-separated hostnames the dev server accepts in Host: header, or a `.suffix` to allow subdomains. Production nginx doesn't use this. |
| `VITE_API_TARGET` | `http://backend:8000` | Where the dev server proxies `/api/*` to. Only used in `npm run dev`. |

## Verifying it's running

```bash
# Backend health
curl https://capacity.example.com/api/healthz
# {"status":"ok"}

# Trigger an immediate poll (don't wait for the scheduler)
curl -X POST https://capacity.example.com/api/metrics/poll/run-now

# Inspect what samples are landing
curl 'https://capacity.example.com/api/metrics/1/address_objects?hours=1'
```

## Updating

**Compose:** `docker compose pull && docker compose up -d`

**Kubernetes:** bump the `image:` tags in `20-backend.yaml` and `30-frontend.yaml` (or use `kubectl set image ...`), then `kubectl rollout restart`.

**From source:** `git pull && pip install -r backend/requirements.txt && (cd frontend && npm install)` and restart.
