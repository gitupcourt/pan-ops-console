import clsx from "clsx";

import { Device } from "../../api";

// Stale threshold for "Last seen" coloring on connected devices. Anything
// fresher than this in green; older slips to amber even when `connected`
// is still true — because if Panorama's sync hasn't refreshed in a
// while, the connected flag itself is stale data and shouldn't read as
// definitively-online.
const STALE_MS = 15 * 60 * 1000; // 15 minutes

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
 * Variants:
 *   - "pill" (default): full text badge for use in tables, e.g. next to
 *     a device name.
 *   - "dot": compact 6px dot for use inline with very limited space
 *     (e.g. inside a dropdown <option> — though `<option>` styling is
 *     limited so this is mostly for future use).
 *   - "banner": full-width rose/amber/emerald banner with the rich
 *     last-seen context, for the dashboard header.
 */
export function DeviceConnectionStatus({ device, variant = "pill" }: Props) {
  // Direct devices: no-op (see component doc).
  if (device.source !== "panorama") return null;

  const lastSeen = device.last_seen_at ?? device.last_refresh_at;
  const lastSeenMs = lastSeen ? Date.now() - new Date(lastSeen).getTime() : null;
  const isStaleSync = lastSeenMs != null && lastSeenMs > STALE_MS;

  // Three states:
  //   ok       — Panorama-reported connected AND last_seen_at is fresh
  //   stale    — Panorama-reported connected but last_seen_at is old
  //              (Panorama itself hasn't synced in a while; the "connected"
  //              boolean may be stale data, so treat as uncertain)
  //   offline  — Panorama explicitly reports disconnected
  const state: "ok" | "stale" | "offline" = device.connected
    ? isStaleSync
      ? "stale"
      : "ok"
    : "offline";

  if (variant === "dot") {
    const dotColor =
      state === "ok"
        ? "bg-emerald-400"
        : state === "stale"
          ? "bg-amber-400"
          : "bg-rose-400";
    return (
      <span
        className={clsx("inline-block w-1.5 h-1.5 rounded-full mr-1.5", dotColor)}
        title={
          state === "ok"
            ? `Panorama reports device as connected (last seen ${relTime(lastSeen)})`
            : state === "stale"
              ? `Panorama reports connected but sync data is stale (last seen ${relTime(lastSeen)})`
              : `Panorama reports device as disconnected (last seen ${relTime(lastSeen)})`
        }
      />
    );
  }

  if (variant === "banner") {
    if (state === "ok") return null; // No banner needed when everything's fine.
    const wrap =
      state === "offline"
        ? "border-rose-700 bg-rose-900/30 text-rose-200"
        : "border-amber-700 bg-amber-900/20 text-amber-200";
    const headline =
      state === "offline"
        ? "Device is disconnected"
        : "Device sync data is stale";
    return (
      <div className={clsx("rounded border px-3 py-2 text-xs", wrap)}>
        <div className="font-semibold">{headline}</div>
        <div className="mt-0.5 text-[11px]">
          {state === "offline"
            ? "Panorama reports this device as not connected — polling is paused until Panorama sees the device come back online."
            : "Panorama hasn't refreshed this device's state recently; the displayed metrics may not reflect current device status."}
          {lastSeen && (
            <>
              {" "}
              Last seen <span className="font-mono">{relTime(lastSeen)}</span>.
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
      : state === "stale"
        ? "border-amber-700 bg-amber-900/30 text-amber-300"
        : "border-rose-700 bg-rose-900/30 text-rose-300";
  const label =
    state === "ok" ? "online" : state === "stale" ? "stale" : "disconnected";

  return (
    <span
      className={clsx(
        "inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide border",
        colors,
      )}
      title={
        state === "ok"
          ? `Panorama reports device as connected (last seen ${relTime(lastSeen)})`
          : state === "stale"
            ? `Panorama reports connected but sync data is stale (last seen ${relTime(lastSeen)})`
            : `Panorama reports device as disconnected (last seen ${relTime(lastSeen)})`
      }
    >
      {label}
    </span>
  );
}
