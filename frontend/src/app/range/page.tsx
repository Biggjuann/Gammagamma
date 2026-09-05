"use client";

import useSWR from "swr";

import RangeChart from "@/components/RangeChart";
import { DailyRange, endpoints, fetchJSON } from "@/lib/api";
import { fmtPrice, fmtPct } from "@/lib/format";

export default function RangePage() {
  const { data } = useSWR<{ rows: DailyRange[] }>(endpoints.range(), fetchJSON, {
    refreshInterval: 60_000,
  });
  const rows = data?.rows ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Today's Range</h1>
        <p className="text-sm text-slate-400 max-w-3xl">
          Expected daily range for each ticker, built from three inputs:
          ATM implied vol gives the <em>width</em>, 25-delta strikes give the
          <em> shape</em> (skew asymmetry), and GEX regime tells us whether
          price is likely to actually use the range or stay pinned inside it.
          Anchors on the 0DTE / nearest expiry.
        </p>
      </div>

      <div className="text-[11px] text-slate-500 flex gap-4">
        <LegendSwatch cls="bg-white" label="Spot" />
        <LegendSwatch cls="bg-sonar-cyan/40" label="25Δ skew range" />
        <LegendSwatch cls="bg-slate-500" label="±1σ IV bounds" />
        <LegendSwatch cls="bg-sonar-green" label="Call wall" />
        <LegendSwatch cls="bg-sonar-red" label="Put wall" />
        <LegendSwatch cls="bg-sonar-amber" label="Gamma flip" />
      </div>

      <div className="grid grid-cols-1 gap-4">
        {rows.map((r) => (
          <TickerRangeCard key={r.underlying} r={r} />
        ))}
      </div>

      <p className="text-[11px] text-slate-500 italic mt-2 max-w-3xl">
        Educational only. IV includes a variance risk premium so realized
        usually stays inside the skew range on quiet days; the exceptions
        cluster in negative-gamma regimes. Not trading advice.
      </p>
    </div>
  );
}

function LegendSwatch({ cls, label }: { cls: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className={`inline-block h-2 w-4 ${cls}`} />
      <span>{label}</span>
    </span>
  );
}

function TickerRangeCard({ r }: { r: DailyRange }) {
  if (r.error) {
    return (
      <div className="card p-4">
        <div className="font-semibold">{r.underlying}</div>
        <div className="text-xs text-sonar-red mt-1">Error: {r.error}</div>
      </div>
    );
  }

  const regimeLabel =
    r.regime === "positive_gamma"
      ? "POSITIVE GAMMA · MEAN REVERT"
      : r.regime === "negative_gamma"
      ? "NEGATIVE GAMMA · TREND"
      : "REGIME UNKNOWN";
  const regimeCls =
    r.regime === "positive_gamma"
      ? "bg-sonar-green/15 text-sonar-green border-sonar-green/40"
      : r.regime === "negative_gamma"
      ? "bg-sonar-red/15 text-sonar-red border-sonar-red/40"
      : "bg-slate-500/15 text-slate-400 border-slate-500/40";

  const skewPct = r.skew_tilt !== null ? r.skew_tilt * 100 : null;

  return (
    <div className="card p-4 flex flex-col gap-4">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-3">
          <span className="text-xl font-semibold">{r.underlying}</span>
          <span className="text-lg text-slate-300 number">{fmtPrice(r.spot)}</span>
          <span
            className={`inline-block text-[10px] font-semibold tracking-wider border rounded px-2 py-0.5 ${regimeCls}`}
          >
            {regimeLabel}
          </span>
        </div>
        <div className="text-[11px] text-slate-400">
          anchor {r.anchor_expiry || "—"} · DTE {(r.dte_years * 365).toFixed(2)}
        </div>
      </div>

      <div>{r.read_headline && (
        <div className="text-sm text-slate-100 font-medium">{r.read_headline}</div>
      )}
      {r.read_bullets.length > 0 && (
        <ul className="mt-2 text-xs text-slate-300 space-y-1 list-disc list-inside">
          {r.read_bullets.map((b, i) => (
            <li key={i}>{b}</li>
          ))}
        </ul>
      )}</div>

      <RangeChart r={r} />

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 pt-2 border-t border-white/5">
        <Stat
          label="ATM IV"
          value={r.atm_iv !== null ? fmtPct(r.atm_iv * 100) : "—"}
        />
        <Stat
          label="Expected 1σ"
          value={
            r.expected_move_1sd !== null ? `±$${r.expected_move_1sd.toFixed(2)}` : "—"
          }
        />
        <Stat
          label="ATM straddle"
          value={r.atm_straddle !== null ? `$${r.atm_straddle.toFixed(2)}` : "—"}
        />
        <Stat
          label="25Δ range"
          value={
            r.put_25d_strike !== null && r.call_25d_strike !== null
              ? `${fmtPrice(r.put_25d_strike)} / ${fmtPrice(r.call_25d_strike)}`
              : "—"
          }
        />
        <Stat
          label="Skew tilt"
          value={
            skewPct !== null
              ? `${skewPct > 0 ? "+" : ""}${skewPct.toFixed(2)}%`
              : "—"
          }
          cls={
            skewPct === null
              ? undefined
              : skewPct > 0.1
              ? "text-sonar-red"
              : skewPct < -0.1
              ? "text-sonar-green"
              : "text-slate-300"
          }
        />
      </div>
    </div>
  );
}

function Stat({
  label,
  value,
  cls,
}: {
  label: string;
  value: string;
  cls?: string;
}) {
  return (
    <div>
      <div className="text-[10px] text-slate-500 uppercase tracking-wider">{label}</div>
      <div className={`text-sm number ${cls ?? "text-slate-200"}`}>{value}</div>
    </div>
  );
}
