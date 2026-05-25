import type { ReactNode } from "react";

/**
 * Label + slot wrapper used in every form on the Devices page.
 */
export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="block">
      <span className="block text-sm text-slate-300">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

/**
 * "Key over value" detail block used inside the expanded device row and a few
 * other detail surfaces. Renders an em dash when value is null/empty so the
 * grid stays visually aligned.
 */
export function Detail({ k, v }: { k: string; v: string | null }) {
  return (
    <div>
      <div className="text-xs uppercase tracking-wide text-slate-500">{k}</div>
      <div className="text-slate-200">{v ?? "—"}</div>
    </div>
  );
}

/** Relative time helper. Returns "12s ago" / "3m ago" / "1h ago" / a full date. */
export function relTime(d: Date): string {
  const ms = Date.now() - d.getTime();
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return d.toLocaleString();
}
