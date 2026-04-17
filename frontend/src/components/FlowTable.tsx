"use client";

import type { FlowRow } from "@/lib/api";
import { fmtBillions, fmtPrice } from "@/lib/format";
import clsx from "clsx";

export default function FlowTable({ rows }: { rows: FlowRow[] }) {
  if (!rows.length) {
    return (
      <div className="card">
        <div className="card-header">
          <div>Flow</div>
        </div>
        <div className="px-4 py-8 text-sm text-slate-500">No recent flow.</div>
      </div>
    );
  }
  return (
    <div className="card">
      <div className="card-header">
        <div>Flow — server-side enriched</div>
        <div className="text-[10px]">sweeps · blocks · OI exceedance · dealer side</div>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-xs">
          <thead className="text-slate-400">
            <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:font-medium [&>th]:text-left">
              <th>Time</th>
              <th>Contract</th>
              <th>Side</th>
              <th className="text-right">Size</th>
              <th className="text-right">Premium</th>
              <th className="text-right">OI×</th>
              <th>Tags</th>
              <th>Dealer</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.instrument_id + r.ts} className="border-t border-white/5 hover:bg-white/[0.03]">
                <td className="px-3 py-1.5 text-slate-400 number">
                  {new Date(r.ts).toLocaleTimeString()}
                </td>
                <td className="px-3 py-1.5">
                  <span className="number">{fmtPrice(r.strike)}</span>
                  <span className={clsx("ml-1 text-[10px] rounded px-1 py-0.5",
                    r.type === "C" ? "bg-sonar-green/15 text-sonar-green" : "bg-sonar-red/15 text-sonar-red")}>
                    {r.type}
                  </span>
                  <span className="ml-2 text-slate-500">{r.expiry}</span>
                </td>
                <td className="px-3 py-1.5 text-slate-300">{r.side}</td>
                <td className="px-3 py-1.5 text-right number">{r.size.toLocaleString()}</td>
                <td className="px-3 py-1.5 text-right number">{fmtBillions(r.premium)}</td>
                <td className="px-3 py-1.5 text-right number">
                  {r.oi_exceedance ? r.oi_exceedance.toFixed(2) + "×" : "—"}
                </td>
                <td className="px-3 py-1.5">
                  {r.sweep && <span className="mr-1 text-[10px] rounded bg-sonar-cyan/15 text-sonar-cyan px-1 py-0.5">SWEEP</span>}
                  {r.block && <span className="text-[10px] rounded bg-sonar-violet/15 text-sonar-violet px-1 py-0.5">BLOCK</span>}
                </td>
                <td className={clsx("px-3 py-1.5 font-medium",
                  r.dealer_side === "short" ? "text-sonar-red"
                  : r.dealer_side === "long" ? "text-sonar-green" : "text-slate-400")}>
                  {r.dealer_side.toUpperCase()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
