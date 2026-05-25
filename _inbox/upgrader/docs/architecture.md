# Architecture

Updated mid-build. Reflects what's actually in the tree, not what was sketched
during the initial scaffold.

## Goals

- Bulk-upgrade Palo Alto Networks firewalls (10s now, designed for 100s).
- HA-aware: never take both halves of an HA pair offline at once.
- Pause for human confirmation between failover and upgrading the new backup.
- Support direct-to-device AND Panorama-proxied connections (per device).
- Tolerate Panorama outages — fall back to direct probes when possible.
- Pluggable auth (local now, SAML-ready).
- Persist devices, credentials, jobs, pre-check history, snapshots, reports.

## Stack

| Layer | Choice |
|---|---|
| Frontend | React 18 + Vite + Tailwind. TanStack Query for server state. React Router. |
| API | FastAPI 0.115 (Python 3.11). Pydantic v2. SQLAlchemy 2.0. |
| Job orchestration | Celery 5 + Redis (broker & result backend). watchmedo for dev hot-reload. |
| Database | PostgreSQL 16. Migrations via Alembic. |
| Auth | Local username/password (argon2) → JWT. Pluggable for SAML (stub in `auth/saml_stub.py`). |
| Crypto | Fernet (cryptography lib) for credential-at-rest encryption. |
| Container | Docker Compose: postgres, redis, backend (uvicorn), worker (celery), beat, frontend (vite). |

### Why 3.11 not 3.12

`pan-os-python` (current 1.12.x) still imports `distutils.version`,
removed in Python 3.12. Stuck on 3.11 until upstream drops that.

## Service topology

```
┌──────────────┐   HTTPS   ┌──────────────┐
│  React UI    │ ────────► │   FastAPI    │
│ (Vite)       │ ◄──poll── │   (uvicorn)  │
└──────────────┘           └──────┬───────┘
                                  │
                  ┌───────────────┼────────────────┐
                  ▼               ▼                ▼
            ┌──────────┐   ┌────────────┐   ┌────────────┐
            │ Postgres │   │   Redis    │   │ Celery     │
            │ (state)  │   │ (broker)   │   │ Workers +  │
            └──────────┘   └────────────┘   │ Beat       │
                                            └─────┬──────┘
                                                  │
                                       ┌──────────┴──────────┐
                                       ▼                     ▼
                                  ┌─────────┐          ┌──────────┐
                                  │ Direct  │          │ Panorama │
                                  │ to FW   │          │  Proxy   │
                                  │ (XMLAPI)│          │ (target=)│
                                  └─────────┘          └──────────┘
```

`beat` schedules a periodic `refresh.sync_all_panoramas` task. Worker
also runs `precheck.run_device_precheck`, `stage.run_device_stage`,
`upgrade.run_job`, `upgrade.drive_pair_task`.

## Data model summary

**Identity:**
- `users` — local users (argon2 hash). `auth_provider='local'` today,
  reserved for `'saml:<idp>'` later.

**Connections:**
- `credentials` — Fernet-encrypted secret blob, scoped device or panorama.
- `panoramas` — Panorama instance info + reachability tracking
  (`reachable`, `last_reachability_at`, `last_reachability_error`).

**Inventory:**
- `devices` — firewall row with hostname, IP, serial, model, current
  version, content versions, HA detail (`ha_role` + `ha_state` +
  `ha_sync_state`), `ha_peer_id`, `device_group`, `template_stack`,
  `connected`, `last_seen_at`, `last_refresh_at`, `verify_tls`,
  `proxy_via_panorama`, `licenses` (JSON), `staged_version`,
  `staged_at`, `staged_error`, `downloaded_versions` (JSON list).
- `panos_images` — uploaded images OR version-only references for
  device-side pull.

**Pre-checks:**
- `precheck_runs` — every readiness check execution with the full
  classified results (per-check `severity` ∈ {pass, warn, fail, skip}),
  rolled-up counters (`pass_count` etc.), and an `overall_severity`.
- `bulk_precheck_runs` — parent op grouping N precheck runs under one
  user click.

**Pre-staging:**
- `bulk_stage_runs` + `device_stage_runs` — parallels the precheck
  pair, tracking image-download operations.

**Upgrades:**
- `upgrade_jobs` — name, target_version, workflow flags
  (`require_failover_confirmation`,
  `require_primary_upgrade_confirmation`, `auto_failback`,
  `auto_reboot_after_install`), state ∈ {pending, running,
  awaiting_confirmation, completed, failed, aborted}.
- `device_upgrade_tasks` — per-device child of a job. Carries
  `ha_pair_key` so HA peers share fate; `phase` (the state machine);
  `progress` (JSON: log lines, completed_phases markers, precheck/
  postcheck run ids, snapshot reference, failing_checks).

## Upgrade state machine

### Per-task phases (HA pair driver picks passive first):
```
PENDING
  → PRECHECK
  → AWAITING_PRECHECK_OVERRIDE (only if precheck FAILed severity)
  → SNAPSHOT
  → DOWNLOADING_IMAGE             (skipped if image already on device
                                   OR device already at target)
  → SUSPEND_SECONDARY             (skipped if device already at target)
  → UPGRADE_SECONDARY             (install only — does NOT reboot)
  → AWAITING_REBOOT_CONFIRM       (default; skip with auto_reboot flag)
  → UPGRADE_SECONDARY             (reboot + wait_for_ready + HA resume
                                   + wait_for_passive)
  → POSTCHECK_SECONDARY
  → AWAITING_POSTCHECK_OVERRIDE   (only if postcheck FAILed)
  → AWAITING_FAILOVER_CONFIRM     (optional gate before failover)
  → FAILOVER                      (suspend the active member; the
                                   upgraded peer takes over)
  → AWAITING_PRIMARY_UPGRADE_CONFIRM (optional gate before upgrading
                                      the now-passive former active)
  → UPGRADE_PRIMARY               (same flow, on the other member)
  → POSTCHECK_PRIMARY
  → FAILBACK                      (only if auto_failback)
  → DONE
```

Standalone is the same set without HA-specific phases.

### Phase-completion markers

`task.progress.completed_phases` is a list of markers
(`precheck`, `snapshot`, `ensure_image`, `suspend_ha`,
`install_complete`, `ha_resume_complete`, `failover`, `postcheck`).
Each phase function checks for its marker at the top and short-circuits
if present. This is what makes Retry a true "resume from last
incomplete step" instead of a from-scratch re-run.

### HA safety belts

- `_wait_for_ha_subsystem_ready` — after reboot's mgmt-plane wait, poll
  until ha_state ≠ 'initial' (HA daemon ready), 30-min cap.
- `_ha_op_with_retry` — wraps every HA control op (suspend / resume /
  failover) in 5 retries × 30s with fresh sockets.
- `_wait_for_passive` — after resume_ha, poll until device is in
  `passive` / `active-secondary`. 10-min cap. Each state transition
  logged to task.progress.log.
- `_phase_failover` peer-state precondition — refuses to suspend the
  active member unless the upgraded peer is in a normal HA state.
- `_drive_ha_pair` early-out — both members at target AND
  `_is_ha_healthy` → mark DONE, no failover. Two predicates so a
  partially-complete job (install ok, HA still suspended) re-runs the
  HA block.
- `wait_for_ready` requires 3 consecutive successful probes (avoids
  returning on a boot-time API blip).

### Pre-check classifier (`services/precheck_classifier.py`)

Wraps pan-os-upgrade-assurance's binary check results in a four-state
severity (pass / warn / fail / skip) and applies project-specific rules:

- `ha`: SKIP for standalone; FAIL only if state isn't `active|passive|
  active-primary|active-secondary`.
- `panorama`: SKIP for direct-attached devices.
- `expired_licenses`: PASS if only Threat Prevention is expired AND
  Advanced Threat Prevention is licensed.
- `dynamic_updates`: PASS for the WildFire-real-time-only schedule
  warning (expected).
- `content_version`: WARN instead of FAIL (operationally not a
  blocker).

Adding a new rule = adding an entry to `_RULES` in that file. The check
list itself comes from pan-os-upgrade-assurance's `CheckType` enum and
is exposed via `GET /api/devices/precheck/available`.

## Resilience patterns

- **Worker code reload** via `watchmedo auto-restart` — uvicorn-style
  reloading for the Celery worker so editing services / tasks doesn't
  require manual `docker compose restart worker`.
- **Cold-boot bind-mount guard** — `wait-for-mount.sh` baked into the
  backend image (at `/usr/local/bin/`, outside the bind mount) blocks
  container startup until `/app/app/main.py` is visible. Backend
  healthcheck on `/health` so Docker can see whether uvicorn is
  actually serving (vs. just process-alive).
- **Panorama down → direct fallback** — `build_client_with_fallback`
  tries the configured proxy path, marks the Panorama unreachable on
  failure, then falls back to a direct connection if the device has
  its own credential + reachable IP. UI shows a banner + per-Panorama
  badge.
- **Schema migrations preserve data** — Alembic. The lifespan hook
  auto-applies on backend startup. Watch out: if you create an empty
  migration file and the backend reloads before you fill it in, alembic
  may mark it applied with the empty content; rewrite + manually patch
  the dev DB.

## Where the heavy logic lives

- `backend/app/services/upgrade.py` — orchestrator. Phase functions,
  pair driver, HA waits, retries, completion markers. Currently ~750
  lines; could reasonably be split into modules (phases, ha, helpers).
- `backend/app/services/precheck.py` — `build_client_with_fallback`,
  `probe_device`, `run_precheck_for_device`. The fallback logic lives
  here because every device-facing service uses it.
- `backend/app/services/pan_client.py` — `PanDeviceClient` (direct or
  via Panorama via the `.direct()` / `.via_panorama()` factories).
  Wraps pan-os-python `Firewall` and pan-os-upgrade-assurance
  `FirewallProxy`. Op methods + retries are owned by the caller
  service, not this client.
- `backend/app/services/precheck_classifier.py` — pass/warn/fail/skip
  classifier rules.

Frontend's hot spots:
- `frontend/src/pages/Devices.tsx` — table, filters, version picker,
  bulk pre-check, bulk stage, new-job modal. ~1500 lines; should be
  decomposed.
- `frontend/src/pages/JobDetail.tsx` — task timeline, gates, retry.

## What's intentionally NOT there

- **No tests yet.** The codebase is now substantial enough to warrant
  some; deferred.
- **No production hardening** — single-host docker-compose. Production
  would want TLS termination, secret management, an actual orchestrator.
- **No multi-tenant model** — all devices/Panoramas/credentials are
  global to the app instance.
- **No SAML wired** — stub only.
