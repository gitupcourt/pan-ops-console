import { CheckCircle2, X, XCircle } from "lucide-react";
import type { SnapshotAreaReport, SnapshotDiff } from "@/lib/types";

/**
 * Modal that renders the pan-os-upgrade-assurance compare report.
 *
 * The library returns one entry per snapshot area; per-area shape varies by
 * comparison type (set-difference, dict-diff, route-table, metric values).
 * Rather than try to render every shape pixel-perfect, we show:
 *   - a top status (pass/fail per area)
 *   - the raw added/missing/changed keys when present
 *   - a JSON dump as a fallback for everything else
 *
 * That gives the operator the information they need without pretending the
 * upgrade tool has more semantic structure than it does.
 */
export function SnapshotDiffViewer({
  diff,
  onClose,
}: {
  diff: SnapshotDiff;
  onClose: () => void;
}) {
  const areas = Object.entries(diff.report);
  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/70 p-4"
      onClick={onClose}
    >
      <div
        className="my-8 w-full max-w-5xl rounded-lg border border-slate-700 bg-slate-900 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between border-b border-slate-800 px-5 py-4">
          <div>
            <div className="flex items-center gap-2 text-base font-semibold text-white">
              Snapshot diff
              {diff.all_passed ? (
                <span className="inline-flex items-center gap-1 rounded border border-emerald-700 bg-emerald-950/40 px-1.5 py-0.5 text-xs text-emerald-300">
                  <CheckCircle2 className="h-3 w-3" /> all areas passed
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded border border-amber-700 bg-amber-950/40 px-1.5 py-0.5 text-xs text-amber-300">
                  <XCircle className="h-3 w-3" /> {diff.failing_areas}
                </span>
              )}
            </div>
            <div className="mt-1 text-xs text-slate-400">
              <span className="text-amber-300">pre</span>{" "}
              {new Date(diff.left.taken_at).toLocaleString()}{" "}
              {diff.left.pan_os_version && `(${diff.left.pan_os_version})`}
              {" → "}
              <span className="text-emerald-300">post</span>{" "}
              {new Date(diff.right.taken_at).toLocaleString()}{" "}
              {diff.right.pan_os_version && `(${diff.right.pan_os_version})`}
            </div>
          </div>
          <button
            onClick={onClose}
            className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-200"
            aria-label="Close"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[70vh] space-y-3 overflow-y-auto p-5">
          {areas.length === 0 && (
            <div className="text-sm text-slate-400">
              No comparable areas in the report.
            </div>
          )}
          {areas.map(([name, body]) => (
            <AreaCard key={name} name={name} body={body} />
          ))}
        </div>
      </div>
    </div>
  );
}

function AreaCard({ name, body }: { name: string; body: SnapshotAreaReport }) {
  // Most area entries are an object with sub-keys per comparison aspect
  // (added/missing/changed). Some (e.g. session_stats) just have a top-level
  // `passed` boolean. We surface whatever's there.
  const subEntries = Object.entries(body || {}).filter(
    ([k]) => k !== "passed",
  );
  const passed = body?.passed !== false;
  return (
    <div
      className={`rounded border ${
        passed ? "border-slate-800" : "border-amber-800"
      } bg-slate-950`}
    >
      <div
        className={`flex items-center gap-2 border-b px-3 py-2 text-sm ${
          passed
            ? "border-slate-800 text-slate-300"
            : "border-amber-800 bg-amber-950/20 text-amber-300"
        }`}
      >
        {passed ? (
          <CheckCircle2 className="h-4 w-4 text-emerald-400" />
        ) : (
          <XCircle className="h-4 w-4 text-amber-400" />
        )}
        <span className="font-mono text-xs uppercase tracking-wide">
          {name}
        </span>
      </div>
      <div className="space-y-2 px-3 py-2 text-xs">
        {subEntries.length === 0 && (
          <div className="text-slate-500">No further detail in the report.</div>
        )}
        {subEntries.map(([aspect, val]) => (
          <Aspect key={aspect} aspect={aspect} value={val} />
        ))}
      </div>
    </div>
  );
}

function Aspect({ aspect, value }: { aspect: string; value: unknown }) {
  // Pick out the common shapes for prettier rendering, fall back to JSON.
  if (value && typeof value === "object" && !Array.isArray(value)) {
    const obj = value as Record<string, unknown>;
    const keysAdded = (obj.added_keys ?? obj.passed_keys) as string[] | undefined;
    const keysMissing = obj.missing_keys as string[] | undefined;
    const changed = (obj.changed_raw ?? obj.changed) as Record<string, unknown> | undefined;
    const passed = obj.passed as boolean | undefined;

    return (
      <div>
        <div className="flex items-center gap-2 text-slate-400">
          <span className="font-medium text-slate-300">{aspect}</span>
          {passed === true && (
            <span className="rounded bg-emerald-950/40 px-1 text-[10px] uppercase tracking-wide text-emerald-300">
              passed
            </span>
          )}
          {passed === false && (
            <span className="rounded bg-amber-950/40 px-1 text-[10px] uppercase tracking-wide text-amber-300">
              changed
            </span>
          )}
        </div>
        {keysAdded && keysAdded.length > 0 && (
          <KeyList label="Added" color="emerald" keys={keysAdded} />
        )}
        {keysMissing && keysMissing.length > 0 && (
          <KeyList label="Missing" color="red" keys={keysMissing} />
        )}
        {changed && Object.keys(changed).length > 0 && (
          <details className="mt-1">
            <summary className="cursor-pointer text-amber-300">
              {Object.keys(changed).length} changed entries
            </summary>
            <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-2 text-[11px] text-slate-300">
              {JSON.stringify(changed, null, 2)}
            </pre>
          </details>
        )}
        {/* Fallback: anything we didn't recognize. */}
        {!keysAdded && !keysMissing && !changed && (
          <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-slate-950 p-2 text-[11px] text-slate-400">
            {JSON.stringify(obj, null, 2)}
          </pre>
        )}
      </div>
    );
  }
  return (
    <div className="text-slate-400">
      <span className="font-medium text-slate-300">{aspect}: </span>
      <span className="font-mono text-slate-400">{String(value)}</span>
    </div>
  );
}

function KeyList({
  label,
  color,
  keys,
}: {
  label: string;
  color: "emerald" | "red";
  keys: string[];
}) {
  const cls =
    color === "emerald"
      ? "border-emerald-800 bg-emerald-950/30 text-emerald-300"
      : "border-red-800 bg-red-950/30 text-red-300";
  return (
    <div className="mt-1">
      <div className="text-slate-500">{label}:</div>
      <div className="mt-0.5 flex flex-wrap gap-1">
        {keys.slice(0, 80).map((k) => (
          <span
            key={k}
            className={`rounded border px-1.5 py-0.5 font-mono text-[11px] ${cls}`}
          >
            {k}
          </span>
        ))}
        {keys.length > 80 && (
          <span className="text-slate-500">… +{keys.length - 80} more</span>
        )}
      </div>
    </div>
  );
}
