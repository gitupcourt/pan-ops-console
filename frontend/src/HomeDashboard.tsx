import { NavLink } from "react-router-dom";

import { Card, CardHeader } from "./core/ui/ui";

/**
 * Home Dashboard — `/`.
 *
 * Holistic at-a-glance view of the whole environment. Each panel is a
 * focused frame that summarizes one slice (alerts, upgrades, version
 * distribution, fleet status) and links into the dedicated page for
 * that slice.
 *
 * **This file is currently scaffolding.** Phase 7 (navigation
 * restructure) lays out the four frames as placeholder cards so the
 * IA + routing are in place. Phase 8 ships the backend aggregation
 * endpoints; phases 12 (alerts) and 14 (home frames) fill the frames
 * with real data. Until then each frame renders an empty-state with
 * the link target wired up, so an operator clicking around in dev
 * doesn't dead-end.
 *
 * Design source: the operator's spec doc + the PA Strata Cloud
 * Manager "Health & Software Management" UI as inspiration.
 */
export default function HomeDashboard() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-zinc-100">Overview</h2>
        <p className="text-sm text-zinc-400 mt-1">
          Fleet status at a glance — drill into any frame for detail.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <AlertsFrame />
        <UpgradesFrame />
        <VersionDistributionFrame />
        <FleetSnapshotFrame />
      </div>
    </div>
  );
}

// ----- frames -----
//
// Each frame is a self-contained placeholder. Phase 14 swaps the
// `<Placeholder />` for the real implementation. Keeping the imports +
// link targets stable here means the next PR is a strictly-additive
// change inside each frame — no churn at this file's level.

function AlertsFrame() {
  return (
    <Card>
      <CardHeader
        title="Active alerts"
        description="Devices approaching configured capacity thresholds."
        action={
          <NavLink
            to="/alerts"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            View all →
          </NavLink>
        }
      />
      <Placeholder
        text="Alerts frame ships in phase 12 with the rule engine. Click 'View all' to land on the (currently empty) alerts page."
      />
    </Card>
  );
}

function UpgradesFrame() {
  return (
    <Card>
      <CardHeader
        title="Upgrade jobs"
        description="Recent + in-flight upgrade orchestration."
        action={
          <NavLink
            to="/upgrade"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            View all →
          </NavLink>
        }
      />
      <Placeholder
        text="Live counts of success / in-flight / failed jobs ship in phase 14. Click 'View all' to go to the existing /upgrade page."
      />
    </Card>
  );
}

function VersionDistributionFrame() {
  return (
    <Card>
      <CardHeader
        title="PAN-OS version distribution"
        description="How many devices on each running version."
        action={
          <NavLink
            to="/inventory"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            View inventory →
          </NavLink>
        }
      />
      <Placeholder
        text="Version-count tiles ship in phase 14. Click a tile (when live) to filter inventory to that version."
      />
    </Card>
  );
}

function FleetSnapshotFrame() {
  return (
    <Card>
      <CardHeader
        title="Fleet at a glance"
        description="Devices, Panoramas, polling health."
        action={
          <NavLink
            to="/inventory"
            className="text-xs text-blue-400 hover:text-blue-300"
          >
            View inventory →
          </NavLink>
        }
      />
      <Placeholder
        text="Live counts (online/disconnected/never-polled) ship in phase 14."
      />
    </Card>
  );
}

function Placeholder({ text }: { text: string }) {
  return (
    <div className="px-4 py-8 text-center text-xs text-zinc-500 italic">
      {text}
    </div>
  );
}
