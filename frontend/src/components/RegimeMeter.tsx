"use client";

type Props = { score: number };

export default function RegimeMeter({ score }: Props) {
  const pct = Math.max(0, Math.min(100, score));
  const band =
    pct >= 75 ? "Elevated" : pct >= 55 ? "Transition watch" : pct >= 35 ? "Neutral" : "Compressed";
  const color =
    pct >= 75 ? "#F87171" : pct >= 55 ? "#FBBF24" : pct >= 35 ? "#22D3EE" : "#A78BFA";

  return (
    <div className="card p-4">
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-[11px] uppercase tracking-wider text-slate-400">Regime</div>
          <div className="mt-1 number text-3xl font-semibold" style={{ color }}>
            {pct.toFixed(0)}
          </div>
        </div>
        <div className="text-right">
          <div className="text-xs" style={{ color }}>{band}</div>
          <div className="text-[10px] text-slate-500 mt-1">15-feature composite</div>
        </div>
      </div>
      <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-white/5">
        <div className="h-full" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
      <div className="mt-2 flex justify-between text-[10px] text-slate-500">
        <span>0</span><span>25</span><span>50</span><span>75</span><span>100</span>
      </div>
    </div>
  );
}
