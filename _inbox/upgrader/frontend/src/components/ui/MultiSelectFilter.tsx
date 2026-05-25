import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, X } from "lucide-react";

/**
 * Generic dropdown filter with checkboxes. Used for device-group, template-stack,
 * and pre-check severity filters on the Devices page; reusable elsewhere.
 *
 * Props are intentionally minimal so callers don't have to bring a state library:
 * pass selected values + onChange, get back a filter that closes on outside-click.
 */
export function MultiSelectFilter({
  label, options, selected, onChange,
}: {
  label: string;
  options: { value: string; label: string }[];
  selected: string[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside-click. Only register the listener while open to avoid
  // unnecessary global handler traffic.
  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  const toggle = (v: string) => {
    if (selected.includes(v)) onChange(selected.filter((x) => x !== v));
    else onChange([...selected, v]);
  };

  const summary =
    selected.length === 0
      ? `All ${label.toLowerCase()}`
      : selected.length === 1
        ? options.find((o) => o.value === selected[0])?.label ?? selected[0]
        : `${selected.length} selected`;

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 rounded-md border border-slate-700 bg-slate-950 px-3 py-2 text-sm hover:bg-slate-900"
      >
        <span className="text-slate-400">{label}:</span>
        <span className="text-slate-200">{summary}</span>
        {selected.length > 0 && (
          <X
            className="h-3 w-3 text-slate-500 hover:text-slate-200"
            onClick={(e) => { e.stopPropagation(); onChange([]); }}
          />
        )}
        <ChevronDown className={`h-3 w-3 text-slate-500 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-20 mt-1 max-h-72 w-56 overflow-y-auto rounded-md border border-slate-700 bg-slate-900 py-1 shadow-lg">
          {options.length === 0 ? (
            <div className="px-3 py-2 text-xs text-slate-500">No options available.</div>
          ) : (
            options.map((o) => {
              const checked = selected.includes(o.value);
              return (
                <button
                  key={o.value}
                  onClick={() => toggle(o.value)}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm text-slate-200 hover:bg-slate-800"
                >
                  <span className={`flex h-4 w-4 items-center justify-center rounded border ${
                    checked ? "border-indigo-500 bg-indigo-600" : "border-slate-600"
                  }`}>
                    {checked && <Check className="h-3 w-3 text-white" />}
                  </span>
                  {o.label}
                </button>
              );
            })
          )}
          {selected.length > 0 && (
            <div className="mt-1 border-t border-slate-800 px-2 pt-1">
              <button
                onClick={() => onChange([])}
                className="rounded px-2 py-0.5 text-xs text-slate-400 hover:bg-slate-800"
              >
                Clear
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
