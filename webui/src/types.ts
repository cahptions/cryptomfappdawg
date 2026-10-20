export type NumberValue = number | string | null | undefined;
export type View =
  | "overview"
  | "scanner"
  | "paper"
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
    armed?: boolean | null;
    buy_enabled?: boolean;
    signer_status?: string;
    reason?: string;
    gates?: {
      name: string;
      ok: boolean;
      reason?: string;
      passed?: boolean;
      detail?: string;
    }[];
    max_capital_sol?: string;
    capital_ceiling_sol?: string;
    guardian_age_seconds?: number | null;
    engine_age_seconds?: number | null;
    book?: LiveBookSummary | null;
  };
  regime?: { status?: string; label?: string; reason?: string };
  readiness?: {
    verdict?: string;
    next_step?: string;
    as_of?: string | null;
    recorded_at?: string;
    path?: string;
    calendar_days?: number;
    closed_trades?: number;
    deciding_versions?: string[];
  } | null;
  council?: CouncilSummary;
}
export type Detail = {
  title: string;
  endpoint?: string;
  value?: unknown;
  kind: "candidate" | "position" | "strategy" | "evidence";
};

/** /api/paper: the learning book in plain English (desk.web.paper_tab). */
export type Tone =
  | "positive"
  | "negative"
  | "warning"
  | "neutral"
  | "info"
  | "critical";
export interface PaperStatus {
  headline: string;
  tone?: Tone;
  detail?: string;
  halted?: string | null;
  guardian_age_seconds?: number | null;
  engine_age_seconds?: number | null;
}
export interface PaperBook {
  started_at?: string;
  sol_usd_at_start?: NumberValue;
  cash_sol?: NumberValue;
  deposit_sol?: NumberValue;
  base_sol?: NumberValue;
  sizing_capital_sol?: NumberValue;
  realized_sol?: NumberValue;
  realized_observed_sol?: NumberValue;
  realized_stress_sol?: NumberValue;
  unrealized_sol?: NumberValue;
  return_on_base?: NumberValue;
  next_ticket_sol?: NumberValue;
  next_ticket_blockers?: string[];
  open_positions?: number;
  max_positions?: number;
  closed_trades?: number;
  win_rate?: NumberValue;
  hours_running?: number;
}
export interface PaperOpenPosition {
  id: string;
  mint?: string;
  symbol?: string;
  strategy?: string;
  state?: string;
  opened_at?: string;
  held_seconds?: number;
  ticket_sol?: NumberValue;
  mark_sol?: NumberValue;
  mark_kind?: string | null;
  mark_at?: string | null;
  return?: NumberValue;
  peak_return?: NumberValue;
  exit_reason?: string;
  exit_attempts?: number;
  regime?: string;
  entry_fees_sol?: NumberValue;
  rent_locked_sol?: NumberValue;
  conviction?: boolean;
  hard_stop_sol?: NumberValue;
  hard_stop_return?: NumberValue;
  trailing_armed?: boolean;
  trailing_arms_at_sol?: NumberValue;
  trailing_stop_sol?: NumberValue;
  max_hold_seconds?: number;
  hold_remaining_seconds?: number;
}
export interface PaperClosedTrade {
  id: string;
  mint?: string;
  symbol?: string;
  strategy?: string;
  opened_at?: string;
  closed_at?: string;
  hold_seconds?: number;
  ticket_sol?: NumberValue;
  exit_sol?: NumberValue;
  pnl_sol?: NumberValue;
  return?: NumberValue;
  observed_return?: NumberValue;
  stress_return?: NumberValue;
  exit_reason?: string;
  regime?: string;
  fees_sol?: NumberValue;
  fees_pct_of_ticket?: NumberValue;
  conviction?: boolean;
  fee_source?: string;
  breakdown?: Record<string, unknown> | null;
}
export type ActivityGroup = "trade" | "signal" | "skip" | "look" | "control";
export interface PaperActivity {
  id: number | string;
  at?: string;
  kind: string;
  group?: ActivityGroup;
  title: string;
  detail?: string;
  tone?: Tone;
  position_id?: string | null;
  candidate_id?: string | null;
  mint?: string | null;
  symbol?: string;
}
export interface PaperFunnel {
  decisions?: number;
  no_trade?: number;
  signals?: number;
  blocked?: number;
  skipped_size?: number;
  skipped_cost?: number;
  attempts?: number;
  failed?: number;
  opened?: number;
  closed?: number;
}
export interface PaperFeeEstimate {
  source?: string;
  observations?: number;
  per_transaction_lamports?: NumberValue;
  signature_lamports?: NumberValue;
  priority_lamports?: NumberValue;
  priority_p50_lamports?: NumberValue;
  priority_p90_lamports?: NumberValue;
  rent_lamports?: NumberValue;
  transactions_per_round_trip?: number;
  rule?: string;
}
export interface PaperTab {
  initialized: boolean;
  status: PaperStatus;
  book?: PaperBook;
  open?: PaperOpenPosition[];
  closed?: PaperClosedTrade[];
  activity?: PaperActivity[];
  funnel?: { last_24h?: PaperFunnel; all_time?: PaperFunnel };
  blocked_reasons?: Record<string, number>;
  strategies?: {
    strategy: string;
    closed?: number;
    open?: number;
    win_rate?: NumberValue;
    mean_return?: NumberValue;
    pnl_sol?: NumberValue;
  }[];
  exit_reasons?: Record<string, number>;
  curve?: { at: string; realized_sol: NumberValue }[];
  replica?: {
    equity_sol?: NumberValue;
    return?: NumberValue;
    trades_taken?: number;
    skipped?: Record<string, number>;
    open_at_end?: number;
    max_drawdown?: NumberValue;
    halted_by_cumulative_drawdown?: boolean;
    note?: string;
  };
  fees?: {
    paid_total_sol?: NumberValue;
    entry_tx_sol?: NumberValue;
    exit_tx_sol?: NumberValue;
    failed_tx_sol?: NumberValue;
    rent_locked_sol?: NumberValue;
    average_round_trip_pct?: NumberValue;
    current_estimate?: PaperFeeEstimate;
    explanation?: string[];
  };
  moonshot?: {
    strategy: string;
    version?: string | null;
    closed?: number;
    open?: number;
    wins?: number;
    pnl_sol?: NumberValue;
    sized_up_trades?: number;
    conviction_evidence?: string;
    conviction_detail?: string;
    normal_ticket_sol?: NumberValue;
    sized_up_ticket_sol?: NumberValue;
    rule?: string;
  };
  notes?: string[];
}

/** Read-only summary of the LIVE ledger; amounts are what the wallet's balances changed by. */
export interface LiveBookSummary {
  started: boolean;
  error?: string;
  deposits_sol?: string;
  cash_sol?: string;
  equity_sol?: string;
  realized_sol?: string;
  closed_trades?: number;
  halted?: string | null;
  pending_requests?: number;
  open?: {
    id: string;
    mint: string;
    state: string;
    strategy: string;
    opened_at: string;
    ticket_sol: string;
    value_sol: string | null;
    exit_reason: string | null;
  }[];
}

/** The evidence council's latest records: two research advisors that never change a trade. */
export interface CouncilSummary {
  enabled: boolean;
  influence: "NONE";
  last_24h: Record<string, Record<string, number>>;
  latest: {
    advisor: string;
    stance: string;
    reasons: string[];
    mint: string;
    source: string;
    primary_action: string;
    decision_at: string;
    signal: Record<string, string>;
  }[];
  benchmark_minutes: number;
}
