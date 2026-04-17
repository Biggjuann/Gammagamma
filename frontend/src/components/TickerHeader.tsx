"use client";

import type { Bundle } from "@/lib/api";
import { fmtBillions, fmtPrice, signClass } from "@/lib/format";

export default function TickerHeader({ bundle }: { bundle: Bundle }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-baseline gap-3">
        <div className="text-3xl font-semibold tracking-tight">{bundle.underlying}</div>
        <div className="number text-2xl">{fmtPrice(bundle.spot)}</div>
        <div className={`number text-sm ${signClass(bundle.totals.gex)}`}>
          GEX {fmtBillions(bundle.totals.gex)}
        </div>
      </div>
      <div className="text-xs text-slate-500">
        Greeks recomputed {new Date(bundle.asof).toLocaleString()} — dealer-convention
      </div>
    </div>
  );
}
