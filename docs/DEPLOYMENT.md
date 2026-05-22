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

## Securing the `FERNET_KEY`

The `FERNET_KEY` encrypts every stored firewall / Panorama API credential at rest in SQLite. **If it leaks, an attacker can decrypt every stored API token and use it against your firewalls.** Treat it like a private TLS key, not like an API token.

Two principles regardless of how you deploy:

1. **The key never lives unencrypted anywhere except the running container's memory.** Not in git, not pasted into chats or screenshots, not in shell history that's getting backed up.
2. **The key is backed up to a password manager** so a host failure doesn't take all your stored credentials with it.

### Compose: `.env` hygiene

The threat model on a single docker-compose host is mostly accidental leakage, not a determined attacker — anyone with shell on the host has the key regardless of clever storage. So the rules are simple:

```bash
# Generate the key directly into .env so it never echoes to the terminal
python -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())" >> .env

# Lock down the file so only your user can read it
chmod 600 .env

# Verify it's gitignored
git check-ignore -v .env    # should print a .gitignore reference

# Back up the VALUE (not the file) to your password manager
# Open .env, copy the FERNET_KEY value, paste it into 1Password/Bitwarden/etc.
# under "pan-capacity-analyzer FERNET_KEY"
```

Do not regenerate the key after you've added device credentials — every stored credential is encrypted with the current key and rotating means losing them all. If you really need to rotate, do it before adding devices, or expect to re-enter every credential by hand afterward.

### Kubernetes: Sealed Secrets (recommended for production)

Plaintext `Secret` manifests in git are decryptable by anyone with repo read access, and worse, a stale `kubectl apply` silently overwrites the live key — both of which we've eaten in real life. The fix is to encrypt the secret value into the manifest itself using a cluster-side controller.

[`bitnami-labs/sealed-secrets`](https://github.com/bitnami-labs/sealed-secrets) is the canonical option:

```bash
# 1. Install the controller (cluster-wide, one-time)
kubectl apply -f https://github.com/bitnami-labs/sealed-secrets/releases/latest/download/controller.yaml

# 2. Install the kubeseal CLI on your workstation
#    (see https://github.com/bitnami-labs/sealed-secrets/releases)

# 3. Back up the controller's master key once, RIGHT AFTER install,
#    into a password manager. Without this, a cluster rebuild loses
#    every sealed value in your repo permanently.
kubectl get secret -n kube-system \
  -l sealedsecrets.bitnami.com/sealed-secrets-key=active \
  -o yaml > sealed-secrets-master.yaml
# Paste contents into 1Password as "<cluster name> sealed-secrets master key", then delete the file.

# 4. Seal pan-capacity-secrets and commit the result
kubectl create secret generic pan-capacity-secrets \
    --namespace=default \
    --from-literal=FERNET_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
    --dry-run=client -o yaml \
  | kubeseal --format=yaml \
  > deploy/kubernetes/01-sealed-secret.yaml

# 5. Apply
kubectl apply -f deploy/kubernetes/01-sealed-secret.yaml
```

The resulting `01-sealed-secret.yaml` is encrypted ciphertext targeted at your specific cluster's controller. It's safe to commit to a public repo — re-applies are idempotent, and a copy of the file does an attacker no good without the master key (which lives only in `etcd` and your password manager).

[`deploy/kubernetes/README.md`](../deploy/kubernetes/README.md) covers the sealed-secrets variant of the apply step.

### Other options (when you outgrow sealed-secrets)

- **SOPS + age/PGP** — like sealed-secrets but works for any file, not just Secrets. More general but requires `sops` installed wherever you edit.
- **External Secrets Operator + a vault** (1Password Connect, AWS Secrets Manager, HashiCorp Vault, …) — single canonical source with audit trail and rotation. Right call once you're managing many apps or want central rotation.
- **HashiCorp Vault directly** — overkill for homelab, the right answer at multi-team / multi-cluster scale.

For 1-2 apps on a single cluster, sealed-secrets is the sweet spot of safety + simplicity.

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
