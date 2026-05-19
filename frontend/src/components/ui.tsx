// Tiny reusable UI primitives. Kept in one file to avoid component sprawl
// while the app is small.

import clsx from "clsx";
import { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

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
