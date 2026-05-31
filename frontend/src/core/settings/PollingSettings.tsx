import { Card, CardHeader } from "../ui/ui";

/**
 * Polling subtab. PR-1 ships this as a read-only explainer of how
 * capacity polling works; the split config/system intervals land in the
 * backend (PR-2), and editable controls + per-Panorama polling-pressure
 * telemetry are the deferred PR-4. Kept honest about what's live vs
 * coming so the tab isn't a lie.
 */
export default function PollingSettings() {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          title="Polling"
          description="How the capacity analyzer samples your fleet."
        />
        <div className="px-4 py-3 text-sm text-zinc-300 space-y-3">
          <p>
            Enabled devices are polled on a schedule and their resource
            counters stored as time-series for the Capacity views and the
            alert engine.
          </p>
          <p className="text-zinc-400">
            <span className="text-zinc-200">Rolling out:</span> metrics are
            being split into two cadences — fast-changing{" "}
            <span className="text-zinc-200">system</span> counters (CPU,
            sessions, throughput) sampled frequently, and slow-changing{" "}
            <span className="text-zinc-200">config</span> facts (licenses,
            software version, group membership) sampled less often — so we
            don't burn API calls re-reading things that rarely move.
          </p>
          <p className="text-zinc-500 text-xs">
            Editable intervals and per-Panorama polling-pressure telemetry
            (in-flight calls, latency, error rate) are coming to this tab.
            Today the cadences are set server-side.
          </p>
        </div>
      </Card>
    </div>
  );
}
