"use client";

import { useMemo, useState } from "react";
import useSWR from "swr";

import { endpoints, fetchJSON } from "@/lib/api";

type Props = { value: string; onChange: (v: string) => void };

export default function TickerPicker({ value, onChange }: Props) {
  const { data } = useSWR(endpoints.universe(), fetchJSON<{ symbols: string[] }>);
  const [q, setQ] = useState("");
  const symbols = data?.symbols ?? [];
  const filtered = useMemo(() => {
    if (!q) return symbols.slice(0, 30);
    return symbols.filter((s) => s.includes(q.toUpperCase())).slice(0, 30);
  }, [q, symbols]);

  return (
    <div className="card">
      <div className="card-header">
        <div>Ticker</div>
        <div className="text-[10px]">{symbols.length} in universe</div>
      </div>
      <div className="p-3">
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search SPX, NVDA, TSLA…"
          className="w-full rounded-md border border-white/10 bg-ink-900 px-3 py-2 text-sm outline-none focus:border-sonar-cyan/60"
        />
        <div className="mt-3 grid grid-cols-3 gap-1.5">
          {filtered.map((s) => (
            <button
              key={s}
              onClick={() => onChange(s)}
              className={`rounded px-2 py-1 text-xs number ${
                s === value
                  ? "bg-sonar-cyan/20 text-sonar-cyan border border-sonar-cyan/40"
                  : "bg-white/5 hover:bg-white/10 border border-transparent"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
