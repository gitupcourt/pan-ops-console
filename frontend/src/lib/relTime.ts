/**
 * Compact "time since" label for a timestamp: "45s ago", "12m ago",
 * "5h ago", "3d ago", "3w ago". Returns "never" for null and
 * "in the future" for clock skew. Used wherever the UI shows when a
 * device was last seen / last reported.
 */
export function relTime(iso: string | null): string {
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
  if (day < 14) return `${day}d ago`;
  const wk = Math.floor(day / 7);
  return `${wk}w ago`;
}
