# Claude session brief — `pan-capacity-analyzer`

This session owns the **application code**. Cluster, DNS, certs, ingress, and platform tooling are owned by a separate session that operates the `gitupcourt/homelab` repo.

## What this app is

Open-source replica of Palo Alto Networks' Capacity Analyzer. Backend (Python 3.11 + FastAPI + APScheduler) polls firewalls on a schedule and persists time-series; frontend (React + Vite + Recharts) renders trends. SQLite for portability; storage layer is swappable.

Memory: `~/.claude/projects/C--Users-clukens/memory/project_pan_capacity_analyzer.md`

## Where it runs

| Environment | URL | Image tag in cluster |
|---|---|---|
| **prod** | https://capacity.apps.courtlukens.com | `ghcr.io/gitupcourt/pan-capacity-analyzer-{backend,frontend}:0.1.0` |
| **dev (Tilt)** | https://capacity-dev.apps.courtlukens.com | `localhost:5000/pan-capacity-{backend,frontend}-dev:tilt-*` |

Cluster: k3s single-node on `appserver01.courtlukens.com` (Debian 13 VM on ESXi). Auth: SSH key at `~/.ssh/appserver01_ed25519` on the Windows operator workstation.

## Sacred state — do not regenerate

- **`FERNET_KEY`** lives in the cluster Secret `pan-capacity-secrets`. It encrypts firewall/Panorama credentials at rest in SQLite. **Regenerating it makes all existing stored credentials unrecoverable.** If you ever need to rotate, decrypt+re-encrypt the device/panorama rows first.
- **The prod SQLite DB** (`capacity.db` in the `pan-capacity-data` PVC) holds real historical sample data and real (encrypted) API credentials. Do not delete the PVC. Do not point a fresh install at it.
- The dev PVC (`pan-capacity-data-dev`) was seeded from the same local DB but diverges from prod over time. It's fine to wipe.

## How to make a code change and ship it

### Fast inner loop (recommended): Tilt

Edit code on the VM via VS Code Remote-SSH. Then on the VM:

```bash
cd ~/code/pan-capacity-analyzer
tilt up         # build dev images, deploy -dev resources, watch files
# now edit backend/app/*.py or frontend/src/*.tsx and save
# - backend: uvicorn --reload picks up Python changes in <1s
# - frontend: vite HMR pushes changes into the open browser tab
# verify at https://capacity-dev.apps.courtlukens.com
tilt down       # remove -dev resources (prod untouched)
```

Access the Tilt UI from Windows: `ssh -L 10350:localhost:10350 ... appserver01.courtlukens.com`, then http://localhost:10350.

### Promoting to prod

1. Commit + push the code change to `gitupcourt/pan-capacity-analyzer`.
2. Build + push a new tagged image:
   ```bash
   cd ~/code/pan-capacity-analyzer
   NEW_TAG=0.1.1
   sudo docker build -t ghcr.io/gitupcourt/pan-capacity-analyzer-backend:$NEW_TAG  -t ghcr.io/gitupcourt/pan-capacity-analyzer-backend:latest backend/
   sudo docker build -f frontend/Dockerfile.prod -t ghcr.io/gitupcourt/pan-capacity-analyzer-frontend:$NEW_TAG -t ghcr.io/gitupcourt/pan-capacity-analyzer-frontend:latest frontend/
   sudo docker push ghcr.io/gitupcourt/pan-capacity-analyzer-backend:$NEW_TAG
   sudo docker push ghcr.io/gitupcourt/pan-capacity-analyzer-backend:latest
   sudo docker push ghcr.io/gitupcourt/pan-capacity-analyzer-frontend:$NEW_TAG
   sudo docker push ghcr.io/gitupcourt/pan-capacity-analyzer-frontend:latest
   ```
3. Bump the `image:` tag in `~/homelab/manifests/pan-capacity-analyzer/{20-backend,30-frontend}.yaml`, commit + push the `homelab` repo.
4. Apply: `k3s kubectl apply -f ~/homelab/manifests/pan-capacity-analyzer/`
5. Watch the rollout: `k3s kubectl rollout status deploy/pan-capacity-backend` (and `-frontend`)
6. Verify at https://capacity.apps.courtlukens.com

### Quick rollback if prod breaks

```bash
k3s kubectl rollout undo deploy/pan-capacity-backend
k3s kubectl rollout undo deploy/pan-capacity-frontend
```

## What you can do without asking the platform session

- Push new image versions (backend or frontend)
- Bump the image tag in the homelab manifests
- Edit values inside the existing manifest YAMLs (e.g., env vars, replicas, resource requests within reason)
- Run `tilt up` / `tilt down`
- Restart a Deployment

## What to ask the platform session for

- New PVC, new ingress hostname, new namespace
- Adding/rotating shared secrets (e.g., the FERNET_KEY — never regenerate without explicit coordination)
- Increasing resource limits in a way that meaningfully changes node sizing
- Adding any cluster-wide piece (monitoring, sealed-secrets, etc.)
- DNS records / firewall changes

## Manifest layout (in the `gitupcourt/homelab` repo)

```
manifests/pan-capacity-analyzer/
  00-secrets.yaml              # ghcr-pull + pan-capacity-secrets (FERNET_KEY)
  10-storage.yaml              # PVC pan-capacity-data (5Gi local-path)
  15-catalog-configmap.yaml    # metrics.yaml as a ConfigMap
  20-backend.yaml              # Deployment + Service
  30-frontend.yaml             # Deployment + Service (nginx serving prod build)
  40-ingress.yaml              # Two ingresses on capacity.apps.courtlukens.com, /api -> backend (strip), / -> frontend
```

The dev variants (`*-dev`) live in `deploy/k8s/` of THIS repo and are applied by Tilt.

## App-specific gotchas

- `frontend/vite.config.ts` includes `allowedHosts: ['.apps.courtlukens.com']` — required by Vite 5.4+ host check for the dev URL. Don't remove.
- Backend routes are unprefixed (`/devices`, `/panoramas`, `/metrics`). The ingress uses a Traefik `stripPrefix` middleware to remove `/api` before forwarding. Frontend always calls `/api/...`.
- SQLite handle: scale backend to 0 before any direct file ops on the DB (kubectl cp, host filesystem copy). Otherwise risk of WAL corruption.
- Image build: backend uses Python 3.11 (NOT 3.12) because `pan-os-python` still imports `distutils`. Don't bump.
