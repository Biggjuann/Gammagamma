"use client";

import clsx from "clsx";

type Props = {
  label: string;
  value: number;
  hint?: string;
  accent?: "cyan" | "violet" | "amber" | "green" | "red";
};

const ACCENT_BG: Record<NonNullable<Props["accent"]>, string> = {
  cyan: "from-sonar-cyan/70 to-sonar-cyan/30",
  violet: "from-sonar-violet/70 to-sonar-violet/30",
  amber: "from-sonar-amber/80 to-sonar-amber/30",
  green: "from-sonar-green/70 to-sonar-green/30",
  red: "from-sonar-red/70 to-sonar-red/30"
};

export default function SignalGauge({ label, value, hint, accent = "cyan" }: Props) {
  const pct = Math.max(0, Math.min(100, value));
  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between">
        <div className="text-[11px] uppercase tracking-wider text-slate-400">{label}</div>
        <div className="number text-xl font-semibold">{pct.toFixed(0)}</div>
      </div>
      <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-white/5">
        <div
          className={clsx("h-full bg-gradient-to-r", ACCENT_BG[accent])}
          style={{ width: `${pct}%` }}
        />
      </div>
      {hint && <div className="mt-2 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}
