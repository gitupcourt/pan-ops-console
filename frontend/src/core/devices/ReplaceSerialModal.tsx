import { useMutation } from "@tanstack/react-query";
import { useState } from "react";

import { api, Device } from "../../api";
import { Button } from "../ui/ui";

/**
 * RMA serial-replacement dialog. Use when a firewall was RMA'd / factory-reset
 * and came back with a new serial. Re-points this record to the new serial so
 * it carries its history forward; the duplicate auto-imported under the new
 * serial is absorbed, and the stored API key is cleared (new box → fresh auth).
 *
 * Candidates: other devices already in inventory are offered as one-click
 * picks (the RMA'd box has usually already auto-imported under its new serial,
 * showing up named by that bare serial), but any serial can also be typed.
 */
export function ReplaceSerialModal({
  device,
  devices,
  onClose,
  onDone,
}: {
  device: Device;
  devices: Device[];
  onClose: () => void;
  onDone?: () => void;
}) {
  const [serial, setSerial] = useState("");
  const [done, setDone] = useState(false);

  // Other devices we could adopt the serial from. Auto-imported records (named
  // by their bare serial) are the usual RMA case, so float those to the top.
  const candidates = devices
    .filter((d) => d.id !== device.id && d.serial)
    .sort(
      (a, b) =>
        Number(b.name === b.serial) - Number(a.name === a.serial),
    );

  const replace = useMutation({
    mutationFn: () => api.replaceDeviceSerial(device.id, serial.trim()),
    onSuccess: () => {
      setDone(true);
      onDone?.();
    },
  });

  const trimmed = serial.trim();
  const canSubmit =
    trimmed.length > 0 && trimmed !== device.serial && !replace.isPending;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg max-h-[85vh] overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-950 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 flex items-start justify-between border-b border-zinc-800 bg-zinc-950 px-4 py-3">
          <div>
            <div className="text-sm font-semibold text-zinc-100">
              Replace serial (RMA)
            </div>
            <div className="text-[11px] text-zinc-500">
              {device.name} · current serial {device.serial ?? "—"}
            </div>
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
          {!done ? (
            <>
              <p className="text-zinc-400">
                For a firewall that was RMA&apos;d or factory-reset and came back
                with a new serial. This record keeps its name, settings, and
                full history (capacity samples, snapshots, upgrade history) — only
                the serial changes.
              </p>

              <div>
                <label className="mb-1 block text-[10px] uppercase tracking-wider text-zinc-500">
                  New serial
                </label>
                <input
                  autoFocus
                  value={serial}
                  onChange={(e) => setSerial(e.target.value)}
                  placeholder="e.g. the serial the replacement reports"
                  className="w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 font-mono text-zinc-100 placeholder:text-zinc-600 focus:border-blue-500 focus:outline-none"
                />
              </div>

              {candidates.length > 0 && (
                <div>
                  <div className="mb-1 text-[10px] uppercase tracking-wider text-zinc-500">
                    Or pick a device already in inventory
                  </div>
                  <div className="max-h-40 space-y-1 overflow-y-auto">
                    {candidates.map((c) => (
                      <button
                        key={c.id}
                        onClick={() => setSerial(c.serial ?? "")}
                        className={`flex w-full items-center gap-2 rounded border px-2 py-1 text-left ${
                          serial.trim() === c.serial
                            ? "border-blue-600 bg-blue-950/40"
                            : "border-zinc-800 bg-zinc-900/40 hover:border-zinc-600"
                        }`}
                      >
                        <span className="font-mono text-zinc-200">
                          {c.serial}
                        </span>
                        {c.name === c.serial ? (
                          <span className="rounded border border-amber-700/60 px-1 text-[9px] uppercase text-amber-300">
                            auto-imported
                          </span>
                        ) : (
                          <span className="text-zinc-500">{c.name}</span>
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              <div className="rounded border border-amber-900/40 bg-amber-950/20 p-2 text-amber-200/90">
                Heads up: the stored API key will be cleared — the replacement
                hardware needs fresh auth, so re-run Test Connection afterward.
                Any device already imported under the new serial will be
                absorbed (its recent data merged into this record, then
                removed).
              </div>

              {replace.error && (
                <div className="text-rose-400">
                  Replace failed: {(replace.error as Error).message}
                </div>
              )}

              <div className="flex justify-end gap-2 border-t border-zinc-800 pt-3">
                <Button onClick={onClose}>Cancel</Button>
                <Button
                  variant="primary"
                  disabled={!canSubmit}
                  onClick={() => replace.mutate()}
                >
                  {replace.isPending ? "Replacing…" : "Replace serial"}
                </Button>
              </div>
            </>
          ) : (
            <div className="space-y-3">
              <div className="rounded border border-emerald-900/40 bg-emerald-950/30 p-2 text-emerald-200">
                Done. <span className="font-semibold">{device.name}</span> now
                uses serial <span className="font-mono">{trimmed}</span>. Its
                history carried over.
              </div>
              <div className="text-[11px] text-zinc-400">
                The stored API key was cleared — run{" "}
                <span className="text-zinc-200">Test connection</span> (or edit
                the device) to re-authenticate against the replacement hardware.
              </div>
              <div className="flex justify-end border-t border-zinc-800 pt-3">
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
