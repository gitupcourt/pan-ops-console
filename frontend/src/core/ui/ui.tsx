// Tiny reusable UI primitives. Kept in one file to avoid component sprawl
// while the app is small.

import clsx from "clsx";
import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, useEffect, useState } from "react";

export function Button({
  className,
  variant = "default",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" }) {
  const base = "text-sm rounded px-2.5 py-1 border transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const variants = {
    default: "border-zinc-700 bg-zinc-800 hover:bg-zinc-700 text-zinc-100",
    primary: "border-blue-600 bg-blue-600 hover:bg-blue-500 text-white",
    danger: "border-rose-700 bg-rose-900/40 hover:bg-rose-900/60 text-rose-200",
  } as const;
  return <button className={clsx(base, variants[variant], className)} {...rest} />;
}

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={clsx(
        "bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100",
        "placeholder:text-zinc-500 focus:outline-none focus:border-blue-500",
        className,
      )}
      {...rest}
    />
  );
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        "bg-zinc-900 border border-zinc-700 rounded px-2 py-1 text-sm text-zinc-100",
        "focus:outline-none focus:border-blue-500",
        className,
      )}
      {...rest}
    >
      {children}
    </select>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <label className="flex flex-col gap-1 text-xs">
      <span className="text-zinc-400">{label}</span>
      {children}
      {hint && <span className="text-[11px] text-zinc-500">{hint}</span>}
    </label>
  );
}

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx("rounded-lg border border-zinc-800 bg-zinc-900/50", className)}>
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  action,
  description,
}: {
  title: string;
  action?: ReactNode;
  description?: string;
}) {
  return (
    <div className="flex items-start justify-between gap-3 px-4 py-3 border-b border-zinc-800">
      <div>
        <h2 className="text-sm font-semibold text-zinc-100">{title}</h2>
        {description && <p className="text-xs text-zinc-500 mt-0.5">{description}</p>}
      </div>
      {action}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="px-4 py-6 text-center text-xs text-zinc-500">{children}</div>;
}

/**
 * Horizontal-scroll safety net for wide data tables.
 *
 * Dense tables get THREE layers of overflow defense, in priority order:
 *   1. Responsive column-hiding (`hidden md:/lg:/xl:table-cell`) drops
 *      low-priority columns as the viewport narrows.
 *   2. Truncation (`max-w-[..] truncate`) caps individual long cells.
 *   3. THIS — a scroll container so that when the still-visible columns
 *      exceed the available width (everything shown on a wide screen, an
 *      unusually long device name, etc.) the content scrolls into reach
 *      instead of being clipped off-frame with no scrollbar.
 *
 * Always wrap a wide `<table>` in this rather than letting it overflow its
 * card. Row-action menus inside such tables must use `position: fixed`
 * (anchored via getBoundingClientRect) so they're not clipped by this
 * container — an absolutely-positioned popover would be.
 */
export function TableScroll({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx("overflow-x-auto", className)}>{children}</div>;
}

/**
 * Animated "…" that cycles "" → "." → ".." → "..." (~every 400ms), so a slow
 * operation reads as actively working rather than hung. Width is reserved for
 * three dots so surrounding text doesn't reflow as it cycles.
 */
export function AnimatedEllipsis({ className }: { className?: string }) {
  const [dots, setDots] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setDots((d) => (d + 1) % 4), 400);
    return () => clearInterval(t);
  }, []);
  return (
    <span className={className} aria-hidden="true">
      {".".repeat(dots)}
      <span className="invisible">{".".repeat(3 - dots)}</span>
    </span>
  );
}
