# Tilt dev loop for pan-capacity-analyzer
#
# Usage (from this directory on appserver01):
#   tilt up        # builds, deploys, watches for changes
#   tilt down      # removes only the *-dev resources (prod is untouched)
#
# Access:
#   App URL:  https://capacity-dev.apps.courtlukens.com
#   Tilt UI:  http://localhost:10350 (port-forward via SSH from your workstation)
#
# Prod is untouched. To redeploy prod, kubectl apply -f the homelab manifests.

default_registry("localhost:5000")

# Backend: builds the dev image (uvicorn --reload) and syncs source files
# into the running pod on save. uvicorn picks up changes automatically.
docker_build(
    "pan-capacity-backend-dev",
    context="./backend",
    dockerfile="./backend/Dockerfile.dev",
    only=["app", "requirements.txt"],
    live_update=[
        sync("./backend/app", "/app/app"),
        # requirements.txt change forces full rebuild (no sync)
    ],
)

# Frontend: vite dev server has built-in HMR; sync src/ and HMR does the rest
docker_build(
    "pan-capacity-frontend-dev",
    context="./frontend",
    dockerfile="./frontend/Dockerfile",   # the original dev Dockerfile (vite dev)
    ignore=["dist", "node_modules"],
    live_update=[
        sync("./frontend/src", "/app/src"),
        sync("./frontend/index.html", "/app/index.html"),
    ],
)

# Keep the catalog ConfigMap fresh from disk
local_resource(
    "catalog-configmap",
    cmd="kubectl create configmap pan-capacity-catalog --from-file=metrics.yaml=./catalog/metrics.yaml --dry-run=client -o yaml | kubectl apply -f -",
    deps=["catalog/metrics.yaml"],
)

k8s_yaml([
    "deploy/k8s/backend-dev.yaml",
    "deploy/k8s/frontend-dev.yaml",
    "deploy/k8s/ingress-dev.yaml",
])

# Group resources in the Tilt UI
k8s_resource("pan-capacity-backend-dev",  port_forwards="18000:8000")
k8s_resource("pan-capacity-frontend-dev", port_forwards="15173:5173")
