# Deployment

Three ways to run pan-fw-upgrader. Pick one.

## 1. Docker Compose (simplest)

One host, the full stack (api, worker, beat, frontend, postgres, redis) in containers. Good for a homelab, a VM, or any single-host operator deployment.

```bash
git clone https://github.com/gitupcourt/pan-fw-upgrader.git
cd pan-fw-upgrader
cp .env.example .env

# Generate the two required secrets. Append them to .env, then edit
# .env to remove the placeholder SECRET_KEY / FERNET_KEY lines.
python3 -c "import secrets; print('SECRET_KEY=' + secrets.token_urlsafe(48))" >> .env
python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())" >> .env

docker compose up -d
```

Frontend at <http://localhost:5173>, API docs at <http://localhost:8000/docs>.

### Production-ish compose

The default `docker-compose.yml` builds locally. For a deployed setup, point at the pre-built images on GHCR (`ghcr.io/gitupcourt/pan-fw-upgrader-backend` and `-frontend`) and put a reverse proxy in front for TLS:

```caddy
# Caddyfile
upgrader.example.com {
    reverse_proxy localhost:5173
}
```

### Persistent data

The compose stack uses named volumes for Postgres data, Redis AOF, and the PAN-OS images cache. To back up Postgres:

```bash
docker compose exec -T postgres pg_dump -U panfw -d panfw \
    --no-owner --no-acl > panfw-backup-$(date +%F).sql
```

To wipe and start fresh: `docker compose down -v` (the `-v` removes the volumes; **all device credentials and job history go with them**).

## 2. Kubernetes

The app is built around four Deployments (api, worker, beat, web) sharing a Postgres + Redis backend and a `ReadWriteOnce` PVC for the PAN-OS image cache. There are no upstream-published manifests in this repo today — the maintainer's own deployment lives in a separate platform repo. You'll need to write equivalents for your cluster, but the moving parts are small:

| Component | Image | Notes |
|---|---|---|
| `api` | `ghcr.io/gitupcourt/pan-fw-upgrader-backend:<tag>` | `command: uvicorn app.main:app …`. Runs Alembic migrations on startup. Healthcheck on `/health`. |
| `worker` | same image | `command: celery -A app.celery_app.celery worker --concurrency=8` |
| `beat` | same image | `command: celery -A app.celery_app.celery beat`. **Must stay at replicas=1** — two beat instances publish duplicate schedules. |
| `web` | `ghcr.io/gitupcourt/pan-fw-upgrader-frontend:<tag>` | nginx serving the prod Vite build |
| `postgres` | `postgres:16-alpine` | PVC ~10 GiB |
| `redis` | `redis:7-alpine` with AOF on | PVC ~1 GiB |

Ingress should route `/api/*` to the api Service and everything else to the web Service. **Do not strip the `/api` prefix** — the FastAPI routers carry it themselves.

The `images-data` PVC (shared between `api` and `worker` for downloaded PAN-OS image storage) is `ReadWriteOnce`. That works on single-node clusters but needs to become RWX (NFS, Longhorn, etc.) on multi-node.

## 3. From source

For development. See [`CONTRIBUTING.md`](../CONTRIBUTING.md) — it walks through the docker-compose dev loop, which is the supported path.

## Securing `FERNET_KEY` and `SECRET_KEY`

The app uses two long-lived secrets:

- **`FERNET_KEY`** encrypts every stored firewall and Panorama API credential at rest. **If it leaks, an attacker can decrypt every stored credential and use it against your firewalls.** Treat it like a private TLS key.
- **`SECRET_KEY`** signs JWTs. Rotating it forces all logged-in users to log in again. Less catastrophic but still sensitive.

Two principles regardless of how you deploy:

1. The key never lives unencrypted anywhere except the running container's environment. Not in git, not in chat / screenshots, not in shell history that's getting backed up.
2. The keys are backed up to a password manager. A host failure that takes the `.env` with it means every stored device credential becomes unrecoverable.

### Compose: `.env` hygiene

```bash
# Generate directly into .env so the value never echoes to the terminal
python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())" >> .env

# Lock down the file
chmod 600 .env

# Verify it's gitignored
git check-ignore -v .env    # should print a .gitignore reference

# Back up the VALUE to your password manager (copy from .env, paste into
# 1Password/Bitwarden/etc. as "pan-fw-upgrader FERNET_KEY")
```

**Do not regenerate `FERNET_KEY` after you've added device credentials** — every stored credential is encrypted with the current key. Rotating means re-encrypting every row, or losing them all. If you really need to rotate, do it with a script that reads every `credentials.encrypted_secret`, decrypts with the OLD key, re-encrypts with the NEW key, and writes back inside a transaction.

### Kubernetes: Sealed Secrets

Plaintext `Secret` manifests in git are decryptable by anyone with repo read access, and a stale `kubectl apply` silently overwrites the live key. The fix is to encrypt the secret value into the manifest itself using a cluster-side controller.

[`bitnami-labs/sealed-secrets`](https://github.com/bitnami-labs/sealed-secrets) is the canonical option:

```bash
# 1. Install the controller (cluster-wide, one-time)
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/latest/download/controller.yaml

# 2. Install the kubeseal CLI on your workstation.

# 3. Back up the controller's master key RIGHT AFTER install, into a
#    password manager. Without it, a cluster rebuild loses every
#    sealed value in your repo permanently.
kubectl get secret -n kube-system \
  -l sealedsecrets.bitnami.com/sealed-secrets-key=active \
  -o yaml > sealed-secrets-master.yaml
# Paste contents into your password manager, then `shred -u` the file.

# 4. Seal the app's secret and commit the result.
kubectl create secret generic pan-fw-upgrader-secrets \
    --namespace=default \
    --from-literal=FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
    --from-literal=SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
    --from-literal=POSTGRES_PASSWORD="<choose one>" \
    --from-literal=INITIAL_ADMIN_PASSWORD="<choose one>" \
    --dry-run=client -o yaml \
  | kubeseal --format=yaml \
  > 00-sealed-secret.yaml

# 5. Apply.
kubectl apply -f 00-sealed-secret.yaml
```

The resulting file is ciphertext targeted at your specific cluster's controller. Safe to commit anywhere — a copy does an attacker no good without the master key.

For 1-2 apps on a single cluster, sealed-secrets is the sweet spot of safety + simplicity. Larger setups: SOPS, External Secrets Operator + Vault, etc.

## Configuration

Backend environment variables (set in `.env` or your `Secret` / `ConfigMap`):

| Variable | Required | Notes |
|---|---|---|
| `FERNET_KEY` | yes | Generated once, kept forever. Encrypts API credentials at rest. |
| `SECRET_KEY` | yes | JWT signing key. |
| `POSTGRES_HOST` / `POSTGRES_PORT` / `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` | yes | Connection details. |
| `REDIS_HOST` / `REDIS_PORT` | yes | Celery broker + result backend. |
| `INITIAL_ADMIN_PASSWORD` | yes (first start only) | Used once to create the `admin` user; ignored afterward. |
| `SMTP_*` | optional | For emailed upgrade reports. See `.env.example`. |

## Verifying it's running

```bash
# Backend health
curl http://localhost:8000/health

# Trigger a Panorama sync on demand (auth required)
curl -X POST http://localhost:8000/api/panoramas/<id>/sync \
  -H "Authorization: Bearer <jwt>"
```

## Updating

**Compose:** `docker compose pull && docker compose up -d`

**Kubernetes:** bump the `image:` tags on the api / worker / beat / web Deployments to the new GHCR `sha-<short>` tag, then `kubectl rollout restart`. Alembic migrations run automatically on api pod startup.

**Downward migrations are not safe in the lifespan handler.** If you need to roll back schema, do it manually: `kubectl exec deploy/<api> -- alembic downgrade <rev>`.
