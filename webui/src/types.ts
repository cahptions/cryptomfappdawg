export type NumberValue = number | string | null | undefined;
export type View =
  | "overview"
  | "scanner"
  | "positions"
  | "strategies"
  | "research"
  | "audit"
  | "system"
  | "live";
export interface Candidate {
  id: string;
  mint: string;
  pool_address?: string;
  symbol?: string;
  name?: string;
  reason?: string;
  state?: string;
  stage?: number;
  last_rule_id?: string;
  observed_at?: string;
  price_usd?: NumberValue;
  liquidity_usd?: NumberValue;
  volume_h1?: NumberValue;
}
export interface Position {
  id: string;
  mint?: string;
  symbol?: string;
  strategy?: string;
  state?: string;
  opened_at?: string;
  ticket_sol?: NumberValue;
  mark_sol?: NumberValue;
  mark_kind?: string;
  mark_at?: string;
  last_mark_at?: string;
}
export interface ConfidenceCell {
  dimension: string;
  status?: string;
  estimate?: NumberValue;
  lower?: NumberValue;
  upper?: NumberValue;
  evidence?: unknown;
  detail?: string;
}
export interface Matrix {
  strategy_id: string;
  strategy_version?: string;
  readiness?: string;
  trades?: number;
  cells?: ConfidenceCell[];
}
export interface Health {
  provider: string;
  state?: string;
  last_success_at?: string;
  last_error?: string;
}
export interface Worker {
  name: string;
  pid?: number;
  age_seconds?: number;
  active?: boolean;
  managed?: boolean;
}
export interface Job {
  name: string;
  pid?: number;
  started_at?: string;
  returncode?: number | null;
}
export interface AuditEvent {
  id?: string;
  at?: string;
  kind?: string;
  position_id?: string;
  candidate_id?: string;
  detail?: unknown;
  payload?: unknown;
}
export interface Snapshot {
  generated_at?: string;
  mode?: string;
  status?: { label?: string; halted?: boolean; reason?: string };
  paper?: {
    initialized?: boolean;
    cash_sol?: NumberValue;
    base_sol?: NumberValue;
    sizing_capital_sol?: NumberValue;
    realized_pnl_sol?: Record<string, NumberValue>;
    unrealized_executable_sol?: NumberValue;
    open_positions?: number;
    closed_trades?: number;
    open?: Position[];
    by_strategy?: Record<
      string,
      {
        trades?: number;
        win_rate?: NumberValue;
        mean_return?: NumberValue;
        pnl_sol?: NumberValue;
      }
    >;
    replica_100_usd_account?: {
      equity_sol?: NumberValue;
      return?: NumberValue;
      trades_taken?: number;
      note?: string;
      max_drawdown?: NumberValue;
      skipped?: Record<string, number>;
    };
    reconciliation?: unknown;
    realized_vs_estimated?: string;
    hours_since_start?: number;
    entry_blocked_reasons?: Record<string, number>;
    [key: string]: unknown;
  };
  scanner?: Candidate[];
  funnel?: {
    by_state?: Record<string, number>;
    by_reason?: Record<string, number>;
    rejection_reasons?: Record<string, number>;
  };
  health?: Health[];
  workers?: Worker[];
  jobs?: Job[];
  confidence?: {
    generated_at?: string;
    closed_trades?: number;
    matrices?: Matrix[];
  };
  audit?: AuditEvent[];
  equity?: {
    at: string;
    value_sol: NumberValue;
    kind?: string;
    label?: string;
  }[];
  system?: {
    memory_available_mb?: NumberValue;
    memory_total_mb?: NumberValue;
    swap_used_mb?: NumberValue;
    disk_free_gb?: NumberValue;
    db_bytes?: NumberValue;
    memory?: {
      total_mb?: NumberValue;
      available_mb?: NumberValue;
      swap_used_mb?: NumberValue;
    };
    databases?: Record<
      string,
      { bytes?: NumberValue; wal_bytes?: NumberValue }
    >;
    [key: string]: unknown;
  };
  live?: {
    armed?: boolean;
    buy_enabled?: boolean;
    reason?: string;
    gates?: {
      name: string;
      ok: boolean;
      reason?: string;
      passed?: boolean;
      detail?: string;
    }[];
    max_capital_sol?: string;
  };
  regime?: { status?: string; label?: string; reason?: string };
}
export type Detail = {
  title: string;
  endpoint?: string;
  value?: unknown;
  kind: "candidate" | "position" | "strategy" | "evidence";
};
