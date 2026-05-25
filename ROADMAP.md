# Backlog

Living list of work in progress and planned, ordered roughly by intended order.
Cross off as we ship.

## In flight / next up (current priority order)

1. **Panorama / log collector upgrades.**
   Currently we only manage firewall device upgrades. Extension path:
   - Panorama HA gets new fields on the `panoramas` table
     (current_version, target_version, ha_state, ha_peer_id) and its own
     upgrade flow. Orchestrator must refuse to proxy through a Panorama
     mid-upgrade.
   - Log collectors slot into the existing `devices` table via a new
     `kind` enum {firewall, log_collector}. Discover via
     `show log-collector all` on Panorama. Reuse the via_panorama
     connection path.

2. **Ad-hoc snapshot capture.**
   Snapshots are persisted from the upgrade orchestrator, but the
   "Take snapshot now" device-row button is a 501 stub. Need a Celery
   task that captures a snapshot on demand and an ad-hoc compare
   endpoint (the latter already exists at POST /api/snapshots/diffs/compare).

3. **Cross-pair scheduling.**
   Today, multiple HA pairs in one job run sequentially. We could fan out
   to N parallel pairs (with a configurable concurrency cap) so a fleet
   of 20 pairs doesn't wait 20× the per-pair time. Watch out for the
   shared-Panorama assumption (we don't want to overwhelm a single
   Panorama's API).

## Smaller items / quality-of-life

- **Manual PAN-OS image upload.** UI to upload a .pkg/.tgz file; push to
  selected devices via SCP/import API. Compatibility checks bypassed,
  with a clear warning.
- **Email / PDF report export.** SMTP config is in `.env.example`. Need
  to render a per-job report (HTML/PDF) and send it.
- **Override-and-edit for failed prechecks.** A "remediate then re-run"
  flow that lets the user fix the issue (e.g. commit candidate config)
  from the UI rather than dropping to CLI.
- **Bulk Probe / Probe All.** Right now Probe is per-row only. A
  "Probe all devices" button next to Refresh now would give a
  Panorama-cache-bypassing fleet refresh.

## Future / bigger lifts

- **SAML / SSO.** Local auth is in place; SAML adapter stub exists at
  `backend/app/auth/saml_stub.py`. Plug in `python3-saml` or `authlib`
  when needed.
- **Maintenance-window scoped views.** Rather than inventing a "device
  set" concept, evolve `UpgradeJob` to also serve as a draft / planning
  artifact. Pre-check status, staging, and the upgrade run all scope to
  the Job. Bridges cleanly from "planned" → "running".
- **Testing — foundation laid; expand coverage.** Initial pytest suite
  lives under `backend/tests/` and covers the precheck classifier rules
  and orchestrator pure helpers (`_is_already_at_target`,
  `_is_ha_healthy`, phase-completion markers). Next targets: orchestrator
  phase functions with a mocked `PanDeviceClient`, the
  `build_client_with_fallback` Panorama-down path, and end-to-end HA-pair
  flow with a fake firewall fixture. Run with
  `docker compose exec backend pytest`. Frontend has no tests yet —
  Vitest + Testing Library would be a natural fit when components
  stabilize.
- **Worker scaling.** Long-running tasks (install + reboot can take 30+
  minutes) hold Celery worker slots. At 16+ concurrent HA pair upgrades
  on the default `--concurrency=8` worker, we'd starve. Easy to fix
  (more workers, or split orchestration into smaller tasks) when scale
  demands.

## Done (recent highlights — not exhaustive)

- Pre-checks with persistence, history, custom classifier (smart
  pass/warn/fail/skip), override gate.
- Pre-staging with version picker, train grouping, cross-train warning,
  already-downloaded indicators.
- Real upgrade orchestrator: HA-pair flow, reboot-confirm gate
  (default), HA-resume retries, wait-for-passive safety, peer-state
  check before failover.
- Phase-completion tracking so Retry truly resumes from the last
  incomplete step.
- Panorama reachability tracking + direct fallback for per-device
  probes when Panorama is down.
- Cold-boot bind-mount guard for Docker Desktop restarts.
- Already-installed detection (HA pair where one member is already on
  target).
- **Snapshot persistence + post-upgrade diff.** Dedicated `snapshots` and
  `snapshot_diffs` tables. Orchestrator captures a pre and post snapshot
  per task and persists the SnapshotCompare report. UI shows snapshot
  history per device + a full diff viewer modal.
- **HA-pair grouping in the Devices table.** Adjacent rows with a shared
  color-coded left accent, peer name in the HA cell, "select pair"
  one-click, toolbar toggle to disable grouping.
- **Percent progress for jobs.** Jobs list shows an aggregated job-level
  percent (per-phase weights + interpolation from device-reported
  download/install progress). Per-task progress bars in JobDetail were
  already in place; now the Jobs index also moves visibly during a run.
- **Device-row in-progress badge + Dashboard tile.** `/api/jobs/active-tasks`
  feeds a 5s-poll badge on each Device row (links to the job) and a
  "Upgrades in flight" Dashboard tile that calls out devices awaiting
  human confirmation.
- **Disk-space self-help.** Per-device disk-space panel runs `df -h` on
  demand and lists removable downloaded PAN-OS images with one-click
  delete (running version protected on both client and server).
- **Custom pre-check sets.** New `precheck_sets` table + CRUD API +
  Settings UI to manage named subsets; Devices page exposes a selector
  next to the bulk pre-check button.
