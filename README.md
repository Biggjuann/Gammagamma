# Gammagamma

A replica of [gammasonar.com](https://gammasonar.com): real-time gamma exposure (GEX) intelligence
for options traders. Consolidated OPRA data is delivered via **Databento** rather than
ThetaData. Greeks and structural levels are recomputed from live options snapshots every
60 seconds during US market hours.

## Features

- **GEX engine** — delta, gamma, vanna, charm computed per contract / per-strike / per-expiry
  across ~95 tickers (SPX, QQQ, single names, ETFs).
- **Structural levels** — call walls, put walls, gamma flip, GVWAP.
- **Regime detection** — 15-feature composite stress score (0–100).
- **Original signals** — SSS, FPI, REGD, HV, GVWAP.
- **Options flow** — sweep / block detection, OI exceedance, dealer-side inference.
- **Expiry filters** — 0DTE through LEAPS.
- **Chart overlay** — strike-by-strike 7-layer GEX stack over price.
- **Live updates** — WebSocket push every 60 s during RTH (09:30–16:00 ET); pre-market
  structural levels by 06:35 ET.

## Architecture

```
┌────────────┐    OPRA.PILLAR     ┌──────────────┐   WebSocket   ┌──────────────┐
│  Databento │ ─── snapshots ───> │  Compute     │ ─── 60 s ───> │  Next.js UI  │
│  (MBP-1,   │   definitions,     │  engine      │               │  (dashboard) │
│  trades,   │   trades, NBBO     │  Python +    │   REST        │              │
│  defs)     │                    │  FastAPI     │ <───────────> │              │
└────────────┘                    └──────────────┘               └──────────────┘
```

- **backend/** — FastAPI service. Pulls OPRA via the Databento SDK, computes Greeks
  with Black–Scholes, aggregates GEX, scores regime, ships snapshots over REST/WS.
- **frontend/** — Next.js 14 (App Router) with TailwindCSS + lightweight-charts.

## Getting started

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABENTO_API_KEY=db-...          # your Databento key
export RISK_FREE_RATE=0.0525
uvicorn app.main:app --reload --port 8000
```

### Frontend

```bash
cd frontend
npm install
NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
```

Open <http://localhost:3000>.

## Data source

Default: **Yahoo Finance options chain** — free, no API key required, ~15-20 minute
delayed, includes real open interest. Perfect for overnight / structural GEX analysis.

Optional: **Databento OPRA.PILLAR** (paid) — consolidated OPRA data, batch or live.
Set `DATA_SOURCE=databento` and provide `DATABENTO_API_KEY` to route through it.

| Source    | Cost       | Delay      | OI         | Coverage                     |
|-----------|------------|------------|------------|------------------------------|
| Yahoo     | Free       | 15-20 min  | Real       | SPY, QQQ, IWM, SPX/NDX/RUT   |
| Databento | $30-2k/mo  | 4h → live  | Real       | All OPRA contracts           |

## Troubleshooting: no options data

`GET /api/diagnostics` reports exactly where the chain pipeline is losing
data — the active source, whether its credentials are present and working,
and the contract / quote / OI counts the client is actually returning. Add
`?symbol=SPY` to probe one ticker. No secrets are included in the response.

```bash
curl -s $API/api/diagnostics | jq .summary
```

| `summary` says | Cause | Fix |
|---|---|---|
| `... not set — the chain fetch cannot run` | The active source's env vars are missing | Set them and redeploy |
| `Schwab token unavailable: ...` | Share endpoint down, or its refresh token expired (Schwab refresh tokens last 7 days) | Re-authorize the owning service |
| `NO CHAIN` | Source reachable but returned zero contracts | Check symbology (indices need `$SPX` on Schwab, `^SPX` on Yahoo) |
| `NO QUOTES` / `NO PRICES` | Contracts listed with no bid/ask | Off-hours on a quote-only source; wait for RTH |
| `NO OPEN INTEREST` | OI stream failed | GEX falls back to placeholder OI=1; shape is right, absolute $ is a lower bound |

Yahoo is rate-limited from most data-center IPs (HTTP 429), so it is not a
viable production source — use Schwab or Databento for a deployed instance.

`POST /api/cache/reset` drops the cached chain payloads and forces the next
request to re-fetch from the vendor (`?deep=false` rebuilds bundles only).

## Disclaimer

Gammagamma provides structural market analytics for educational purposes. Nothing on
this platform constitutes investment advice, a trading signal, or a recommendation.
