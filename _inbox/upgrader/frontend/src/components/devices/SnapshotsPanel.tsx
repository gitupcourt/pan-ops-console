import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Camera,
  CheckCircle2,
  ChevronRight,
  Loader2,
  XCircle,
} from "lucide-react";
import { api } from "@/lib/api";
import type { SnapshotDiff, SnapshotSummary } from "@/lib/types";

/**
 * Compact snapshots browser inside the expanded device row.
 *
 * Lists the most recent snapshots for the device. Pre/post pairs that have
 * a computed diff get a "View diff" link that opens the SnapshotDiffViewer
 * modal. Lazy-loaded — we only fetch the list when the parent row is
 * expanded, so the Devices table stays cheap on render.
 */
export function SnapshotsPanel({
  deviceId,
  onViewDiff,
}: {
  deviceId: number;
  onViewDiff: (diff: SnapshotDiff) => void;
}) {
  const snapshotsQ = useQuery<SnapshotSummary[]>({
    queryKey: ["snapshots", deviceId],
    queryFn: () => api(`/api/devices/${deviceId}/snapshots?limit=20`),
    // Cheap and changes infrequently — re-fetch on focus is enough.
    staleTime: 30_000,
  });

  const [openId, setOpenId] = useState<number | null>(null);

  if (snapshotsQ.isLoading) {
    return (
      <div className="mt-4 flex items-center gap-2 text-sm text-slate-500">
        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading snapshots…
      </div>
    );
  }
  const rows = snapshotsQ.data ?? [];
  if (rows.length === 0) {
    return (
      <div className="mt-4 rounded-md border border-slate-800 bg-slate-950 p-3 text-sm text-slate-500">
        <div className="mb-1 flex items-center gap-2 text-slate-400">
          <Camera className="h-4 w-4 text-fuchsia-400" />
          <span className="font-medium">Snapshots</span>
        </div>
        No snapshots yet — the orchestrator captures pre/post snapshots
        during upgrades.
      </div>
    );
  }

  return (
    <div className="mt-4 rounded-md border border-slate-800 bg-slate-950 p-3">
      <div className="mb-2 flex items-center gap-2 text-sm">
        <Camera className="h-4 w-4 text-fuchsia-400" />
        <span className="font-medium">Snapshots</span>
        <span className="text-xs text-slate-500">· {rows.length} recent</span>
      </div>
      <div className="space-y-1">
        {rows.map((s) => (
          <SnapshotRow
            key={s.id}
            snap={s}
            isOpen={openId === s.id}
            onToggle={() => setOpenId((o) => (o === s.id ? null : s.id))}
            onViewDiff={onViewDiff}
          />
        ))}
      </div>
    </div>
  );
}

function SnapshotRow({
  snap,
  isOpen,
  onToggle,
  onViewDiff,
}: {
  snap: SnapshotSummary;
  isOpen: boolean;
  onToggle: () => void;
  onViewDiff: (diff: SnapshotDiff) => void;
}) {
  // Look up the diff that this snapshot is the *right* side of (i.e. it's a
  // post-snapshot with a pre-snapshot counterpart). We only do this when the
  // row is expanded to avoid an N+1 on the list view.
  const diffQ = useQuery<SnapshotDiff | null>({
    queryKey: ["snapshot-diff-by-task", snap.task_id],
    queryFn: () =>
      snap.task_id == null
        ? Promise.resolve(null)
        : api(`/api/snapshots/diffs/by-task/${snap.task_id}`),
    enabled: isOpen && snap.task_id != null,
    staleTime: 60_000,
  });

  const kindLabel =
    snap.kind === "pre_upgrade"
      ? "Pre-upgrade"
      : snap.kind === "post_upgrade"
        ? "Post-upgrade"
        : "Ad-hoc";

  const kindColor =
    snap.kind === "pre_upgrade"
      ? "text-amber-300"
      : snap.kind === "post_upgrade"
        ? "text-emerald-300"
        : "text-slate-300";

  return (
    <div className="rounded border border-slate-800/50">
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left text-xs text-slate-400 hover:bg-slate-900/40"
      >
        <ChevronRight
          className={`h-3 w-3 transition-transform ${isOpen ? "rotate-90" : ""}`}
        />
        <span className={`font-medium ${kindColor}`}>{kindLabel}</span>
        <span className="text-slate-300">{new Date(snap.taken_at).toLocaleString()}</span>
        {snap.pan_os_version && (
          <span className="text-slate-500">· {snap.pan_os_version}</span>
        )}
        {snap.error ? (
          <span className="ml-auto inline-flex items-center gap-1 text-red-400">
            <XCircle className="h-3 w-3" /> capture failed
          </span>
        ) : (
          <span className="ml-auto text-slate-500">
            {snap.areas.length} areas
          </span>
        )}
      </button>
      {isOpen && (
        <div className="space-y-2 border-t border-slate-800/50 px-3 py-2 text-xs">
          {snap.error && (
            <div className="rounded border border-red-800 bg-red-950/40 px-2 py-1 text-red-300">
              {snap.error}
            </div>
          )}
          {snap.areas.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {snap.areas.map((a) => (
                <span
                  key={a}
                  className="rounded border border-slate-700 bg-slate-900 px-1.5 py-0.5 text-slate-300"
                >
                  {a}
                </span>
              ))}
            </div>
          )}
          {snap.task_id != null && (
            <div className="text-slate-500">From job task #{snap.task_id}</div>
          )}
          {diffQ.isLoading && (
            <div className="flex items-center gap-1 text-slate-500">
              <Loader2 className="h-3 w-3 animate-spin" /> loading diff…
            </div>
          )}
          {diffQ.data && (
            <div className="flex items-center gap-2">
              {diffQ.data.all_passed ? (
                <span className="inline-flex items-center gap-1 text-emerald-400">
                  <CheckCircle2 className="h-3.5 w-3.5" /> diff: all areas passed
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 text-amber-400">
                  <XCircle className="h-3.5 w-3.5" />
                  diff: changes in {diffQ.data.failing_areas}
                </span>
              )}
              <button
                onClick={() => diffQ.data && onViewDiff(diffQ.data)}
                className="rounded border border-slate-700 px-2 py-0.5 text-slate-300 hover:bg-slate-800"
              >
                View diff
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
