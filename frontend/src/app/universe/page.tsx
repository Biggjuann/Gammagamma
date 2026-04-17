"use client";

import useSWR from "swr";

import { SummaryRow, endpoints, fetchJSON } from "@/lib/api";
import { fmtBillions, fmtPrice, signClass } from "@/lib/format";

export default function UniversePage() {
  const { data } = useSWR<{ rows: SummaryRow[] }>(endpoints.summary(), fetchJSON, {
    refreshInterval: 600_000
  });
  const rows = data?.rows ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Universe</h1>
        <p className="text-sm text-slate-400">
          Cross-sectional view — GEX, structural levels, and regime for every ticker we
          cover. All numbers share the same cached 60 s snapshot as the dashboard.
        </p>
      </div>

      <div className="card overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="text-xs text-slate-400">
            <tr className="[&>th]:px-3 [&>th]:py-2 [&>th]:text-left [&>th]:font-medium">
              <th>Ticker</th>
              <th className="text-right">Spot</th>
              <th className="text-right">Net GEX</th>
              <th className="text-right">Net DEX</th>
              <th className="text-right">Flip</th>
              <th className="text-right">Call wall</th>
              <th className="text-right">Put wall</th>
              <th className="text-right">Regime</th>
              <th className="text-right">SSS</th>
              <th className="text-right">FPI</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.underlying} className="border-t border-white/5 hover:bg-white/[0.03]">
                <td className="px-3 py-2 font-semibold">{r.underlying}</td>
                <td className="px-3 py-2 text-right number">{fmtPrice(r.spot)}</td>
                <td className={`px-3 py-2 text-right number ${signClass(r.total_gex)}`}>
                  {fmtBillions(r.total_gex)}
                </td>
                <td className={`px-3 py-2 text-right number ${signClass(r.total_dex)}`}>
                  {fmtBillions(r.total_dex)}
                </td>
                <td className="px-3 py-2 text-right number text-sonar-amber">
                  {fmtPrice(r.gamma_flip ?? undefined)}
                </td>
                <td className="px-3 py-2 text-right number text-sonar-green">
                  {fmtPrice(r.call_wall ?? undefined)}
                </td>
                <td className="px-3 py-2 text-right number text-sonar-red">
                  {fmtPrice(r.put_wall ?? undefined)}
                </td>
                <td className="px-3 py-2 text-right number">{r.regime_score?.toFixed(0) ?? "—"}</td>
                <td className="px-3 py-2 text-right number">{r.sss?.toFixed(0) ?? "—"}</td>
                <td className="px-3 py-2 text-right number">{r.fpi?.toFixed(0) ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
