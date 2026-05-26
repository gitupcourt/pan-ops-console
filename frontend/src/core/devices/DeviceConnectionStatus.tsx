import clsx from "clsx";

import { Device } from "../../api";

// "Online" threshold for direct devices, derived from poll freshness.
// The poller fires every POLL_INTERVAL_SECONDS (default 300s); a successful
// last_poll within 2× that window means the device is alive enough for the
// scheduled poller to have heard from it recently. Older than that and we
// stop claiming online without explicit evidence.
const DIRECT_ONLINE_MS = 10 * 60 * 1000; // 10 minutes

function relTime(iso: string | null): string {
  if (!iso) return "never";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return "in the future";
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 48) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  return `${day}d ago`;
}

type Variant = "pill" | "dot" | "banner";

type Props = {
  device: Pick<
    Device,
    "source" | "connected" | "last_seen_at" | "last_refresh_at"
    | "last_poll_at" | "last_poll_error"
  >;
  variant?: Variant;
};

/**
 * Visual indicator for "is this device alive right now."
 *
 * Two state sources, picked by `device.source`:
 *
 * - **Panorama-imported** (`source === "panorama"`): trust the
 *   `connected` flag refreshed by Panorama-sync. `true` → online,
 *   `false` → disconnected. This is the authoritative signal for these
 *   devices.
 *
 * - **Direct-added** (`source === "direct"`): derive from poll
 *   freshness. The capacity poller runs every POLL_INTERVAL_SECONDS
 *   (default 300s) and writes `last_poll_at` on success or
 *   `last_poll_error` on failure. A `last_poll_at` within 10 minutes
 *   AND no `last_poll_error` means the poller successfully reached
 *   this device on the last cycle — that's effectively "online."
 *   Anything else: render nothing (we don't fabricate an offline
 *   state for direct devices that have just never been polled or
 *   are showing transient errors — the existing per-row error
 *   indicator already surfaces that).
 *
 * Why no "stale" state: we don't run a scheduled Panorama sync yet,
 * so an amber stale pill on Panorama-imported devices became permanent
 * noise. For direct devices the poller does write `last_poll_at`
 * on a schedule, so a future stale state IS meaningful — but the
 * binary online/no-pill scheme covers the operator's current need
 * cleanly, so we hold off on adding stale back until there's a
 * concrete UX need.
 *
 * Variants:
 *   - "pill" (default): full text badge for use in tables.
 *   - "dot": compact 6px dot for tight spaces.
 *   - "banner": full-width warning when the device is disconnected.
 *     Only fires for Panorama-imported disconnected devices, since
 *     that's the only state we render with high confidence.
 */
export function DeviceConnectionStatus({ device, variant = "pill" }: Props) {
  const state = computeState(device);
  if (state === "hidden") return null;

  const lastSeen =
    device.source === "panorama"
      ? (device.last_seen_at ?? device.last_refresh_at)
      : device.last_poll_at;

  if (variant === "dot") {
    const dotColor = state === "ok" ? "bg-emerald-400" : "bg-rose-400";
    return (
      <span
        className={clsx("inline-block w-1.5 h-1.5 rounded-full mr-1.5", dotColor)}
        title={titleFor(state, device.source, lastSeen)}
      />
    );
  }

  if (variant === "banner") {
    if (state === "ok") return null; // No banner needed when fine.
    return (
      <div className="rounded border border-rose-700 bg-rose-900/30 text-rose-200 px-3 py-2 text-xs">
        <div className="font-semibold">Device is disconnected</div>
        <div className="mt-0.5 text-[11px]">
          Panorama reports this device as not connected — polling is paused
          until Panorama sees the device come back online.
          {lastSeen && (
            <>
              {" "}
              Last sync refresh <span className="font-mono">{relTime(lastSeen)}</span>.
            </>
          )}
        </div>
      </div>
    );
  }

  // pill (default)
  const colors =
    state === "ok"
      ? "border-emerald-700 bg-emerald-900/30 text-emerald-300"
      : "border-rose-700 bg-rose-900/30 text-rose-300";
  const label = state === "ok" ? "online" : "disconnected";

  return (
    <span
      className={clsx(
        "inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide border",
        colors,
      )}
      title={titleFor(state, device.source, lastSeen)}
    >
      {label}
    </span>
  );
}

type State = "ok" | "offline" | "hidden";

function computeState(d: Props["device"]): State {
  if (d.source === "panorama") {
    return d.connected ? "ok" : "offline";
  }
  // direct: derive "online" from a fresh, error-free last_poll_at.
  if (!d.last_poll_at) return "hidden";
  if (d.last_poll_error) return "hidden"; // existing per-row error pill takes over
  const ageMs = Date.now() - new Date(d.last_poll_at).getTime();
  return ageMs <= DIRECT_ONLINE_MS ? "ok" : "hidden";
}

function titleFor(state: State, source: string, lastSeen: string | null): string {
  if (source === "panorama") {
    return state === "ok"
      ? `Panorama reports device as connected (last refresh ${relTime(lastSeen)})`
      : `Panorama reports device as disconnected (last refresh ${relTime(lastSeen)})`;
  }
  // direct
  return `Poller reached this device successfully (last poll ${relTime(lastSeen)})`;
}
