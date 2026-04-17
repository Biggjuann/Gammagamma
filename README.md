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

All data originates from the consolidated OPRA feed (all 18 US options exchanges) via
[Databento's OPRA.PILLAR](https://databento.com/datasets/OPRA.PILLAR) dataset. The
backend uses:

| schema       | purpose                                        |
|--------------|------------------------------------------------|
| `definition` | contract metadata (strike, expiry, type)       |
| `cmbp-1`     | consolidated NBBO for mid-price / IV solve     |
| `trades`     | flow (sweep/block detection, aggressor side)   |
| `statistics` | open interest                                  |

## Disclaimer

Gammagamma provides structural market analytics for educational purposes. Nothing on
this platform constitutes investment advice, a trading signal, or a recommendation.
