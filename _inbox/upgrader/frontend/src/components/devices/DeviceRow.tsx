import {
  ChevronRight,
  Link2,
  Loader2,
  Package,
  Pencil,
  RefreshCw,
  ShieldCheck,
  Wifi,
  WifiOff,
  XCircle,
} from "lucide-react";
import type { ActiveTask, Device, PreCheckResult, SnapshotDiff } from "@/lib/types";
import { Detail } from "@/components/ui/Field";
import { PrecheckBadge, PrecheckPanel } from "./PrecheckPanel";
import { SnapshotsPanel } from "./SnapshotsPanel";
import { UpgradeBadge } from "./UpgradeBadge";
import type { OptionalColumnKey } from "./ColumnsMenu";
import { DiskSpacePanel } from "./DiskSpacePanel";
import { ConnectivityPanel } from "./ConnectivityPanel";

/**
 * One row in the Devices table, plus the matching expanded-detail row
 * (rendered as a fragment of two <tr>s — that's why this returns a Fragment).
 *
 * Quite a lot of UI per row: name, model, version + staged + downloaded
 * pills, HA, dev-group, template, connection status, pre-check badge,
 * per-row action buttons (Pre-check / Probe), plus an expandable detail
 * panel with all of the content versions, HA detail, source info, the
 * proxy-via-panorama toggle, and the expanded pre-check viewer.
 *
 * Keeping all of this in one component is intentional — the row and its
 * expansion are visually tightly coupled, and splitting would mean
 * threading the device prop through another layer just to render a
 * disclosure. Action callbacks come from the parent because they touch
 * page-level state (which row's pre-check is running, etc.).
 */
export function DeviceRow({
  d, isOpen, onToggle, onPrecheck, onProbe, onPatch, precheck, precheckRunning,
  isSelected, onToggleSelect, onViewDiff,
  haPeerName, pairAccentClass, pairPosition, onTogglePair, activeUpgrade, onEdit,
  hiddenCols, visibleColCount,
}: {
  d: Device;
  isOpen: boolean;
  onToggle: () => void;
  onPrecheck: () => void;
  onProbe: () => void;
  onPatch: (body: Partial<Device>) => Promise<void>;
  precheck: PreCheckResult | undefined;
  precheckRunning: boolean;
  isSelected: boolean;
  onToggleSelect: () => void;
  onViewDiff: (diff: SnapshotDiff) => void;
  // HA grouping visuals. When this row is part of an HA pair the parent
  // assigns a deterministic accent color shared with the peer, and tells us
  // which half of the pair we are (first/second/solo). The accent is a
  // 4px left border — readable without dominating the row.
  haPeerName: string | null;
  pairAccentClass: string;
  pairPosition: "first" | "second" | "solo";
  // Provided only for paired devices; selects both members at once.
  onTogglePair?: () => void;
  // If set, this device is currently part of a running upgrade job.
  activeUpgrade?: ActiveTask;
  // Open the Edit Device modal for this row.
  onEdit: () => void;
  // Set of optional column keys the user has hidden via the Columns menu.
  // We render <td>s conditionally so the row width matches the header.
  hiddenCols: Set<OptionalColumnKey>;
  // Total count of currently-visible columns — used for the expanded
  // detail row's colSpan so it always spans the full table width.
  visibleColCount: number;
}) {
  const dim = !d.connected;
  const isPaired = d.ha_peer_id != null && !!haPeerName;
  // Tiny helper so each optional <td> is one-line conditional. Keeps the
  // JSX readable next to the always-visible cells.
  const show = (k: OptionalColumnKey) => !hiddenCols.has(k);
  // Suppress the top border between adjacent pair members so the two rows
  // read as one visual unit. The accent left-border + same color makes the
  // grouping obvious.
  const borderTopClass = pairPosition === "second" ? "" : "border-t";
  const accentClass = pairAccentClass ? `border-l-4 ${pairAccentClass}` : "";
  return (
    <>
      <tr className={`${borderTopClass} ${accentClass} border-slate-800 hover:bg-slate-900/50 ${dim ? "opacity-50" : ""}`}>
        <td className="px-2 py-2">
          <div className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={isSelected}
              onChange={onToggleSelect}
              onClick={(e) => e.stopPropagation()}
            />
            {isPaired && onTogglePair && (
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  onTogglePair();
                }}
                className="rounded p-0.5 text-slate-500 hover:bg-slate-800 hover:text-slate-300"
                title={`Toggle selection of the whole HA pair (with ${haPeerName})`}
              >
                <Link2 className="h-3 w-3" />
              </button>
            )}
          </div>
        </td>
        <td className="px-2 py-2 text-slate-500 cursor-pointer" onClick={onToggle}>
          <ChevronRight className={`h-4 w-4 transition-transform ${isOpen ? "rotate-90" : ""}`} />
        </td>
        <td className="px-3 py-2 cursor-pointer" onClick={onToggle}>
          <div className="font-medium">{d.name}</div>
          <div className="text-xs text-slate-500">
            {d.ip_address ?? d.hostname}{d.serial ? ` · ${d.serial}` : ""}
          </div>
        </td>
        {show("model") && <td className="px-3 py-2">{d.model ?? "—"}</td>}
        {show("panos") && <td className="px-3 py-2">
          {d.current_version ?? "—"}
          {activeUpgrade && (
            <div>
              <UpgradeBadge t={activeUpgrade} />
            </div>
          )}
          {d.staged_version && d.staged_version !== d.current_version && (
            <div
              className="mt-0.5 inline-flex items-center gap-1 rounded border border-sky-700 bg-sky-950/40 px-1.5 text-xs text-sky-300"
              title={`Staged ${d.staged_at ? new Date(d.staged_at).toLocaleString() : ""}`}
            >
              <Package className="h-3 w-3" /> staged: {d.staged_version}
            </div>
          )}
          {(() => {
            // Downloaded images on the device that aren't the currently running
            // one — these are pre-staged or leftover from prior upgrades.
            const extras = (d.downloaded_versions ?? []).filter((v) => v !== d.current_version);
            if (extras.length === 0) return null;
            return (
              <div
                className="mt-0.5 inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-900 px-1.5 text-xs text-slate-400"
                title={`Already downloaded: ${extras.join(", ")}`}
              >
                <Package className="h-3 w-3" />
                {extras.length} downloaded
              </div>
            );
          })()}
          {d.staged_error && (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); alert(`Stage error on ${d.name}:\n\n${d.staged_error}`); }}
              className="mt-0.5 inline-flex items-center gap-1 rounded border border-red-800 bg-red-950/40 px-1.5 text-xs text-red-300 hover:bg-red-900/40"
              title="Click for full error"
            >
              <XCircle className="h-3 w-3" /> stage error
            </button>
          )}
        </td>}
        {show("ha") && <td className="px-3 py-2">
          {d.ha_role}
          {d.ha_state && <span className="ml-1 text-xs text-slate-500">({d.ha_state})</span>}
          {isPaired && (
            <div className="text-xs text-slate-500" title="HA peer device">
              <Link2 className="inline h-3 w-3 align-text-bottom" /> {haPeerName}
            </div>
          )}
        </td>}
        {show("dg") && <td className="px-3 py-2">{d.device_group ?? "—"}</td>}
        {show("ts") && <td className="px-3 py-2">{d.template_stack ?? "—"}</td>}
        {show("status") && <td className="px-3 py-2">
          {d.connected ? (
            <span className="inline-flex items-center gap-1 text-emerald-400"><Wifi className="h-3.5 w-3.5" />connected</span>
          ) : (
            <span className="inline-flex items-center gap-1 text-slate-500"><WifiOff className="h-3.5 w-3.5" />offline</span>
          )}
        </td>}
        {show("precheck") && <td className="px-3 py-2"><PrecheckBadge summary={d.latest_precheck} /></td>}
        <td className="px-3 py-2 text-right">
          <div className="inline-flex gap-1">
            <button
              onClick={onPrecheck}
              disabled={precheckRunning}
              className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800 disabled:opacity-50"
              title="Run readiness pre-checks"
            >
              {precheckRunning ? <Loader2 className="h-3 w-3 animate-spin" /> : <ShieldCheck className="h-3 w-3" />}
              Pre-check
            </button>
            <button
              onClick={onProbe}
              className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800"
              title={
                d.source === "direct"
                  ? "Query this device directly for live state"
                  : "Query this device directly (via Panorama target-serial) for live state. Bypasses Panorama's cached managed-device view."
              }
            >
              <RefreshCw className="h-3 w-3" />
              Probe
            </button>
            <button
              onClick={onEdit}
              className="inline-flex items-center gap-1 rounded border border-slate-700 px-2 py-1 text-xs hover:bg-slate-800"
              title="Edit name, hostname, mgmt IP, credential, TLS, or Panorama proxy"
            >
              <Pencil className="h-3 w-3" />
              Edit
            </button>
          </div>
        </td>
      </tr>
      {isOpen && (
        <tr className="border-t border-slate-800 bg-slate-950/40">
          <td colSpan={visibleColCount} className="px-6 py-4">
            <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm md:grid-cols-3 lg:grid-cols-4">
              <Detail k="Apps" v={d.app_version} />
              <Detail k="Threats" v={d.threat_version} />
              <Detail k="Antivirus" v={d.av_version} />
              <Detail k="WildFire" v={d.wildfire_version} />
              <Detail k="URL filtering" v={d.url_filtering_version} />
              <Detail k="GP client" v={d.gp_client_version} />
              <Detail k="Uptime" v={d.uptime} />
              <Detail k="HA sync" v={d.ha_sync_state} />
              <Detail k="Last seen" v={d.last_seen_at ? new Date(d.last_seen_at).toLocaleString() : null} />
              <Detail k="Last refresh" v={d.last_refresh_at ? new Date(d.last_refresh_at).toLocaleString() : null} />
              <Detail k="Source" v={d.source} />
              <Detail k="Hostname" v={d.hostname} />
              <Detail k="Mgmt IP" v={d.ip_address} />
              <Detail k="Verify TLS" v={d.verify_tls ? "yes" : "no"} />
              <Detail k="Staged version" v={d.staged_version} />
              <Detail k="Staged at" v={d.staged_at ? new Date(d.staged_at).toLocaleString() : null} />
              {d.downloaded_versions && d.downloaded_versions.length > 0 && (
                <div className="col-span-2 md:col-span-3 lg:col-span-4">
                  <div className="text-xs uppercase tracking-wide text-slate-500">Downloaded images on device</div>
                  <div className="mt-0.5 flex flex-wrap gap-1.5">
                    {d.downloaded_versions.map((v) => {
                      const isCurrent = v === d.current_version;
                      const isStaged = v === d.staged_version;
                      return (
                        <span
                          key={v}
                          className={`inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${
                            isCurrent
                              ? "border-emerald-700 bg-emerald-950/30 text-emerald-300"
                              : isStaged
                                ? "border-sky-700 bg-sky-950/30 text-sky-300"
                                : "border-slate-700 bg-slate-900 text-slate-300"
                          }`}
                        >
                          <Package className="h-3 w-3" />
                          {v}
                          {isCurrent && <span className="text-[10px] uppercase tracking-wide opacity-70">running</span>}
                          {isStaged && !isCurrent && <span className="text-[10px] uppercase tracking-wide opacity-70">staged</span>}
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}
              {d.staged_error && (
                <div className="col-span-2 md:col-span-3 lg:col-span-4">
                  <div className="text-xs uppercase tracking-wide text-red-400">Stage error</div>
                  <div className="mt-0.5 whitespace-pre-wrap rounded border border-red-800 bg-red-950/30 px-2 py-1 text-xs text-red-200">
                    {d.staged_error}
                  </div>
                </div>
              )}
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500">Proxy via Panorama</div>
                <div className="mt-0.5">
                  <button
                    onClick={() => onPatch({ proxy_via_panorama: !d.proxy_via_panorama })}
                    disabled={d.source !== "panorama"}
                    className={`rounded px-2 py-0.5 text-xs font-medium ${
                      d.proxy_via_panorama
                        ? "bg-indigo-600 text-white hover:bg-indigo-500"
                        : "border border-slate-700 text-slate-300 hover:bg-slate-800"
                    } disabled:opacity-40 disabled:cursor-not-allowed`}
                    title={d.source !== "panorama" ? "Only Panorama-managed devices can be proxied" : ""}
                  >
                    {d.proxy_via_panorama ? "on" : "off"}
                  </button>
                </div>
              </div>
            </div>

            <PrecheckPanel
              device={d}
              precheck={precheck ?? null}
              precheckRunning={precheckRunning}
            />

            <SnapshotsPanel deviceId={d.id} onViewDiff={onViewDiff} />

            <DiskSpacePanel d={d} />

            <ConnectivityPanel deviceId={d.id} />
          </td>
        </tr>
      )}
    </>
  );
}
