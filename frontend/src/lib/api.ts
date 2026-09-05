export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";

export type ExpiryFilter = "all" | "0dte" | "weekly" | "monthly" | "leaps";

export type Bundle = {
  underlying: string;
  asof: string;
  spot: number;
  totals: { gex: number; dex: number; vanna: number; charm: number };
  levels: {
    call_wall: number | null;
    put_wall: number | null;
    gamma_flip: number | null;
    gvwap: number | null;
    major_call_walls: number[];
    major_put_walls: number[];
  };
  signals: {
    sss: number;
    fpi: number;
    regd: number;
    hv: number;
    gvwap: number;
    regime_score: number;
  };
  playbook: Playbook;
  rows: ChainRow[];
  per_strike: Record<string, { call_gex: number; put_gex: number; net_gex: number; oi: number }>;
  per_expiry: Record<string, number>;
  flow: FlowRow[];
};

export type Playbook = {
  bias: "MEAN REVERT" | "TREND" | "PIVOT WATCH" | "DIRECTIONAL";
  regime: "COMPRESSED" | "NEUTRAL" | "TRANSITION" | "ELEVATED";
  headline: string;
  bullets: string[];
  key_levels: { label: string; price: number | null; note: string }[];
  risk_flags: string[];
};

export type ChainRow = {
  instrument_id: number;
  strike: number;
  expiry: string;
  dte: number;
  type: "C" | "P";
  mid: number;
  oi: number;
  iv: number;
  delta: number;
  gamma: number;
  vanna: number;
  charm: number;
  gex: number;
};

export type FlowRow = {
  instrument_id: number;
  strike: number;
  expiry: string;
  type: "C" | "P";
  premium: number;
  size: number;
  side: string;
  sweep: boolean;
  block: boolean;
  oi_exceedance: number;
  dealer_side: string;
  ts: string;
};

export type SummaryRow = {
  underlying: string;
  asof?: string;
  spot?: number;
  total_gex?: number;
  total_dex?: number;
  call_wall?: number | null;
  put_wall?: number | null;
  gamma_flip?: number | null;
  regime_score?: number;
  sss?: number;
  fpi?: number;
  error?: string;
};

export async function fetchJSON<T>(url: string): Promise<T> {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json() as Promise<T>;
}

export type DailyRange = {
  underlying: string;
  spot: number;
  asof: string;
  anchor_expiry: string;
  dte_years: number;
  atm_iv: number | null;
  expected_move_1sd: number | null;
  upper_1sd: number | null;
  lower_1sd: number | null;
  atm_straddle: number | null;
  call_25d_strike: number | null;
  put_25d_strike: number | null;
  call_25d_iv: number | null;
  put_25d_iv: number | null;
  skew_tilt: number | null;
  call_wall: number | null;
  put_wall: number | null;
  gamma_flip: number | null;
  regime: "positive_gamma" | "negative_gamma" | "unknown";
  read_headline: string;
  read_bullets: string[];
  error?: string;
};

export const endpoints = {
  summary: () => `${API_BASE}/api/summary`,
  universe: () => `${API_BASE}/api/universe`,
  ticker: (sym: string, expiry: ExpiryFilter = "all") =>
    `${API_BASE}/api/ticker/${sym}?expiry=${expiry}`,
  range: () => `${API_BASE}/api/range`,
  rangeSingle: (sym: string) => `${API_BASE}/api/range/${sym}`,
  ws: (sym: string) =>
    `${API_BASE.replace(/^http/, "ws")}/ws/ticker/${sym}`
};
