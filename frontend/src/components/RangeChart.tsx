"use client";

import type { DailyRange } from "@/lib/api";
import { fmtPrice } from "@/lib/format";

type Props = { r: DailyRange };

/**
 * Horizontal one-dimensional range chart. Places every relevant level
 * on a shared price axis so the reader can see at a glance whether
 * walls sit inside or outside the skew-implied range and where spot
 * lives relative to the gamma flip.
 *
 * No axis library — a simple SVG scaled by the pre-computed price
 * span keeps the render fast and predictable at any width.
 */
export default function RangeChart({ r }: Props) {
  // Collect every level we have a number for, so the axis auto-fits
  // to the widest span (usually put_25d ... call_25d, but sometimes
  // a wall or 1-SD bound extends farther).
  const points = [
    r.spot,
    r.lower_1sd,
    r.upper_1sd,
    r.put_25d_strike,
    r.call_25d_strike,
    r.put_wall,
    r.call_wall,
    r.gamma_flip,
  ].filter((v): v is number => typeof v === "number" && !Number.isNaN(v));

  if (points.length === 0) {
    return (
      <div className="text-xs text-slate-500 italic">
        No range data available for {r.underlying}.
      </div>
    );
  }

  // Pad the axis 5% beyond the extremes so labels don't clip.
  const raw_min = Math.min(...points);
  const raw_max = Math.max(...points);
  const pad = Math.max((raw_max - raw_min) * 0.05, 0.5);
  const axis_min = raw_min - pad;
  const axis_max = raw_max + pad;
  const span = axis_max - axis_min || 1;
  const x = (v: number) => ((v - axis_min) / span) * 100;

  const rangeLo = r.put_25d_strike ?? r.lower_1sd;
  const rangeHi = r.call_25d_strike ?? r.upper_1sd;

  return (
    <div className="w-full">
      {/* SVG track */}
      <div className="relative h-24">
        {/* Base axis */}
        <div className="absolute left-0 right-0 top-1/2 h-px bg-white/10" />

        {/* Skew-implied range band (25Δ put → 25Δ call) */}
        {rangeLo !== null && rangeHi !== null && (
          <div
            className="absolute top-1/2 -translate-y-1/2 h-6 bg-sonar-cyan/10 border-y border-sonar-cyan/40"
            style={{
              left: `${x(rangeLo)}%`,
              width: `${x(rangeHi) - x(rangeLo)}%`,
            }}
            title="25Δ put → 25Δ call (skew-implied range)"
          />
        )}

        {/* 1-SD IV bounds as dashed vertical lines */}
        {r.lower_1sd !== null && (
          <Marker
            leftPct={x(r.lower_1sd)}
            color="border-slate-500/50"
            style="dashed"
            label={`−1σ ${fmtPrice(r.lower_1sd)}`}
            labelPos="bottom"
          />
        )}
        {r.upper_1sd !== null && (
          <Marker
            leftPct={x(r.upper_1sd)}
            color="border-slate-500/50"
            style="dashed"
            label={`+1σ ${fmtPrice(r.upper_1sd)}`}
            labelPos="bottom"
          />
        )}

        {/* Walls (red = put wall, green = call wall) */}
        {r.put_wall !== null && (
          <Marker
            leftPct={x(r.put_wall)}
            color="border-sonar-red"
            label={`Put wall ${fmtPrice(r.put_wall)}`}
            labelPos="top"
          />
        )}
        {r.call_wall !== null && (
          <Marker
            leftPct={x(r.call_wall)}
            color="border-sonar-green"
            label={`Call wall ${fmtPrice(r.call_wall)}`}
            labelPos="top"
          />
        )}

        {/* Gamma flip (amber) */}
        {r.gamma_flip !== null && (
          <Marker
            leftPct={x(r.gamma_flip)}
            color="border-sonar-amber"
            label={`Flip ${fmtPrice(r.gamma_flip)}`}
            labelPos="top"
          />
        )}

        {/* Spot marker last so it sits on top */}
        <div
          className="absolute top-1/2 -translate-y-1/2 flex flex-col items-center"
          style={{ left: `${x(r.spot)}%` }}
        >
          <div className="h-4 w-1 bg-white rounded-full ring-2 ring-white/30" />
          <div className="mt-1 text-[10px] font-semibold text-white whitespace-nowrap">
            {fmtPrice(r.spot)}
          </div>
        </div>
      </div>

      {/* Axis min / max labels */}
      <div className="flex justify-between text-[10px] text-slate-500 mt-1">
        <span>{fmtPrice(axis_min)}</span>
        <span>{fmtPrice(axis_max)}</span>
      </div>
    </div>
  );
}

function Marker({
  leftPct,
  color,
  label,
  labelPos,
  style = "solid",
}: {
  leftPct: number;
  color: string;
  label: string;
  labelPos: "top" | "bottom";
  style?: "solid" | "dashed";
}) {
  return (
    <div
      className="absolute top-1/2 -translate-y-1/2 flex flex-col items-center"
      style={{ left: `${leftPct}%` }}
    >
      <div
        className={`h-8 border-l ${color} ${
          style === "dashed" ? "border-dashed" : ""
        }`}
      />
      <div
        className={`absolute ${
          labelPos === "top" ? "-top-4" : "top-10"
        } text-[10px] text-slate-300 whitespace-nowrap px-1`}
      >
        {label}
      </div>
    </div>
  );
}
