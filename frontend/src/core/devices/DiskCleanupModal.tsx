import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, DiskCleanupResult, DiskSpaceRow } from "../../api";
import { Button } from "../ui/ui";

// "Additional cleanup options" KB — pointed to for deeper (manual) cleaning
// once the safe automated pass isn't enough (logs, cores, etc.).
const KB_URL =
  "https://knowledgebase.paloaltonetworks.com/KCSArticleDetail?id=kA10g000000ClaJCAS";

function fmtKb(kb?: string | number | null): string {
  const n = typeof kb === "number" ? kb : Number(kb);
  if (kb == null || kb === "" || Number.isNaN(n)) return "?";
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} GB`;
  if (n >= 1024) return `${(n / 1024).toFixed(0)} MB`;
  return `${n} KB`;
}

function totalKb(images: { size_kb: string | null }[]): number {
  return images.reduce((s, i) => s + (Number(i.size_kb) || 0), 0);
}

function DiskBars({ rows }: { rows: DiskSpaceRow[] }) {
  const sorted = [...rows].sort(
    (a, b) => (Number(b.use_pct) || 0) - (Number(a.use_pct) || 0),
  );
  return (
    <div className="space-y-1.5">
      {sorted.map((r) => {
        const pct = Number(r.use_pct) || 0;
        const tone =
          pct >= 90 ? "bg-rose-500" : pct >= 75 ? "bg-amber-500" : "bg-emerald-500";
        return (
          <div key={`${r.filesystem}-${r.mounted_on}`} className="text-[11px]">
            <div className="flex justify-between text-zinc-400">
              <span className="font-mono">{r.mounted_on}</span>
              <span>
                {r.used}/{r.size} ({r.use_pct}%) · {r.avail} free
              </span>
            </div>
            <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded bg-zinc-800">
              <div
                className={`h-full ${tone}`}
                style={{ width: `${Math.min(Math.max(pct, 0), 100)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Confirm-then-clean disk-space dialog. Opens with a dry-run plan (current
 * `df` usage + the old-train images we'd delete), lets the operator pick which
 * to remove (all by default), then runs the destructive cleanup and shows
 * before/after. Conservative scope only — see services/disk_cleanup.py.
 */
export function DiskCleanupModal({
  deviceId,
  deviceName,
  onClose,
  onDone,
}: {
  deviceId: number;
  deviceName: string;
  onClose: () => void;
  onDone?: () => void;
}) {
  const planQ = useQuery({
    queryKey: ["disk-cleanup-plan", deviceId],
    queryFn: () => api.getDiskCleanupPlan(deviceId),
    // Always re-read the device on open, and never keep the result cached
    // after the modal closes — otherwise reopening after a cleanup shows the
    // just-deleted images + stale disk usage until a full page refresh.
    staleTime: 0,
    gcTime: 0,
  });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<DiskCleanupResult | null>(null);

  // Default-select every deletable image once the plan arrives.
  useEffect(() => {
    if (planQ.data) {
      setSelected(new Set(planQ.data.deletable_images.map((i) => i.version)));
    }
  }, [planQ.data]);

  const run = useMutation({
    mutationFn: () => api.runDiskCleanup(deviceId, Array.from(selected)),
    onSuccess: (r) => {
      setResult(r);
      onDone?.();
    },
  });

  const plan = planQ.data;
  const deletable = plan?.deletable_images ?? [];
  const selectedImages = deletable.filter((i) => selected.has(i.version));

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-xl max-h-[85vh] overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-start justify-between border-b border-zinc-800 bg-zinc-950 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-zinc-100">
              Free up disk space
            </div>
            <div className="text-[11px] text-zinc-500">{deviceName}</div>
          </div>
          <button
            onClick={onClose}
            className="text-sm text-zinc-500 hover:text-zinc-200"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        <div className="space-y-4 p-4 text-xs">
          {planQ.isLoading && (
            <div className="text-zinc-500">
              Reading disk usage and software inventory…
            </div>
          )}
          {planQ.error && (
            <div className="text-rose-400">
              Couldn&apos;t read disk state: {(planQ.error as Error).message}
            </div>
          )}

          {plan && !result && (
            <>
              {planQ.isFetching && (
                <div className="text-[11px] text-blue-300">
                  Re-reading disk usage…
                </div>
              )}
              {plan.disk_space.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">
                    Current disk usage
                  </div>
                  <DiskBars rows={plan.disk_space} />
                </div>
              )}

              <div>
                <div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">
                  Reclaimable software images
                  {plan.current_version
                    ? ` (older than ${plan.current_version} — running version & train base kept)`
                    : ""}
                </div>
                {deletable.length === 0 ? (
                  <div className="rounded border border-zinc-800 bg-zinc-900/40 p-2 text-zinc-400">
                    No older downloaded images to remove on this device — the
                    running version, the current train&apos;s base, and any
                    newer pre-staged image are all kept. If the disk is filled by
                    logs/temp instead, see the{" "}
                    <a
                      href={KB_URL}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-400 underline hover:text-blue-300"
                    >
                      PAN cleanup KB
                    </a>
                    .
                  </div>
                ) : (
                  <div className="space-y-1">
                    {deletable.map((i) => (
                      <label
                        key={i.version}
                        className="flex cursor-pointer items-center gap-2 rounded border border-zinc-800 bg-zinc-900/40 px-2 py-1"
                      >
                        <input
                          type="checkbox"
                          checked={selected.has(i.version)}
                          onChange={(e) =>
                            setSelected((prev) => {
                              const n = new Set(prev);
                              if (e.target.checked) n.add(i.version);
                              else n.delete(i.version);
                              return n;
                            })
                          }
                        />
                        <span className="font-mono text-zinc-200">
                          {i.version}
                        </span>
                        {i.release_type === "Base" && (
                          <span className="rounded border border-zinc-700 px-1 text-[9px] uppercase text-zinc-400">
                            base
                          </span>
                        )}
                        <span className="ml-auto text-zinc-500">
                          {fmtKb(i.size_kb)}
                        </span>
                      </label>
                    ))}
                    <div className="text-[11px] text-zinc-500">
                      {selectedImages.length} selected · ~
                      {fmtKb(totalKb(selectedImages))} reclaimable. Logs and
                      config are never touched.
                    </div>
                  </div>
                )}
              </div>

              {run.error && (
                <div className="text-rose-400">
                  Cleanup failed: {(run.error as Error).message}
                </div>
              )}

              <div className="flex justify-end gap-2 border-t border-zinc-800 pt-3">
                <Button onClick={onClose}>Cancel</Button>
                <Button
                  variant="danger"
                  disabled={selectedImages.length === 0 || run.isPending}
                  onClick={() => run.mutate()}
                >
                  {run.isPending
                    ? "Deleting…"
                    : `Delete ${selectedImages.length} old image${
                        selectedImages.length === 1 ? "" : "s"
                      }`}
                </Button>
              </div>
            </>
          )}

          {result && (
            <div className="space-y-3">
              <div className="rounded border border-emerald-900/40 bg-emerald-950/30 p-2 text-emerald-200">
                Done.
                {result.deleted.length > 0
                  ? ` Deleted ${result.deleted.length} image${
                      result.deleted.length === 1 ? "" : "s"
                    }.`
                  : " Nothing was removed."}
              </div>
              {result.deleted.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] uppercase text-zinc-500">
                    Deleted
                  </div>
                  <ul className="space-y-0.5 font-mono text-emerald-200">
                    {result.deleted.map((v) => (
                      <li key={v}>− {v}</li>
                    ))}
                  </ul>
                </div>
              )}
              {result.failed.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] uppercase text-rose-400/80">
                    Skipped / failed
                  </div>
                  <ul className="space-y-0.5 text-rose-300">
                    {result.failed.map((f) => (
                      <li key={f.version}>
                        <span className="font-mono">{f.version}</span>: {f.error}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {result.disk_space_after.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] uppercase text-zinc-500">
                    Disk usage after
                  </div>
                  <DiskBars rows={result.disk_space_after} />
                </div>
              )}
              <div className="text-[11px] text-zinc-500">
                Need more space (logs, cores, snapshots)? See the{" "}
                <a
                  href={KB_URL}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-400 underline hover:text-blue-300"
                >
                  PAN cleanup KB
                </a>
                .
              </div>
              <div className="flex justify-end gap-2 border-t border-zinc-800 pt-3">
                <Button
                  onClick={() => {
                    // Re-check this device and go back to the plan view —
                    // no close/reopen or page refresh needed.
                    setResult(null);
                    setSelected(new Set());
                    planQ.refetch();
                  }}
                >
                  Scan again
                </Button>
                <Button variant="primary" onClick={onClose}>
                  Close
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
