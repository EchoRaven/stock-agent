/** Shapes mirrored from backend/app/api/schemas.py and routes_*.py response
 * dicts. Kept intentionally close to the Python field names. */

export type Mode = "advisory" | "semi_auto" | "full_auto";

export interface DashboardResponse {
  mode: Mode;
  as_of: string;
  positions: Record<string, { shares: number; avg_cost: number }>;
  cash: number;
  equity: number;
  circuit_breaker_tripped: boolean;
  pending_orders_count: number;
}

export interface TradeDecision {
  symbol: string;
  action: string;
  confidence: number;
  shares: number | null;
  submit_result: { status?: string; note?: string; mode?: string; [k: string]: unknown };
}

export interface TradeCycleResponse {
  as_of: string;
  mode: string;
  evaluated: number;
  skipped: unknown[];
  errors: { symbol: string; error: string }[];
  decisions: TradeDecision[];
  fills: unknown[];
  gemini_calls: number;
}

export interface SignalPart {
  score: number;
  detail: string;
}

export interface SignalResponse {
  symbol: string;
  rank: number;
  total: number;
  parts: Record<string, SignalPart>;
}

export interface OrderResponse {
  id: number;
  as_of: string;
  symbol: string;
  side: string;
  shares: number;
  status: string;
  mode: string;
  reason: string | null;
  decision_id: number | null;
}

export interface OrderActionResponse {
  order: OrderResponse | null;
  note: string;
}

export interface SettingsResponse {
  mode: Mode;
  single_position_cap_pct: number;
  total_position_cap_pct: number;
  max_new_positions_per_day: number;
  daily_loss_halt_pct: number;
  cooldown_days: number;
  initial_cash: number;
}

export type RiskParamKey = Exclude<keyof SettingsResponse, "mode">;

export interface BacktestMetrics {
  total_return: number;
  max_drawdown: number;
  sharpe: number;
  win_rate: number;
  num_fills: number;
  [key: string]: number;
}

export interface BacktestResponse {
  metrics: BacktestMetrics;
  equity_curve: { date: string; equity: number }[];
  skipped: { symbol: string; reason: string }[];
}

export type ExecutionBackend = "paper" | "futu_paper";

export interface ExecutionResponse {
  backend: ExecutionBackend;
  available_backends: ExecutionBackend[];
  futu: {
    host: string;
    port: number;
    trd_env: string;
    allow_real: boolean;
    opend_reachable: boolean;
  };
}

export interface SentimentHeadline {
  date: string;
  source: string;
  headline: string;
}

export interface SentimentResponse {
  symbol: string;
  as_of: string;
  days: number;
  news_count: number;
  sentiment: number | null;
  scored: boolean;
  headlines: SentimentHeadline[];
}

export interface FillResponse {
  order_id: number;
  symbol: string;
  side: string;
  shares: number;
  price: number;
  fill_date: string;
}

export interface SettleResponse {
  fills: FillResponse[];
  count: number;
}

export interface WatchdogResponse {
  healthy: boolean;
  mode_before: Mode;
  mode_after: Mode;
  downgraded: boolean;
  reasons: string[];
}

export type MemoryKind = "insight" | "factor" | "trade_review" | "market_note";
export type MemoryStatus = "validated" | "refuted" | "data_blocked" | "proposed" | "active";

export interface MemoryEntry {
  id: number;
  kind: MemoryKind;
  title: string;
  body: string;
  symbol: string | null;
  status: MemoryStatus;
  evidence_json: string | null;
  source: string | null;
  weight: number;
  created_at: string;
  updated_at: string;
}

export interface MemorySeedResponse {
  inserted: number;
}

export type FactorMineVerdict = "validated" | "no_improvement" | "refuted" | "error";

export interface FactorMineWindowSummary {
  base: BacktestMetrics;
  cand: BacktestMetrics;
}

export interface FactorMineResult {
  factor: string;
  params: Record<string, number>;
  verdict: FactorMineVerdict;
  /** Present for non-error verdicts: per-window base vs. candidate metrics. */
  windows?: Record<string, FactorMineWindowSummary>;
  entry_id?: number;
  /** Present only when verdict === "error" (proposal failed before backtest). */
  error?: string;
}

export interface FactorMineResponse {
  results: FactorMineResult[];
  count: number;
}

export interface StockPricePoint {
  date: string;
  close: number | null;
  volume?: number | null;
}

export interface StockSummary {
  num_bars: number;
  last_date?: string | null;
  last_close: number | null;
  chg_1d?: number | null;
  pct_1d: number | null;
  chg_5d?: number | null;
  pct_5d: number | null;
  chg_20d?: number | null;
  pct_20d: number | null;
  sma20: number | null;
  sma50: number | null;
  rsi14: number | null;
  avg_vol_20: number | null;
  high_52w: number | null;
  low_52w: number | null;
}

export interface StockNewsItem {
  date: string;
  source: string;
  headline: string;
  summary: string;
  url: string;
}

export interface FundamentalPoint {
  end: string;
  value: number;
  fiscal: string;
}

export interface StockFundamentals {
  revenue: FundamentalPoint[];
  net_income: FundamentalPoint[];
  eps: FundamentalPoint[];
}

export interface StockDetail {
  symbol: string;
  as_of: string;
  days: number;
  price_series: StockPricePoint[];
  summary: StockSummary;
  news: StockNewsItem[];
  fundamentals: StockFundamentals;
}

export interface CumulativePoint {
  date: string;
  cum_pnl: number;
}

export interface PerformanceResponse {
  closed_trades: number;
  realized_pnl_total: number;
  win_rate: number | null;
  wins: number;
  losses: number;
  avg_win: number | null;
  avg_loss: number | null;
  avg_holding_days: number | null;
  cumulative_pnl_series: CumulativePoint[];
  cash: number;
  open_positions: number;
  open_positions_cost_value: number;
  equity_at_cost: number;
  initial_cash: number;
}

export interface DecisionHistoryItem {
  id: number;
  as_of: string;
  symbol: string;
  action: string;
  confidence: number;
  mode: string;
  chair_verdict: string;
  created_at: string;
}

export type CommitteeRoleKey = "technical" | "fundamental" | "sentiment" | "bear";

export interface StockAnalysis {
  symbol: string;
  as_of: string;
  held: boolean;
  committee: Record<CommitteeRoleKey, { summary: string }>;
  chair: { verdict: string; bear_rebuttal: string };
  action: "buy" | "sell" | "hold";
  confidence: number;
  note: string;
}
