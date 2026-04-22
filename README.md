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

## Disclaimer

Gammagamma provides structural market analytics for educational purposes. Nothing on
this platform constitutes investment advice, a trading signal, or a recommendation.
