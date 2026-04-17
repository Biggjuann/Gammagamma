"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";

import ExpiryFilterBar from "@/components/ExpiryFilter";
import ExpiryGexChart from "@/components/ExpiryGexChart";
import FlowTable from "@/components/FlowTable";
import LevelsPanel from "@/components/LevelsPanel";
import PlaybookCard from "@/components/PlaybookCard";
import RegimeMeter from "@/components/RegimeMeter";
import SignalGauge from "@/components/SignalGauge";
import StrikeGexChart from "@/components/StrikeGexChart";
import TickerHeader from "@/components/TickerHeader";
import TickerPicker from "@/components/TickerPicker";
import {
  Bundle,
  ExpiryFilter,
  endpoints,
  fetchJSON
} from "@/lib/api";

export default function Dashboard() {
  const [symbol, setSymbol] = useState("SPX");
  const [expiry, setExpiry] = useState<ExpiryFilter>("all");
  const { data, mutate } = useSWR<Bundle>(endpoints.ticker(symbol, expiry), fetchJSON, {
    // backend refreshes twice/day (08:30 & 16:00 CT) — a browser-side poll
    // every 10 min just picks up scheduled refreshes without spamming the API.
    refreshInterval: 600_000,
    revalidateOnFocus: false
  });

  const wsRef = useRef<WebSocket | null>(null);
  useEffect(() => {
    // open websocket so the 60 s refresh tick from the backend drives SWR
    try {
      const ws = new WebSocket(endpoints.ws(symbol));
      wsRef.current = ws;
      ws.onmessage = (ev) => {
        try {
          const payload = JSON.parse(ev.data);
          if (payload.underlying === symbol) mutate(payload, { revalidate: false });
        } catch {
          /* ignore */
        }
      };
      return () => ws.close();
    } catch {
      return;
    }
  }, [symbol, mutate]);

  return (
    <div className="grid grid-cols-12 gap-4">
      <aside className="col-span-12 lg:col-span-3 flex flex-col gap-4">
        <TickerPicker value={symbol} onChange={setSymbol} />
        {data && <RegimeMeter score={data.signals.regime_score} />}
        {data && (
          <div className="grid grid-cols-2 gap-2">
            <SignalGauge label="SSS" value={data.signals.sss} accent="cyan"
              hint="Structural Stability Score" />
            <SignalGauge label="FPI" value={data.signals.fpi} accent="violet"
              hint="Flow Pressure Index" />
            <SignalGauge label="REGD" value={data.signals.regd} accent="amber"
              hint="Regime Distance" />
            <SignalGauge label="HV" value={data.signals.hv} accent="green"
              hint="Hidden Vol" />
          </div>
        )}
      </aside>

      <section className="col-span-12 lg:col-span-9 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-3">
          {data ? <TickerHeader bundle={data} /> : <div className="h-10" />}
          <ExpiryFilterBar value={expiry} onChange={setExpiry} />
        </div>

        {data?.playbook && <PlaybookCard playbook={data.playbook} />}

        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 xl:col-span-8">
            {data ? <StrikeGexChart bundle={data} /> : <Skeleton h={420} />}
          </div>
          <div className="col-span-12 xl:col-span-4">
            {data ? <LevelsPanel bundle={data} /> : <Skeleton h={420} />}
          </div>
          <div className="col-span-12 xl:col-span-8">
            {data ? <ExpiryGexChart bundle={data} /> : <Skeleton h={240} />}
          </div>
          <div className="col-span-12 xl:col-span-4">
            {data && <TopRowsCard bundle={data} />}
          </div>
          <div className="col-span-12">
            {data ? <FlowTable rows={data.flow} /> : <Skeleton h={240} />}
          </div>
        </div>
      </section>
    </div>
  );
}

function Skeleton({ h }: { h: number }) {
  return (
    <div
      className="card animate-pulse"
      style={{ height: h }}
    />
  );
}

function TopRowsCard({ bundle }: { bundle: Bundle }) {
  const top = [...bundle.rows]
    .sort((a, b) => Math.abs(b.gex) - Math.abs(a.gex))
    .slice(0, 10);
  return (
    <div className="card">
      <div className="card-header">
        <div>Top gamma contracts</div>
        <div className="text-[10px]">by |GEX|</div>
      </div>
      <div className="divide-y divide-white/5">
        {top.map((r) => (
          <div key={r.instrument_id} className="flex items-center justify-between px-3 py-1.5 text-xs">
            <div className="number">
              {r.strike.toFixed(2)}
              <span
                className={`ml-1 text-[10px] rounded px-1 py-0.5 ${
                  r.type === "C"
                    ? "bg-sonar-green/15 text-sonar-green"
                    : "bg-sonar-red/15 text-sonar-red"
                }`}
              >
                {r.type}
              </span>
              <span className="ml-2 text-slate-500">{new Date(r.expiry).toLocaleDateString()}</span>
            </div>
            <div className={`number ${r.gex >= 0 ? "text-sonar-green" : "text-sonar-red"}`}>
              {formatCompact(r.gex)}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatCompact(v: number): string {
  const abs = Math.abs(v);
  const sign = v < 0 ? "-" : "";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}
