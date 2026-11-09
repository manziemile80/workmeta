import type { LucideIcon } from "lucide-react";
import { cn } from "../lib/cn";

export type StatusTone = "ok" | "warn" | "error" | "neutral";

const toneStyles: Record<
  StatusTone,
  { dot: string; value: string; ring: string }
> = {
  ok: {
    dot: "bg-success-500 shadow-[0_0_0_4px_rgb(16_185_129/0.12)]",
    value: "text-ink-900",
    ring: "ring-mist-200",
  },
  warn: {
    dot: "bg-amber-400 shadow-[0_0_0_4px_rgb(251_191_36/0.16)]",
    value: "text-ink-900",
    ring: "ring-amber-400/30",
  },
  error: {
    dot: "bg-danger-500 shadow-[0_0_0_4px_rgb(239_68_68/0.12)]",
    value: "text-ink-900",
    ring: "ring-danger-500/25",
  },
  neutral: {
    dot: "bg-ink-400 shadow-[0_0_0_4px_rgb(148_163_184/0.14)]",
    value: "text-ink-700",
    ring: "ring-mist-200",
  },
};

interface StatusCardProps {
  icon: LucideIcon;
  label: string;
  value: string;
  detail?: string;
  tone: StatusTone;
  loading?: boolean;
}

export function StatusCard({
  icon: Icon,
  label,
  value,
  detail,
  tone,
  loading = false,
}: StatusCardProps) {
  const styles = toneStyles[tone];

  return (
    <div
      className={cn(
        "animate-in rounded-2xl bg-white p-5 shadow-card ring-1",
        styles.ring,
      )}
    >
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.12em] text-ink-500">
          <Icon className="h-4 w-4 text-teal-600" aria-hidden="true" />
          {label}
        </span>
        <span
          className={cn(
            "h-2 w-2 rounded-full",
            loading ? "animate-pulse bg-ink-400" : styles.dot,
          )}
          aria-hidden="true"
        />
      </div>
      <p
        className={cn(
          "mt-3 text-lg font-semibold tracking-tight",
          styles.value,
        )}
      >
        {loading ? "Checking…" : value}
      </p>
      {detail ? (
        <p className="mt-1 text-sm leading-relaxed text-ink-500">{detail}</p>
      ) : null}
    </div>
  );
}
