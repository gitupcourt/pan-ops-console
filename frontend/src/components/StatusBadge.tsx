import clsx from "clsx";

const COLORS = {
  verified: "bg-emerald-900/50 text-emerald-300 border-emerald-700",
  probable: "bg-amber-900/40 text-amber-300 border-amber-700",
  needs_work: "bg-rose-900/40 text-rose-300 border-rose-700",
} as const;

export function StatusBadge({ status }: { status: keyof typeof COLORS }) {
  return (
    <span
      className={clsx(
        "inline-block rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide border",
        COLORS[status],
      )}
      title={
        status === "verified"
          ? "Confirmed on a live firewall"
          : status === "probable"
            ? "Based on docs / common knowledge — verify on first poll"
            : "Command/extractor is a guess and will likely need adjustment"
      }
    >
      {status.replace("_", " ")}
    </span>
  );
}
