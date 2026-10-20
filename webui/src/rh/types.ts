export type Span = "1D" | "1W" | "1M" | "3M" | "1Y" | "ALL";

export interface Coin {
  symbol: string;
  name: string;
  price: string;
  price_source: "robinhood" | "history";
  bid: string | null;
  ask: string | null;
  spread: string | null;
  quote_crossed: boolean;
  quote_at: string | null;
  change_24h: string | null;
  spark: string[];
  roundtrip_cost: string | null;
}

export interface Account {
  equity: string;
  starting_capital: string;
  realized: string;
  unrealized: string;
  total_return: string;
  today_change: string;
  today_change_fraction: string;
  invested: string;
  learning_cash: string;
  ticket: string;
  halted: string | null;
  open_positions: number;
}

export interface Position {
  id: string;
  symbol: string;
  name: string;
  strategy_id: string;
  spec_version: number;
  quantity: string;
  cost: string;
  value: string | null;
  pnl: string | null;
  pnl_fraction: string | null;
  entry_price: string;
  stop: string | null;
  route: "MM" | "EX";
  roundtrip_cost: string;
  opened_at: string;
  days: number;
}

export interface SimRow {
  trades: number;
  wins: number;
  win_rate: string | null;
  mean_net_return: string | null;
  median_net_return: string | null;
  total_pnl_usd: string;
  max_drawdown_usd: string;
  mean_hold_days: string | null;
  pnl_per_dollar_year: string | null;
  exit_reasons: Record<string, number>;
}

export interface Strategy {
  id: string;
  kind: "STRATEGY" | "BASELINE" | "LEARNED";
  about: string;
  spec_sha256: string;
  spec_version: number;
  legacy_open: number;
  legacy_closed: number;
  legacy_realized: string;
  closed: number;
  open: number;
  wins: number;
  realized: string;
  mean_return: string | null;
  backtest: SimRow | null;
}

export interface Activity {
  at: string;
  kind: "ENTRY" | "EXIT" | "HALT" | "RESUME" | "SKIP";
  position_id: string | null;
  detail: Record<string, unknown>;
}

export interface Snapshot {
  generated_at: string;
  mode: "PAPER";
  venue: string;
  universe_source: string;
  robinhood_prices: boolean;
  last_quote_at: string | null;
  last_daily_bar: string | null;
  account: Account;
  positions: Position[];
  coins: Coin[];
  strategies: Strategy[];
  activity: Activity[];
  session: {
    last: {
      day: string;
      regime: string;
      eligible: number;
      ineligible: Record<string, string>;
      open_positions: number;
    } | null;
    last_at: string | null;
    next_at: string;
    ready_assets: number;
    total_assets: number;
    pending: Record<string, string>;
    regime: string;
    check_interval_seconds: number;
  };
  backtest: {
    kind: string;
    sessions: string;
    generated_at: string;
    btc_buy_and_hold_return: string | null;
    cost_model: {
      assumed_roundtrip_fraction: string;
      measured_symbols: string[];
    };
    universe: { source: string; with_history: number };
    caveats: string[];
  } | null;
  limits: {
    ticket_fraction: string;
    max_roundtrip_cost: string;
    max_open_positions: number;
  };
}

export interface CoinDetail extends Partial<Coin> {
  symbol: string;
  name: string;
  span: Span;
  regime: string;
  eligible: boolean;
  ineligible_reason: string | null;
  points: [string, string][];
  features: Record<string, string> | null;
  analysis_at: string;
  frames: Record<
    string,
    {
      required: number;
      available: number;
      ready: boolean;
      problem: "GAP" | "STALE" | "WARMUP" | "NONE" | null;
      last_close: string | null;
      expected_close: string;
    }
  >;
  signals: {
    strategy_id: string;
    conditions: { label: string; met: boolean }[];
  }[];
  roundtrip_cost: string | null;
  decisions: {
    at: string;
    strategy_id: string;
    action: "ENTER" | "EXIT" | "SKIP";
    reasons: string[];
  }[];
  positions: Position[];
}

export interface EquitySeries {
  span: Span;
  points: [string, string][];
}

export interface LearningProcedure {
  label: string;
  folds: number;
  trades: number;
  wins: number;
  mean_net: number | null;
  median_net: number | null;
  lcb_net: number | null;
  mean_excess_vs_btc: number | null;
  pnl_per_dollar_year: number | null;
  p_profit: number | null;
  p_beats_btc: number | null;
  holm_p_profit?: number;
  holm_p_beats_btc?: number;
  passed: boolean;
  failures: string[];
  curve: [string, number, number][];
}

export interface Learning {
  mode: "paper" | "record" | "off";
  lab_version: number;
  rules: {
    train_days: number;
    test_days: number;
    min_folds: number;
    min_trades: number;
    alpha_profit: number;
    alpha_btc: number;
    max_positions: number;
  };
  latest: {
    id: number;
    created_at: string;
    data_end: string;
    symbols: number;
    days: number;
    candidates: number;
    folds: number;
    promoted: string | null;
    procedures: Record<string, LearningProcedure>;
    leaderboard: {
      id: string;
      describe: string;
      score: number;
      trades: number;
      mean_net: number;
    }[];
    costs: { measured: number; assumed: number };
  } | null;
  selection: { procedure: string; id: string; describe: string }[];
  runs: {
    id: number;
    created_at: string;
    data_end: string;
    promoted: string | null;
  }[];
  next_run: string | null;
  forward: {
    closed_all: number;
    closed_current: number;
    open: number;
    wins: number;
    mean_net: number | null;
    upper_bound: number | null;
    benched: boolean;
    realized: string;
  };
}
