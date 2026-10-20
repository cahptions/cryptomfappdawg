export type Span = "1D" | "1W" | "1M" | "3M" | "1Y" | "ALL";

export interface Coin {
  symbol: string;
  name: string;
  price: string;
  price_source: "robinhood" | "history";
  bid: string | null;
  ask: string | null;
  spread: string | null;
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
  kind: "STRATEGY" | "BASELINE";
  about: string;
  spec_sha256: string;
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
