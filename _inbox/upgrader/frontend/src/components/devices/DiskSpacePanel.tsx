import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  HardDrive,
  Loader2,
  RefreshCw,
  Trash2,
  Package,
} from "lucide-react";
import { api } from "@/lib/api";
import type { Device, DiskSpaceRow } from "@/lib/types";

/**
 * Disk-space self-help panel inside the expanded device row.
 *
 * Two layers:
 *   - top: live `df -h` output from the device, with a colored bar per row
 *     so a >85% partition jumps out.
 *   - bottom: list of PAN-OS images currently on the device (from the cached
 *     `downloaded_versions`), each with a Delete button. We refuse to delete
 *     the running version on the server too — but the button is also hidden
 *     for safety.
 *
 * `df -h` is a slow op (multi-second round-trip), so it's lazy: only fired
 * when the user clicks "Check disk space." Image deletion auto-refreshes
 * both the disk readout and the device row.
 */
export function DiskSpacePanel({ d }: { d: Device }) {
  const qc = useQueryClient();
  const [shown, setShown] = useState(false);

  const dfQ = useQuery<DiskSpaceRow[]>({
    queryKey: ["disk-space", d.id],
    queryFn: () => api(`/api/devices/${d.id}/disk-space`),
    enabled: shown,
    staleTime: 15_000,
  });

  const del = useMutation({
    mutationFn: (version: string) =>
      api(`/api/devices/${d.id}/software/delete`, {
        method: "POST",
        body: { version },
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["disk-space", d.id] });
      qc.invalidateQueries({ queryKey: ["devices"] });
    },
  });

  // Anything we'd plausibly want to free: every downloaded image that isn't
  // currently running. (Staged is included — operator can decide if they
  // want it gone too.)
  const removable = (d.downloaded_versions ?? []).filter((v) => v !== d.current_version);

  return (
    <div className="mt-4 rounded-md border border-slate-800 bg-slate-950 p-3">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm">
          <HardDrive className="h-4 w-4 text-cyan-400" />
          <span className="font-medium">Disk space</span>
          <span className="text-xs text-slate-500">
            {removable.length === 0
              ? "no removable images on device"
              : `${removable.length} removable image${removable.length === 1 ? "" : "s"}`}
          </span>
        </div>
        <button
          onClick={() => {
            setShown(true);
            dfQ.refetch();
          }}
          disabled={dfQ.isFetching}
          className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-0.5 text-xs hover:bg-slate-800 disabled:opacity-50"
        >
          {dfQ.isFetching ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : (
            <RefreshCw className="h-3 w-3" />
          )}
          {shown ? "Refresh" : "Check disk space"}
        </button>
      </div>

      {shown && dfQ.data && dfQ.data.length > 0 && (
        <div className="space-y-1">
          {dfQ.data.map((r) => {
            const pct = Number.parseInt(r.use_pct, 10) || 0;
            const tight = pct >= 85;
            const warn = pct >= 70 && pct < 85;
            const barColor = tight
              ? "bg-red-500"
              : warn
                ? "bg-amber-500"
                : "bg-emerald-500";
            return (
              <div key={r.filesystem + r.mounted_on} className="text-xs">
                <div className="flex items-center justify-between text-slate-300">
                  <span className="font-mono">
                    {r.mounted_on}{" "}
                    <span className="text-slate-500">({r.filesystem})</span>
                  </span>
                  <span
                    className={`tabular-nums ${
                      tight ? "text-red-300" : warn ? "text-amber-300" : "text-slate-400"
                    }`}
                  >
                    {r.used} / {r.size} · {pct}%
                  </span>
                </div>
                <div className="mt-0.5 h-1 overflow-hidden rounded bg-slate-800">
                  <div className={`h-full ${barColor}`} style={{ width: `${Math.min(100, pct)}%` }} />
                </div>
              </div>
            );
          })}
        </div>
      )}
      {shown && dfQ.isError && (
        <div className="text-xs text-red-400">Failed to query disk space.</div>
      )}

      {removable.length > 0 && (
        <div className="mt-3 border-t border-slate-800 pt-3">
          <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
            Downloaded images
          </div>
          <div className="flex flex-wrap gap-2">
            {(d.downloaded_versions ?? []).map((v) => {
              const isCurrent = v === d.current_version;
              const isStaged = v === d.staged_version;
              return (
                <div
                  key={v}
                  className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs ${
                    isCurrent
                      ? "border-emerald-700 bg-emerald-950/30 text-emerald-300"
                      : isStaged
                        ? "border-sky-700 bg-sky-950/30 text-sky-300"
                        : "border-slate-700 bg-slate-900 text-slate-300"
                  }`}
                >
                  <Package className="h-3 w-3" />
                  <span className="font-mono">{v}</span>
                  {isCurrent && (
                    <span className="text-[10px] uppercase tracking-wide opacity-70">running</span>
                  )}
                  {isStaged && !isCurrent && (
                    <span className="text-[10px] uppercase tracking-wide opacity-70">staged</span>
                  )}
                  {!isCurrent && (
                    <button
                      onClick={() => {
                        if (
                          window.confirm(
                            `Delete PAN-OS ${v} from ${d.name}?\n\nFrees disk space. The running version (${d.current_version ?? "unknown"}) is not affected.`,
                          )
                        ) {
                          del.mutate(v);
                        }
                      }}
                      disabled={del.isPending}
                      className="ml-1 inline-flex items-center gap-0.5 rounded p-0.5 text-slate-400 hover:bg-slate-800 hover:text-red-400 disabled:opacity-50"
                      title={`Delete ${v}`}
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  )}
                </div>
              );
            })}
          </div>
          {del.isError && (
            <div className="mt-1 text-xs text-red-400">
              Delete failed: {(del.error as Error).message}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
