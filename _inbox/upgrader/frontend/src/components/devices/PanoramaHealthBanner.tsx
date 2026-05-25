import { AlertTriangle } from "lucide-react";
import type { Panorama } from "@/lib/types";

/**
 * Top-of-page amber callout that surfaces any Panoramas the backend currently
 * reports as unreachable. Hidden entirely when all configured Panoramas are
 * healthy. Renders nothing for "never-checked" state — only false.
 */
export function PanoramaHealthBanner({ panoramas }: { panoramas: Panorama[] }) {
  const down = panoramas.filter((p) => p.reachable === false);
  if (down.length === 0) return null;
  return (
    <div className="rounded-md border border-amber-700 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-400" />
        <div className="flex-1">
          <div className="font-medium">
            {down.length === 1 ? "Panorama unreachable" : `${down.length} Panoramas unreachable`}
          </div>
          <ul className="mt-1 list-disc pl-5 text-xs text-amber-300">
            {down.map((p) => (
              <li key={p.id}>
                <span className="font-mono">{p.name}</span>
                {p.last_reachability_error && (
                  <> — {p.last_reachability_error}</>
                )}
              </li>
            ))}
          </ul>
          <div className="mt-1 text-xs">
            Device list <span className="text-amber-100">Refresh</span> won't have current state
            from these Panoramas. Per-device <span className="text-amber-100">Probe</span> will fall
            back to direct connection where possible.
          </div>
        </div>
      </div>
    </div>
  );
}
