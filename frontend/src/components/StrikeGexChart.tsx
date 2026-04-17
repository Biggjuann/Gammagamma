"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import type { Bundle } from "@/lib/api";
import { fmtBillions, fmtPrice } from "@/lib/format";

type Props = { bundle: Bundle };

export default function StrikeGexChart({ bundle }: Props) {
  const strikes = Object.keys(bundle.per_strike)
    .map((k) => parseFloat(k))
    .sort((a, b) => a - b);

  const data = strikes.map((k) => {
    const b = bundle.per_strike[k.toFixed(2)];
    return {
      strike: k,
      call: b?.call_gex ?? 0,
      put: b?.put_gex ?? 0,
      net: b?.net_gex ?? 0
    };
  });

  return (
    <div className="card">
      <div className="card-header">
        <div>Strike-by-strike GEX</div>
        <div className="flex gap-3 text-[10px]">
          <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-sonar-green inline-block"/>CALL</span>
          <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-sonar-red inline-block"/>PUT</span>
          <span className="flex items-center gap-1"><i className="h-2 w-2 rounded-sm bg-sonar-cyan inline-block"/>NET</span>
        </div>
      </div>
      <div className="h-[420px] p-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} stackOffset="sign" margin={{ top: 12, right: 16, left: 0, bottom: 6 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.08)" />
            <XAxis
              dataKey="strike"
              tick={{ fill: "#94a3b8", fontSize: 10 }}
              axisLine={{ stroke: "rgba(148,163,184,0.2)" }}
              tickLine={false}
              tickFormatter={(v) => fmtPrice(v, 0)}
            />
            <YAxis
              tick={{ fill: "#94a3b8", fontSize: 10 }}
              axisLine={{ stroke: "rgba(148,163,184,0.2)" }}
              tickLine={false}
              tickFormatter={(v) => fmtBillions(v)}
              width={72}
            />
            <Tooltip
              contentStyle={{
                background: "#0B1020",
                border: "1px solid rgba(148,163,184,0.15)",
                borderRadius: 8,
                color: "#E6ECFF"
              }}
              labelFormatter={(v) => `Strike ${fmtPrice(v as number)}`}
              formatter={(v: number, name) => [fmtBillions(v), name]}
            />
            <ReferenceLine
              x={bundle.spot}
              stroke="#22D3EE"
              strokeDasharray="4 4"
              label={{ value: "spot", fill: "#22D3EE", fontSize: 10, position: "insideTopRight" }}
            />
            {bundle.levels.gamma_flip != null && (
              <ReferenceLine
                x={bundle.levels.gamma_flip}
                stroke="#FBBF24"
                strokeDasharray="4 4"
                label={{ value: "flip", fill: "#FBBF24", fontSize: 10, position: "insideTop" }}
              />
            )}
            {bundle.levels.call_wall != null && (
              <ReferenceLine
                x={bundle.levels.call_wall}
                stroke="#34D399"
                strokeDasharray="2 3"
                label={{ value: "call wall", fill: "#34D399", fontSize: 10, position: "insideTop" }}
              />
            )}
            {bundle.levels.put_wall != null && (
              <ReferenceLine
                x={bundle.levels.put_wall}
                stroke="#F87171"
                strokeDasharray="2 3"
                label={{ value: "put wall", fill: "#F87171", fontSize: 10, position: "insideBottom" }}
              />
            )}
            <Bar dataKey="call" stackId="g" radius={[2, 2, 0, 0]}>
              {data.map((_, i) => (
                <Cell key={`c-${i}`} fill="#34D399" fillOpacity={0.85} />
              ))}
            </Bar>
            <Bar dataKey="put" stackId="g" radius={[0, 0, 2, 2]}>
              {data.map((_, i) => (
                <Cell key={`p-${i}`} fill="#F87171" fillOpacity={0.85} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
