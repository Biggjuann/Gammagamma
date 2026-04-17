"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import type { Bundle } from "@/lib/api";
import { fmtBillions } from "@/lib/format";

export default function ExpiryGexChart({ bundle }: { bundle: Bundle }) {
  const data = Object.entries(bundle.per_expiry)
    .map(([expiry, gex]) => ({ expiry, gex }))
    .sort((a, b) => a.expiry.localeCompare(b.expiry));

  return (
    <div className="card">
      <div className="card-header">
        <div>GEX by expiry</div>
      </div>
      <div className="h-[240px] p-2">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 6 }}>
            <CartesianGrid stroke="rgba(148,163,184,0.08)" />
            <XAxis
              dataKey="expiry"
              tick={{ fill: "#94a3b8", fontSize: 10 }}
              axisLine={{ stroke: "rgba(148,163,184,0.2)" }}
              tickLine={false}
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
              formatter={(v: number) => fmtBillions(v)}
            />
            <Bar dataKey="gex" radius={[2, 2, 0, 0]}>
              {data.map((d, i) => (
                <Cell key={i} fill={d.gex >= 0 ? "#34D399" : "#F87171"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
