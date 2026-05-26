import clsx from "clsx";

import { Device } from "../../api";

// "Online" threshold for direct devices, derived from poll freshness.
// The poller fires every POLL_INTERVAL_SECONDS (default 300s); a successful
// last_poll within 2× that window means the device is alive enough for the
// scheduled poller to have heard from it recently. Older than that and we
// stop claiming online without explicit evidence.
const DIRECT_ONLINE_MS = 10 * 60 * 1000; // 10 minutes

// "Sync is itself stale" threshold for Panorama-imported devices. Backend
// runs `panorama.sync_all` every PANORAMA_SYNC_INTERVAL_SECONDS (default
// 300s). If `last_refresh_at` is older than 2× that, the scheduled sync
// probably isn't running — the `connected` boolean we'd display may be
// many minutes (or more) out of date and shouldn't be trusted as
// definitive. Show amber "STALE" pill in that case.
//
// IMPORTANT: keep in lockstep with backend `PANORAMA_SYNC_INTERVAL_SECONDS`.
// If you bump the backend interval, bump this here too (~2× the new value).
// Future polish: expose the configured interval via an API endpoint and
// compute this client-side rather than hardcoding.
const PANORAMA_STALE_MS = 10 * 60 * 1000; // 10 minutes (2× the 300s default)

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
 * Three states for Panorama-imported devices, picked by sync freshness +
 * the connected flag:
 *
 *   - **online (emerald)**: Panorama-reported `connected` AND
 *     `last_refresh_at` is fresh (within the panorama-sync interval ×2).
 *   - **stale (amber)**: Panorama-reported `connected` but
 *     `last_refresh_at` is older than 2× the sync interval — the sync
 *     itself probably isn't running, so the `connected` flag may not
 *     reflect reality.
 *   - **disconnected (rose)**: Panorama explicitly reports the device
 *     as not connected.
 *
 * Two states for direct-added devices, picked by poll freshness:
 *
 *   - **online (emerald)**: poller's `last_poll_at` is fresh AND there
 *     is no `last_poll_error`.
 *   - **(hidden)**: never polled, errored on last poll, or last poll
 *     too old. The existing per-row "poll error" indicator covers
 *     the errored case; we don't fabricate an "offline" badge.
 *
 * Why per-source logic: `connected` is only meaningful on rows
 * Panorama-sync touches. For direct devices there's no equivalent
 * external signal, so we lean on the poller's own success/failure
 * trail. Both ultimately answer the operator's question — "is this
 * device alive?" — using whichever signal we actually have for that
 * row type.
 *
 * Variants:
 *   - "pill" (default): full text badge for use in tables.
 *   - "dot": compact 6px dot for tight spaces.
 *   - "banner": full-width warning when the device is disconnected
 *     OR sync is stale on a Panorama-imported device.
 */
export function DeviceConnectionStatus({ device, variant = "pill" }: Props) {
  const state = computeState(device);
  if (state === "hidden") return null;

  const lastSeen =
    device.source === "panorama"
      ? (device.last_refresh_at ?? device.last_seen_at)
      : device.last_poll_at;

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
        title={titleFor(state, device.source, lastSeen)}
      />
    );
  }

  if (variant === "banner") {
    if (state === "ok") return null; // No banner needed when fine.
    if (state === "stale") {
      return (
        <div className="rounded border border-amber-700 bg-amber-900/30 text-amber-200 px-3 py-2 text-xs">
          <div className="font-semibold">Sync data is stale</div>
          <div className="mt-0.5 text-[11px]">
            The scheduled Panorama sync hasn't refreshed this device's
            state in a while — the displayed connected status may be out
            of date. Check the worker logs for <span className="font-mono">panorama.sync_all</span> failures.
            {lastSeen && (
              <>
                {" "}
                Last refresh <span className="font-mono">{relTime(lastSeen)}</span>.
              </>
            )}
          </div>
        </div>
      );
    }
    // offline
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
      title={titleFor(state, device.source, lastSeen)}
    >
      {label}
    </span>
  );
}

type State = "ok" | "stale" | "offline" | "hidden";

function computeState(d: Props["device"]): State {
  if (d.source === "panorama") {
    if (!d.connected) return "offline";
    // Connected per the last sync — but check that the sync itself
    // isn't stale. With a scheduled sync running every
    // PANORAMA_SYNC_INTERVAL_SECONDS, last_refresh_at should always be
    // within ~1 sync interval. Anything older than 2× means the sync
    // isn't running, so the connected flag may be lying.
    const lastRefresh = d.last_refresh_at ?? d.last_seen_at;
    if (lastRefresh) {
      const ageMs = Date.now() - new Date(lastRefresh).getTime();
      if (ageMs > PANORAMA_STALE_MS) return "stale";
    }
    return "ok";
  }
  // direct: derive "online" from a fresh, error-free last_poll_at.
  if (!d.last_poll_at) return "hidden";
  if (d.last_poll_error) return "hidden"; // existing per-row error pill takes over
  const ageMs = Date.now() - new Date(d.last_poll_at).getTime();
  return ageMs <= DIRECT_ONLINE_MS ? "ok" : "hidden";
}

function titleFor(state: State, source: string, lastSeen: string | null): string {
  if (source === "panorama") {
    if (state === "ok") {
      return `Panorama reports device as connected (last refresh ${relTime(lastSeen)})`;
    }
    if (state === "stale") {
      return (
        `Panorama reports connected, but the sync hasn't refreshed in a while ` +
        `(last refresh ${relTime(lastSeen)}). The connected flag may be out of date — ` +
        `check the worker logs for panorama.sync_all failures.`
      );
    }
    return `Panorama reports device as disconnected (last refresh ${relTime(lastSeen)})`;
  }
  // direct
  return `Poller reached this device successfully (last poll ${relTime(lastSeen)})`;
}
