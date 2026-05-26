import clsx from "clsx";

import { Device } from "../../api";

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
  device: Pick<Device, "source" | "connected" | "last_seen_at" | "last_refresh_at">;
  variant?: Variant;
};

/**
 * Visual indicator for "is this device reachable per Panorama right now."
 *
 * Renders nothing for `source === "direct"` — Panorama-sync's `connected`
 * field isn't populated for direct-added devices, so showing a
 * "disconnected" badge there would label every direct device offline. The
 * `direct` case is intentionally a no-op; callers can render alternative
 * direct-aware status (e.g. last_poll_at freshness) themselves.
 *
 * Two states only: `online` and `disconnected`. We previously had a third
 * `stale` state coloured amber when `last_seen_at` was > 15 minutes ago,
 * meant to convey "Panorama hasn't refreshed this in a while, treat
 * `connected` as uncertain." That heuristic was wrong in practice because
 * the merged app does NOT yet run a scheduled Panorama sync — the sync
 * only fires on operator action (manual "Sync now" / device-import). So
 * `last_seen_at` becomes "stale" by the 15-min mark on EVERY device, the
 * moment an operator stops clicking the sync button — turning the badge
 * into permanent amber noise that doesn't correlate with actual device
 * health.
 *
 * When scheduled Panorama sync lands (future work — needs a celery beat
 * entry alongside `capacity.poll_all`), reintroducing a stale state will
 * become meaningful again: `last_seen_at` older than 2× the sync
 * interval = sync is itself failing. Until then, just trust `connected`
 * and don't paint amber on top of it.
 *
 * Variants:
 *   - "pill" (default): full text badge for use in tables.
 *   - "dot": compact 6px dot for tight spaces.
 *   - "banner": full-width warning for the dashboard header when the
 *     selected device is disconnected.
 */
export function DeviceConnectionStatus({ device, variant = "pill" }: Props) {
  // Direct devices: no-op (see component doc).
  if (device.source !== "panorama") return null;

  const lastSeen = device.last_seen_at ?? device.last_refresh_at;
  const state: "ok" | "offline" = device.connected ? "ok" : "offline";

  if (variant === "dot") {
    const dotColor = state === "ok" ? "bg-emerald-400" : "bg-rose-400";
    return (
      <span
        className={clsx("inline-block w-1.5 h-1.5 rounded-full mr-1.5", dotColor)}
        title={
          state === "ok"
            ? `Panorama reports device as connected (last refresh ${relTime(lastSeen)})`
            : `Panorama reports device as disconnected (last refresh ${relTime(lastSeen)})`
        }
      />
    );
  }

  if (variant === "banner") {
    if (state === "ok") return null; // No banner needed when everything's fine.
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
      title={
        state === "ok"
          ? `Panorama reports device as connected (last refresh ${relTime(lastSeen)})`
          : `Panorama reports device as disconnected (last refresh ${relTime(lastSeen)})`
      }
    >
      {label}
    </span>
  );
}
