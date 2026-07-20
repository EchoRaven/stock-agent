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

export interface SignalResponse {
  symbol: string;
  rank: number;
  total: number;
  parts: Record<string, number>;
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
