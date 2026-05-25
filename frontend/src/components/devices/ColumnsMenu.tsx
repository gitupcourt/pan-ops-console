import { useEffect, useRef, useState } from "react";
import { Columns3, Check } from "lucide-react";

/**
 * Columns picker for the Devices table.
 *
 * Required columns (checkbox, expander, Name, Actions) are always
 * visible — they're the controls the operator needs to actually use the
 * row. Everything else is toggleable. We track the HIDDEN set in the
 * URL (`?hide=ts,dg`) rather than the visible set so URLs stay short
 * when nothing is hidden, which is the common case.
 *
 * The dropdown closes on outside-click + Escape so it doesn't trap
 * keyboard focus on a page that's mostly table.
 */

export type OptionalColumnKey =
  | "model"
  | "panos"
  | "ha"
  | "dg"
  | "ts"
  | "status"
  | "precheck";

export const OPTIONAL_COLUMNS: { key: OptionalColumnKey; label: string }[] = [
  { key: "model", label: "Model" },
  { key: "panos", label: "PAN-OS" },
  { key: "ha", label: "HA" },
  { key: "dg", label: "Device Group" },
  { key: "ts", label: "Template Stack" },
  { key: "status", label: "Status" },
  { key: "precheck", label: "Pre-check" },
];

export function ColumnsMenu({
  hidden,
  onChange,
  hasCustomWidths,
  onAutoFit,
  onResetWidths,
}: {
  hidden: Set<OptionalColumnKey>;
  onChange: (next: Set<OptionalColumnKey>) => void;
  // Whether any column has been manually resized. Drives whether the
  // Reset-widths action is offered (no point showing it when defaults
  // are already in place).
  hasCustomWidths: boolean;
  // Snapshot every column's current rendered width and pin them. Useful
  // before starting to drag-resize so other columns don't silently
  // redistribute.
  onAutoFit: () => void;
  // Clear all custom widths, return to browser auto-layout.
  onResetWidths: () => void;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const toggle = (key: OptionalColumnKey) => {
    const next = new Set(hidden);
    next.has(key) ? next.delete(key) : next.add(key);
    onChange(next);
  };

  const visibleCount = OPTIONAL_COLUMNS.length - hidden.size;

  return (
    <div className="relative" ref={wrap}>
      <button
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 px-3 py-2 text-xs font-medium text-slate-300 hover:bg-slate-800"
        title="Show / hide table columns"
      >
        <Columns3 className="h-3.5 w-3.5" />
        Columns
        <span className="text-slate-500">
          ({visibleCount}/{OPTIONAL_COLUMNS.length})
        </span>
      </button>
      {open && (
        <div className="absolute right-0 z-30 mt-1 w-56 rounded-md border border-slate-700 bg-slate-900 shadow-xl">
          <div className="border-b border-slate-800 px-3 py-2 text-xs text-slate-500">
            Toggle optional columns. Name, checkbox, and actions are always
            shown.
          </div>
          <div className="max-h-72 overflow-y-auto py-1">
            {OPTIONAL_COLUMNS.map(({ key, label }) => {
              const visible = !hidden.has(key);
              return (
                <button
                  key={key}
                  onClick={() => toggle(key)}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-slate-800"
                >
                  <span
                    className={`flex h-4 w-4 items-center justify-center rounded border ${
                      visible
                        ? "border-indigo-500 bg-indigo-600 text-white"
                        : "border-slate-600"
                    }`}
                  >
                    {visible && <Check className="h-3 w-3" />}
                  </span>
                  <span className={visible ? "text-slate-200" : "text-slate-500"}>
                    {label}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="border-t border-slate-800 px-3 py-1.5">
            <div className="mb-1 text-xs uppercase tracking-wide text-slate-500">
              Width
            </div>
            <button
              onClick={onAutoFit}
              className="block w-full text-left text-xs text-indigo-400 hover:underline"
              title="Snapshot each column's current rendered width and pin them. Do this before drag-resizing so other columns don't silently redistribute."
            >
              Auto-fit columns to content
            </button>
            {hasCustomWidths && (
              <button
                onClick={onResetWidths}
                className="mt-1 block w-full text-left text-xs text-indigo-400 hover:underline"
              >
                Reset to default widths
              </button>
            )}
          </div>
          {hidden.size > 0 && (
            <div className="border-t border-slate-800 px-3 py-1.5">
              <button
                onClick={() => onChange(new Set())}
                className="text-xs text-indigo-400 hover:underline"
              >
                Show all columns
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
