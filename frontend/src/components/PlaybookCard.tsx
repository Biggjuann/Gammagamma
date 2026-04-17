"use client";

import clsx from "clsx";

import type { Playbook } from "@/lib/api";
import { fmtPrice } from "@/lib/format";

type Props = { playbook: Playbook };

const BIAS_STYLES: Record<Playbook["bias"], { bg: string; text: string; ring: string }> = {
  "MEAN REVERT": {
    bg: "bg-sonar-cyan/10",
    text: "text-sonar-cyan",
    ring: "ring-sonar-cyan/30"
  },
  TREND: {
    bg: "bg-sonar-red/10",
    text: "text-sonar-red",
    ring: "ring-sonar-red/30"
  },
  "PIVOT WATCH": {
    bg: "bg-sonar-amber/10",
    text: "text-sonar-amber",
    ring: "ring-sonar-amber/30"
  },
  DIRECTIONAL: {
    bg: "bg-sonar-violet/10",
    text: "text-sonar-violet",
    ring: "ring-sonar-violet/30"
  }
};

const REGIME_STYLES: Record<Playbook["regime"], string> = {
  COMPRESSED: "text-sonar-violet",
  NEUTRAL: "text-sonar-cyan",
  TRANSITION: "text-sonar-amber",
  ELEVATED: "text-sonar-red"
};

export default function PlaybookCard({ playbook }: Props) {
  const styles = BIAS_STYLES[playbook.bias];

  return (
    <div className={clsx("card ring-1", styles.ring)}>
      <div className="card-header">
        <div>Today&apos;s playbook</div>
        <div className="text-[10px] text-slate-500">
          Rule-based from the live GEX structure
        </div>
      </div>
      <div className="p-5 flex flex-col gap-4">
        <div className="flex flex-wrap items-center gap-3">
          <span
            className={clsx(
              "px-3 py-1 rounded-md text-[11px] font-semibold tracking-wider",
              styles.bg,
              styles.text
            )}
          >
            {playbook.bias}
          </span>
          <span className="text-[11px] uppercase tracking-wider text-slate-400">
            regime ·{" "}
            <span className={REGIME_STYLES[playbook.regime]}>{playbook.regime}</span>
          </span>
        </div>

        <p className="text-sm text-slate-200 leading-relaxed">{playbook.headline}</p>

        <ul className="flex flex-col gap-1.5 text-sm text-slate-300">
          {playbook.bullets.map((b, i) => (
            <li key={i} className="flex gap-2">
              <span className={clsx("mt-1.5 h-1 w-1 rounded-full shrink-0", styles.text, "bg-current")} />
              <span>{b}</span>
            </li>
          ))}
        </ul>

        <div className="grid grid-cols-2 md:grid-cols-5 gap-3 pt-2 border-t border-white/5">
          {playbook.key_levels.map((k) => (
            <div key={k.label}>
              <div className="text-[10px] uppercase tracking-wider text-slate-500">
                {k.label}
              </div>
              <div className="number font-semibold text-slate-100">
                {fmtPrice(k.price ?? undefined)}
              </div>
              {k.note && <div className="text-[10px] text-slate-500 mt-0.5">{k.note}</div>}
            </div>
          ))}
        </div>

        {playbook.risk_flags.length > 0 && (
          <div className="flex flex-col gap-1 pt-2 border-t border-white/5">
            <div className="text-[10px] uppercase tracking-wider text-sonar-amber">
              Risk flags
            </div>
            <ul className="flex flex-col gap-1 text-xs text-slate-400">
              {playbook.risk_flags.map((r, i) => (
                <li key={i}>· {r}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
