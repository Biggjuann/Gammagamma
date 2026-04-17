"use client";

import type { Bundle } from "@/lib/api";
import { fmtBillions, fmtPrice, signClass } from "@/lib/format";

export default function LevelsPanel({ bundle }: { bundle: Bundle }) {
  const l = bundle.levels;
  const rows: { label: string; value: string; accent?: string }[] = [
    { label: "Spot", value: fmtPrice(bundle.spot), accent: "text-sonar-cyan" },
    { label: "Gamma flip", value: l.gamma_flip != null ? fmtPrice(l.gamma_flip) : "—", accent: "text-sonar-amber" },
    { label: "Call wall", value: l.call_wall != null ? fmtPrice(l.call_wall) : "—", accent: "text-sonar-green" },
    { label: "Put wall", value: l.put_wall != null ? fmtPrice(l.put_wall) : "—", accent: "text-sonar-red" },
    { label: "GVWAP", value: l.gvwap != null ? fmtPrice(l.gvwap) : "—", accent: "text-sonar-violet" }
  ];
  return (
    <div className="card">
      <div className="card-header">
        <div>Structural levels</div>
        <div className="text-[10px]">asof {new Date(bundle.asof).toLocaleTimeString()}</div>
      </div>
      <div className="divide-y divide-white/5">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center justify-between px-4 py-2.5 text-sm">
            <div className="text-slate-400">{r.label}</div>
            <div className={`number font-semibold ${r.accent ?? ""}`}>{r.value}</div>
          </div>
        ))}
        <div className="px-4 py-3 text-xs text-slate-400">
          <div className="mb-1">Major call walls</div>
          <div className="flex flex-wrap gap-1">
            {l.major_call_walls.length === 0 && <span>—</span>}
            {l.major_call_walls.map((v) => (
              <span key={`cw-${v}`} className="number rounded bg-sonar-green/15 text-sonar-green px-1.5 py-0.5">
                {fmtPrice(v)}
              </span>
            ))}
          </div>
          <div className="mb-1 mt-3">Major put walls</div>
          <div className="flex flex-wrap gap-1">
            {l.major_put_walls.length === 0 && <span>—</span>}
            {l.major_put_walls.map((v) => (
              <span key={`pw-${v}`} className="number rounded bg-sonar-red/15 text-sonar-red px-1.5 py-0.5">
                {fmtPrice(v)}
              </span>
            ))}
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-2 px-4 py-3 text-xs">
          <Stat label="Total GEX" value={fmtBillions(bundle.totals.gex)} cls={signClass(bundle.totals.gex)} />
          <Stat label="Total DEX" value={fmtBillions(bundle.totals.dex)} cls={signClass(bundle.totals.dex)} />
          <Stat label="Vanna" value={fmtBillions(bundle.totals.vanna)} cls={signClass(bundle.totals.vanna)} />
          <Stat label="Charm" value={fmtBillions(bundle.totals.charm)} cls={signClass(bundle.totals.charm)} />
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, cls }: { label: string; value: string; cls: string }) {
  return (
    <div>
      <div className="text-slate-500 text-[10px] uppercase">{label}</div>
      <div className={`number font-semibold ${cls}`}>{value}</div>
    </div>
  );
}
