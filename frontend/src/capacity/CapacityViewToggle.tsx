import { useNavigate, useSearchParams } from "react-router-dom";

/**
 * Heat Map / Table view toggle for the Capacity Analyzer.
 *
 * Mounted at the top of both `/capacity` (heat-map view) and
 * `/capacity/table` (table view) so the operator can flip between
 * layouts without clicking through a tile to switch.
 *
 * Toggling navigates between the two routes; URL search params
 * (model, metric, device_group, template_stack, sort, dir) are
 * preserved so filter context survives the view switch. Heat-map-only
 * UI state (Capacity Filter slider, color scheme) is local to that
 * view and doesn't carry over — that's intentional, since those
 * controls don't have a meaning in the table view.
 */
export function CapacityViewToggle({ view }: { view: "heatmap" | "table" }) {
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const go = (next: "heatmap" | "table") => {
    if (next === view) return;
    const path = next === "heatmap" ? "/capacity" : "/capacity/table";
    const qs = params.toString();
    navigate(qs ? `${path}?${qs}` : path);
  };

  return (
    <div className="inline-flex border border-zinc-700 rounded overflow-hidden text-[11px]">
      <button
        onClick={() => go("heatmap")}
        className={
          "px-3 py-1 " +
          (view === "heatmap"
            ? "bg-zinc-700 text-zinc-100"
            : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
        }
      >
        Heat Map
      </button>
      <button
        onClick={() => go("table")}
        className={
          "px-3 py-1 " +
          (view === "table"
            ? "bg-zinc-700 text-zinc-100"
            : "bg-zinc-900 text-zinc-400 hover:text-zinc-200")
        }
      >
        Table
      </button>
    </div>
  );
}
