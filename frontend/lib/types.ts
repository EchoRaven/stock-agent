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
