"use client";

import type { ExpiryFilter } from "@/lib/api";

const OPTIONS: { value: ExpiryFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "0dte", label: "0DTE" },
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "leaps", label: "LEAPS" }
];

type Props = { value: ExpiryFilter; onChange: (v: ExpiryFilter) => void };

export default function ExpiryFilterBar({ value, onChange }: Props) {
  return (
    <div className="flex gap-1 rounded-md border border-white/10 bg-ink-800 p-1 text-xs">
      {OPTIONS.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={`px-2.5 py-1 rounded ${
            value === o.value
              ? "bg-sonar-cyan/20 text-sonar-cyan"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
