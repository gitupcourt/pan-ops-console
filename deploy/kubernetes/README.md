# Kubernetes deployment

Example manifests for running pan-capacity-analyzer on any Kubernetes cluster. They use the **prod images** built by CI and published to GHCR (`ghcr.io/gitupcourt/pan-capacity-analyzer-{backend,frontend}`), so you don't need to build locally.

Read top-to-bottom. Manifests are numbered for `kubectl apply -f .` ordering.

## Before you start

1. **Pick a hostname.** The example ingress assumes a single host serving both frontend and backend, with `/api/*` routed to the backend (after stripping the prefix). Replace `capacity.example.com` everywhere it appears.

2. **Generate a Fernet key** — used to encrypt firewall/Panorama API keys at rest in SQLite.
   ```bash
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Keep this safe — losing it makes existing stored credentials unrecoverable.

3. **Decide on storage.** The PVC in `10-storage.yaml` uses `local-path` (k3s default). Swap the `storageClassName` for whatever your cluster offers.

4. **Decide on ingress.** The example uses Traefik with a `stripPrefix` middleware for `/api`. If you use nginx-ingress, switch to its rewrite annotation; if you use a Gateway API controller, write the equivalent route. See [`40-ingress-traefik.yaml`](40-ingress-traefik.yaml) for the reference.

## Apply

```bash
# 1. Edit 01-secret.yaml — paste your generated FERNET_KEY
# 2. Edit 40-ingress-traefik.yaml — replace capacity.example.com with your host
# 3. Apply everything in order
kubectl apply -f .
kubectl rollout status deploy/pan-capacity-backend
kubectl rollout status deploy/pan-capacity-frontend
```

## What gets deployed

| Resource | Purpose | Required? |
|---|---|---|
| Secret `pan-capacity-secrets` | Holds `FERNET_KEY` | Yes |
| PVC `pan-capacity-data` (5 Gi) | SQLite DB persistence | Strongly recommended — without it samples + inventory are lost on restart |
| ConfigMap `pan-capacity-catalog` | Metric catalog YAML | Yes (mounted into backend at `/app/catalog/metrics.yaml`) |
| Deployment `pan-capacity-backend` (1 replica) | FastAPI + APScheduler poller | Yes |
| Service `pan-capacity-backend` (port 8000) | ClusterIP for ingress | Yes |
| Deployment `pan-capacity-frontend` (1 replica) | nginx serving the React SPA | Yes |
| Service `pan-capacity-frontend` (port 80) | ClusterIP for ingress | Yes |
| Middleware + Ingress | TLS termination, path routing | Recommended (raw HTTP fine for lab) |

The backend is a **single replica** by design — SQLite isn't safe for multiple writers. Scale to >1 only after swapping the storage backend (see [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md)).

## Updating to a newer version

Bump the `image:` tag in `20-backend.yaml` and `30-frontend.yaml` (e.g. from `:latest` to `:sha-abc1234`) and `kubectl apply` again. The Deployments will roll the new pods in.
