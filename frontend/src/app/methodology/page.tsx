export default function MethodologyPage() {
  return (
    <div className="max-w-3xl flex flex-col gap-6">
      <section>
        <h1 className="text-2xl font-semibold tracking-tight">Methodology</h1>
        <p className="mt-2 text-sm text-slate-400">
          Gammagamma ingests consolidated OPRA data via Databento, computes Greeks with
          Black–Scholes, and aggregates exposure per-strike, per-expiry, and per-ticker.
          This page documents how every number on the dashboard is derived.
        </p>
      </section>

      <Block title="Data pipeline">
        <ul className="list-disc pl-5 space-y-1 text-sm text-slate-300">
          <li>
            <b>Definitions</b> — OPRA.PILLAR <code>definition</code> schema, pulled once
            per trading day and cached on disk (24 h TTL).
          </li>
          <li>
            <b>NBBO</b> — <code>cmbp-1</code> snapshots quantised to the minute;
            cached for 55 s so every consumer on the refresh tick shares one fetch.
          </li>
          <li>
            <b>Open interest</b> — <code>statistics</code> (stat_type = 9), refreshed
            every 12 h.
          </li>
          <li>
            <b>Flow</b> — <code>trades</code> over a 5 min rolling window, cached for the
            duration of the refresh tick. No streaming quota consumed.
          </li>
        </ul>
      </Block>

      <Block title="Greeks">
        <p className="text-sm text-slate-300">
          Delta, gamma, vega, theta, vanna, and charm are computed per contract
          analytically from Black–Scholes on a vectorised numpy/scipy kernel. IV is
          solved from mid-quote via Newton–Raphson with a bisection fallback.
        </p>
      </Block>

      <Block title="GEX convention">
        <p className="text-sm text-slate-300">
          Positive GEX → dealers are net long gamma → they <i>sell into strength, buy
          weakness</i> (dampens vol). Negative GEX → the reverse. Per-contract contribution:
        </p>
        <pre className="mt-2 rounded-md bg-ink-900 border border-white/5 p-3 text-xs">
{`sign = +1 if call else -1
GEX_$ = sign * gamma * OI * multiplier * spot² * 0.01`}
        </pre>
      </Block>

      <Block title="Structural levels">
        <ul className="list-disc pl-5 space-y-1 text-sm text-slate-300">
          <li><b>Call wall</b> — strike with largest positive call GEX.</li>
          <li><b>Put wall</b> — strike with most negative put GEX.</li>
          <li><b>Gamma flip</b> — nearest zero-crossing of cumulative net GEX to spot.</li>
          <li><b>GVWAP</b> — |GEX|-weighted mean strike.</li>
        </ul>
      </Block>

      <Block title="Signals">
        <ul className="list-disc pl-5 space-y-1 text-sm text-slate-300">
          <li><b>SSS</b> — Structural Stability Score; 50 + 50·(ΣGEX / Σ|GEX|).</li>
          <li><b>FPI</b> — Flow Pressure Index; tanh(|DEX| / Σ|GEX|) · 100.</li>
          <li><b>REGD</b> — Regime Distance; normalised distance from spot to gamma flip.</li>
          <li><b>HV</b> — Hidden Vol; tanh(range(GEX) / Σ|GEX|) · 100.</li>
          <li><b>Regime</b> — mean of fifteen normalised stress features (0–100).</li>
        </ul>
      </Block>

      <Block title="Caching discipline">
        <p className="text-sm text-slate-300">
          Every Databento call is routed through a two-tier (memory + disk) TTL cache.
          Multiple dashboard tabs share a single snapshot, and transient Databento errors
          transparently serve the last good value instead of spamming retries.
        </p>
      </Block>
    </div>
  );
}

function Block({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="card p-5">
      <h2 className="text-lg font-semibold mb-2">{title}</h2>
      {children}
    </section>
  );
}
