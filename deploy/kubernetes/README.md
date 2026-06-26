# Kubernetes deployment

Example manifests for running **pan-ops-console** on any Kubernetes cluster.
They mirror the production topology, hardened (non-root, dropped capabilities,
read-only root FS, seccomp `RuntimeDefault`, NetworkPolicies) and generic
(replace the `example.com` hostname and the `REPLACE_ME` secrets).

> For the simplest possible run, use Docker Compose instead — see
> [`deploy/compose/`](../compose/) and [`docs/DEPLOYMENT.md`](../../docs/DEPLOYMENT.md).

## What gets deployed

| File | Resource(s) |
|---|---|
| `01-secret.yaml` | `pan-ops-console-secrets` — FERNET_KEY, Postgres + Redis creds (**plaintext template — see "Secrets" below**) |
| `05-catalog-configmap.yaml` | metric catalog (placeholder — regenerate from `catalog/metrics.yaml`) |
| `10-postgres.yaml` | PostgreSQL Deployment + Service + PVC (**system of record**) |
| `15-redis.yaml` | Redis Deployment + Service + PVC (Celery broker + locks) |
| `20-backend.yaml` | FastAPI API + Service. Runs `alembic upgrade head` on startup |
| `30-workers.yaml` | Celery `worker` (capacity), `upgrade-worker`, and `beat` (one each) |
| `50-frontend.yaml` | nginx serving the SPA + Service |
| `60-ingress-traefik.yaml` | Traefik ingress: `/api/*` → backend (prefix-stripped), `/` → frontend |
| `70-networkpolicies.yaml` | default-deny ingress + per-tier allows (optional, recommended) |

**Redis + at least one worker are mandatory** — the API starts without them but
nothing polls, alerts, or runs upgrades.

## Before you start

1. **Pick a hostname.** Replace `pan-ops-console.example.com` in
   `60-ingress-traefik.yaml`, and in `CORS_ORIGINS` / `PUBLIC_BASE_URL` in
   `20-backend.yaml`.

2. **TLS.** Provide a secret named `pan-ops-console-tls` (cert-manager, or
   `kubectl create secret tls pan-ops-console-tls --cert=... --key=...`).

3. **Storage.** The PVCs use `local-path` (k3s default). Swap the
   `storageClassName` for whatever your cluster offers.

4. **Images.** Public on GHCR; if you mirror them to a private registry, add
   `imagePullSecrets` to the Deployments. Pin to a release tag for real deploys.

5. **Ingress controller.** The example uses Traefik with a `stripPrefix`
   middleware for `/api`. For nginx-ingress or Gateway API, write the equivalent
   (route `/api/*` to the backend with the prefix stripped).

## Apply

```bash
# 1. Fill in the secrets
#    Generate a Fernet key:
#      python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
#    Edit 01-secret.yaml -> FERNET_KEY, POSTGRES_PASSWORD, REDIS_PASSWORD

# 2. Load the real metric catalog (the shipped one is a placeholder)
kubectl create configmap pan-ops-console-catalog \
  --from-file=metrics.yaml=catalog/metrics.yaml \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Apply everything (numbered for ordering)
kubectl apply -f .

# 4. Watch it come up (the API runs migrations on first start)
kubectl rollout status deploy/pan-ops-console-backend
kubectl rollout status deploy/pan-ops-console-worker
```

Re-run step 2 (and restart backend/worker/beat) whenever `catalog/metrics.yaml`
changes.

## Notes

- **Migrations** run automatically when the API starts (`alembic upgrade head`).
  No separate Job needed; bring the backend up before/with the workers.
- **Run exactly one `beat`.** Two schedulers double-schedule every task.
- **The capacity `worker` is safe to scale**; `beat` is not.
- **Upgrade-worker** does snapshot capture + XML diffing — it has more memory and
  a disk-backed `/tmp`. Keep it separate from the capacity worker so a long HA
  upgrade cannot starve metric polling.

## Secrets

`01-secret.yaml` is a **plaintext `Secret` template** — fine for trying the app
out, but **do not commit it filled-in or run it long-term**. The `FERNET_KEY` is
the keys-to-the-kingdom for every stored firewall/Panorama credential, and a
plaintext Secret in git is readable by anyone with repo access; a stale
`kubectl apply` also silently overwrites the live value.

For anything real, seal it with [Bitnami **Sealed Secrets**](https://github.com/bitnami-labs/sealed-secrets):

```bash
# 1. Install the controller (one-time, cluster-wide)
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/latest/download/controller.yaml

# 2. RIGHT AFTER install, back up the controller's master key to a password
#    manager — without it, a cluster rebuild loses every sealed value forever.
kubectl get secret -n kube-system \
  -l sealedsecrets.bitnami.com/sealed-secrets-key=active -o yaml > sealed-secrets-master.yaml
#    Paste into your password manager, then delete the file.

# 3. Seal the secret (fill in the same values as 01-secret.yaml) and commit it
kubectl create secret generic pan-ops-console-secrets \
  --from-literal=FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  --from-literal=POSTGRES_USER=panops \
  --from-literal=POSTGRES_DB=panops \
  --from-literal=POSTGRES_PASSWORD='CHANGE_ME' \
  --from-literal=REDIS_PASSWORD='CHANGE_ME' \
  --dry-run=client -o yaml \
  | kubeseal --format=yaml > 01-sealed-secret.yaml

# 4. Use 01-sealed-secret.yaml instead of 01-secret.yaml
```

The sealed output is ciphertext targeted at your cluster's controller — safe to
commit even to a public repo. **Back up the `FERNET_KEY` value separately** (a
password manager): losing it makes every stored credential unrecoverable.

For larger setups: SOPS + age/PGP, or External Secrets Operator backed by a
vault. For one cluster, Sealed Secrets is the sweet spot.
