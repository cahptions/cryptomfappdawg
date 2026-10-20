import { useEffect, useMemo, useState } from "react";
import { usePoll } from "./api";
import LineChart, { Sparkline } from "./LineChart";
import type {
  Activity,
  Coin,
  CoinDetail,
  EquitySeries,
  Learning,
  Position,
  Snapshot,
  Span,
  Strategy,
} from "./types";

const SPANS: Span[] = ["1D", "1W", "1M", "3M", "1Y", "ALL"];
const STRATEGY_NAMES: Record<string, string> = {
  R1_TREND: "Trend breakout",
  R2_XMOM: "Relative momentum",
  R3_PULLBACK: "Uptrend pullback",
  B1_HOLD_BTC: "Hold BTC · baseline",
  B2_RANDOM: "Random coin · baseline",
  L1_LEARNED: "Learned strategy",
};
const REASONS: Record<string, string> = {
  NO_ROBINHOOD_PRICE: "no Robinhood price yet (key not set up)",
  ROUNDTRIP_COST_ABOVE_CEILING: "buying and selling would cost too much",
  BOOK_FULL: "already holding the maximum",
  STALE_QUOTE: "price too old",
  STOP_FILE: "stop switch is on",
  SIZE_OUTSIDE_PAIR_LIMITS: "size outside Robinhood's order limits",
  NOT_TRADABLE_ON_ROBINHOOD: "not tradable on Robinhood",
  NO_FRESH_ESTIMATE: "Robinhood gave no fresh price",
  BREAKOUT_20D: "20-day breakout",
  ABOVE_SMA50: "above 50-day average",
  BTC_RISK_ON: "BTC in an uptrend",
  TOP3_28D_MOMENTUM: "top-3 28-day run",
  BEATS_BTC: "beat BTC",
  RSI2_BELOW_10: "sharp 2-day dip",
  UPTREND_50_OVER_100: "in an uptrend",
  BASELINE_HOLD: "baseline: hold BTC",
  BASELINE_RANDOM: "baseline: random pick",
  DONCHIAN_EXIT: "closed at a 10-day low",
  TRAILING_ATR: "trailing stop",
  MAX_HOLD: "held for the maximum time",
  HARD_STOP: "stop price hit",
  REBALANCE_OUT: "dropped out of the top 3",
  REGIME_OFF: "BTC trend turned down",
  MEAN_REVERTED: "bounced back",
  HOLD_COMPLETE: "7 days done",
  STALE_HISTORY: "price history is behind",
  SHORT_HISTORY: "less than 120 days of history",
  THIN_VOLUME: "too little trading volume",
  NO_HISTORY: "no price history",
};

// why a strategy wanted the coin; anything else on a SKIP is what blocked it
const ENTRY_REASONS = new Set([
  "BREAKOUT_20D",
  "ABOVE_SMA50",
  "BTC_RISK_ON",
  "TOP3_28D_MOMENTUM",
  "BEATS_BTC",
  "RSI2_BELOW_10",
  "UPTREND_50_OVER_100",
  "BASELINE_HOLD",
  "BASELINE_RANDOM",
]);

const FRAME_PROBLEMS: Record<string, string> = {
  GAP: "periods with no trades on Coinbase",
  STALE: "newest candle not downloaded yet",
  WARMUP: "not enough history yet",
  NONE: "no price history from Coinbase",
};

function reason(code: string): string {
  if (code.startsWith("HALTED:")) return "entries halted";
  return REASONS[code] ?? code.toLowerCase().replaceAll("_", " ");
}

function num(value: string | null | undefined): number | null {
  if (value === null || value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

const usdFormat = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
});

// money amounts: always cents
function usd(value: number | string | null | undefined): string {
  const n = typeof value === "number" ? value : num(value);
  return n === null ? "—" : usdFormat.format(n);
}

// coin prices: cents above $1, significant digits below (PEPE, SHIB)
function price(value: number | string | null | undefined): string {
  const n = typeof value === "number" ? value : num(value);
  if (n === null) return "—";
  if (Math.abs(n) > 0 && Math.abs(n) < 1) {
    return `$${n.toLocaleString("en-US", { maximumSignificantDigits: 4 })}`;
  }
  return usdFormat.format(n);
}

function signedUsd(value: number | null): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : "−"}${usd(Math.abs(value))}`;
}

function signedPrice(value: number | null): string {
  if (value === null) return "—";
  return `${value >= 0 ? "+" : "−"}${price(Math.abs(value))}`;
}

function pct(fraction: number | null, digits = 2): string {
  if (fraction === null) return "—";
  return `${fraction >= 0 ? "+" : "−"}${Math.abs(fraction * 100).toFixed(digits)}%`;
}

function tone(value: number | null): string {
  if (value === null || value === 0) return "flat";
  return value > 0 ? "up" : "down";
}

function when(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return withTime
    ? date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : date.toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
}

function useRoute(): string[] {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const update = () => {
      setHash(window.location.hash);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", update);
    return () => window.removeEventListener("hashchange", update);
  }, []);
  return hash.replace(/^#\/?/, "").split("/").filter(Boolean);
}

function Mark() {
  return (
    <svg viewBox="0 0 40 40" className="rh-mark" aria-hidden>
      <path d="m5 10 15 6L35 5 25 24l-5 11-5-12Z" />
    </svg>
  );
}

function SpanPills({
  value,
  onChange,
  toneClass,
}: {
  value: Span;
  onChange: (span: Span) => void;
  toneClass: string;
}) {
  return (
    <div className={`rh-spans ${toneClass}`} role="tablist" aria-label="Range">
      {SPANS.map((span) => (
        <button
          key={span}
          role="tab"
          aria-selected={span === value}
          className={span === value ? "is-active" : ""}
          onClick={() => onChange(span)}
        >
          {span}
        </button>
      ))}
    </div>
  );
}

function Header({ snap }: { snap: Snapshot | null }) {
  const route = useRoute();
  const here = route[0] ?? "";
  const links: [string, string][] = [
    ["", "Investing"],
    ["strategies", "Strategies"],
    ["learning", "Learning"],
    ["activity", "Activity"],
    ["system", "System"],
  ];
  return (
    <header className="rh-header">
      <a className="rh-brand" href="#/">
        <Mark />
        <span>KESTREL</span>
        <span className="rh-venue">Crypto</span>
      </a>
      <nav aria-label="Main">
        {links.map(([path, label]) => (
          <a
            key={label}
            href={`#/${path}`}
            className={here === path ? "is-active" : ""}
            aria-current={here === path ? "page" : undefined}
          >
            {label}
          </a>
        ))}
      </nav>
      <div className="rh-status">
        <span className="rh-paper">PAPER</span>
        <span
          className={`rh-dot ${snap?.robinhood_prices ? "is-live" : ""}`}
          title={
            snap?.robinhood_prices
              ? "Robinhood prices are flowing"
              : "No live Robinhood prices (see System)"
          }
        />
      </div>
    </header>
  );
}

function Watchlist({ coins }: { coins: Coin[] }) {
  return (
    <aside className="rh-watch" aria-label="Crypto">
      <div className="rh-watch-head">
        <h2>Crypto</h2>
        <span>{coins.length}</span>
      </div>
      <ul>
        {coins.map((coin) => {
          const change = num(coin.change_24h);
          return (
            <li key={coin.symbol}>
              <a href={`#/coin/${coin.symbol}`}>
                <span className="rh-watch-name">
                  <strong>{coin.symbol.replace("-USD", "")}</strong>
                  <small>{coin.name}</small>
                </span>
                <Sparkline values={coin.spark.map(Number)} />
                <span className="rh-watch-price">
                  <span>{price(coin.price)}</span>
                  <span className={`rh-chip ${tone(change)}`}>
                    {pct(change)}
                  </span>
                </span>
              </a>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}

function PositionRows({ positions }: { positions: Position[] }) {
  if (!positions.length) {
    return (
      <p className="rh-empty">
        No paper positions. The desk checks newly closed 15-minute candles as
        data arrives. It buys only when a setup and every risk and cost check
        qualify.
      </p>
    );
  }
  return (
    <ul className="rh-rows">
      {positions.map((p) => {
        const pnl = num(p.pnl);
        return (
          <li key={p.id}>
            <a href={`#/coin/${p.symbol}`}>
              <span>
                <strong>{p.symbol.replace("-USD", "")}</strong>
                <small>
                  {STRATEGY_NAMES[p.strategy_id] ?? p.strategy_id} · v
                  {p.spec_version} · {p.days}d
                  {p.stop ? ` · stop ${price(p.stop)}` : ""}
                </small>
              </span>
              <span className="rh-right">
                <strong>{usd(p.value)}</strong>
                <small className={tone(pnl)}>
                  {signedUsd(pnl)} ({pct(num(p.pnl_fraction))})
                </small>
              </span>
            </a>
          </li>
        );
      })}
    </ul>
  );
}

function activityText(a: Activity): {
  title: string;
  sub: string;
  value?: string;
  toneValue?: number | null;
} {
  const d = a.detail as Record<string, string | string[] | undefined>;
  const symbol = String(d.symbol ?? "").replace("-USD", "");
  const name =
    STRATEGY_NAMES[String(d.strategy_id)] ?? String(d.strategy_id ?? "");
  if (a.kind === "ENTRY") {
    return {
      title: `Bought ${symbol}`,
      sub: `${name} · ${d.route === "EX" ? "exchange routing" : "Robinhood market makers"}`,
      value: usd(String(d.cost_usd)),
    };
  }
  if (a.kind === "EXIT") {
    const pnl = num(String(d.pnl_usd));
    return {
      title: `Sold ${symbol}`,
      sub: `${name} · ${reason(String(d.reason))}`,
      value: signedUsd(pnl),
      toneValue: pnl,
    };
  }
  if (a.kind === "SKIP") {
    const reasons = (d.reasons as string[] | undefined) ?? [];
    const blocked = reasons.filter((r) => !ENTRY_REASONS.has(r));
    return {
      title: `Skipped ${symbol}`,
      sub: `${name} · ${blocked.map(reason).join(", ") || "blocked"}`,
    };
  }
  return {
    title: a.kind === "HALT" ? "Entries halted" : "Entries resumed",
    sub: String(d.reason ?? ""),
  };
}

function ActivityRows({ items, limit }: { items: Activity[]; limit?: number }) {
  const rows = limit ? items.slice(0, limit) : items;
  if (!rows.length) return <p className="rh-empty">Nothing yet.</p>;
  return (
    <ul className="rh-rows">
      {rows.map((a, i) => {
        const text = activityText(a);
        return (
          <li key={`${a.at}-${i}`}>
            <div>
              <span>
                <strong>{text.title}</strong>
                <small>
                  {when(a.at)} · {text.sub}
                </small>
              </span>
              {text.value && (
                <span className="rh-right">
                  <strong
                    className={
                      text.toneValue !== undefined
                        ? tone(text.toneValue ?? null)
                        : ""
                    }
                  >
                    {text.value}
                  </strong>
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}

function Home({ snap }: { snap: Snapshot }) {
  const [span, setSpan] = useState<Span>("1M");
  const [hover, setHover] = useState<number | null>(null);
  const { data: series } = usePoll<EquitySeries>(
    `/api/rh/equity?span=${span}`,
    60_000,
  );
  const values = useMemo(
    () => (series?.points ?? []).map(([, v]) => Number(v)),
    [series],
  );
  const labels = useMemo(
    () =>
      (series?.points ?? []).map(([t]) =>
        when(t, span === "1D" || span === "1W"),
      ),
    [series, span],
  );
  const account = snap.account;
  const shown =
    hover !== null && values[hover] !== undefined
      ? values[hover]
      : num(account.equity);
  const base = values.length ? values[0] : num(account.starting_capital);
  const change =
    hover !== null && shown !== null && base !== null
      ? shown - base
      : num(account.today_change);
  const changeFraction =
    hover !== null && shown !== null && base
      ? shown / base - 1
      : num(account.today_change_fraction);
  return (
    <>
      <section className="rh-hero" aria-label="Your $100 account">
        <p className="rh-eyebrow">Your $100 account · paper</p>
        <h1 className="rh-big">{usd(shown)}</h1>
        <p className={`rh-change ${tone(change)}`}>
          {signedUsd(change)} ({pct(changeFraction)}){" "}
          <span>{hover !== null ? labels[hover] : "Today"}</span>
        </p>
        <LineChart
          values={values}
          labels={labels}
          onHover={setHover}
          ariaLabel="Account value"
        />
        <SpanPills
          value={span}
          onChange={setSpan}
          toneClass={tone(
            values.length ? values[values.length - 1] - values[0] : 0,
          )}
        />
      </section>
      {account.halted && (
        <p className="rh-banner">
          Entries are halted ({account.halted}). Exits keep running.
        </p>
      )}
      <section className="rh-card rh-power">
        <div>
          <span>Next ticket</span>
          <strong>{usd(account.ticket)}</strong>
        </div>
        <div>
          <span>Realized</span>
          <strong className={tone(num(account.realized))}>
            {signedUsd(num(account.realized))}
          </strong>
        </div>
        <div>
          <span>Open</span>
          <strong>{account.open_positions}</strong>
        </div>
      </section>
      <section aria-labelledby="pos-h">
        <h2 id="pos-h" className="rh-h2">
          Positions
        </h2>
        <PositionRows positions={snap.positions} />
      </section>
      <section aria-labelledby="act-h">
        <h2 id="act-h" className="rh-h2">
          Recent activity <a href="#/activity">See all</a>
        </h2>
        <ActivityRows items={snap.activity} limit={6} />
      </section>
    </>
  );
}

function CoinPage({ symbol, snap }: { symbol: string; snap: Snapshot | null }) {
  const [span, setSpan] = useState<Span>("1M");
  const [interval, setInterval] = useState("1h");
  const [hover, setHover] = useState<number | null>(null);
  const { data, error } = usePoll<CoinDetail>(
    `/api/rh/coin/${symbol}?span=${span}&interval=${interval}`,
    60_000,
  );
  const values = useMemo(
    () => (data?.points ?? []).map(([, v]) => Number(v)),
    [data],
  );
  const labels = useMemo(
    () =>
      (data?.points ?? []).map(([t]) =>
        when(t, span === "1D" || span === "1W"),
      ),
    [data, span],
  );
  if (error) return <p className="rh-empty">{error}</p>;
  if (!data) return <p className="rh-empty">Loading…</p>;
  const last = values.length
    ? values[values.length - 1]
    : num(data.price ?? null);
  const shown =
    hover !== null ? values[hover] : (num(data.price ?? null) ?? last);
  const first = values.length ? values[0] : null;
  const change = shown !== null && first !== null ? shown - first : null;
  const f = data.features ?? {};
  const coin = snap?.coins.find((c) => c.symbol === symbol);
  const stats: [string, string][] = [
    ["Bid", price(data.bid ?? null)],
    ["Ask", price(data.ask ?? null)],
    [
      data.quote_crossed ? "Crossed quote spread" : "Spread",
      data.spread ? pct(Number(data.spread), 3).replace("+", "") : "—",
    ],
    [
      "Round trip (measured)",
      data.roundtrip_cost
        ? pct(Number(data.roundtrip_cost)).replace("+", "")
        : "not measured yet",
    ],
    ["24-hour return", f.return24h ? pct(Number(f.return24h), 1) : "—"],
    ["Hourly RSI2", f.rsi2 ? Number(f.rsi2).toFixed(1) : "—"],
    ["Prior 20-hour high", price(f.high_prior20)],
    ["4-hour EMA20", price(f.four_ema20)],
    ["4-hour EMA50", price(f.four_ema50)],
    ["Hourly range (ATR 20)", price(f.atr20)],
    ["Median daily volume", usd(f.median_dollar_volume30)],
    [
      "Price from",
      coin?.price_source === "robinhood" ? "Robinhood" : "price history",
    ],
  ];
  return (
    <>
      <a className="rh-back" href="#/">
        ← Investing
      </a>
      <section className="rh-hero" aria-label={data.name}>
        <p className="rh-eyebrow">{data.name}</p>
        <h1 className="rh-big">{price(shown)}</h1>
        <p className={`rh-change ${tone(change)}`}>
          {signedPrice(change)} (
          {pct(change !== null && first ? change / first : null)}){" "}
          <span>
            {hover !== null
              ? labels[hover]
              : `Past ${span === "ALL" ? "all time" : span}`}
          </span>
        </p>
        <LineChart
          values={values}
          labels={labels}
          onHover={setHover}
          ariaLabel={`${data.name} price`}
        />
        <SpanPills value={span} onChange={setSpan} toneClass={tone(change)} />
        <div
          className="rh-spans flat"
          role="tablist"
          aria-label="Candle interval"
        >
          {["15m", "1h", "4h", "1d"].map((value) => (
            <button
              key={value}
              role="tab"
              aria-selected={interval === value}
              className={interval === value ? "is-active" : ""}
              onClick={() => {
                setInterval(value);
                setHover(null);
              }}
            >
              {value}
            </button>
          ))}
        </div>
        <p className="rh-note">
          Closed {interval} candle prices · available stored history only.
          Missing buckets stay missing; the line connects available closes.
        </p>
      </section>
      <section aria-label="Timeframe readiness">
        <h2 className="rh-h2">Data for the current check</h2>
        <dl className="rh-stats">
          {Object.entries(data.frames).map(([frame, status]) => (
            <div key={frame}>
              <dt>{frame}</dt>
              <dd>
                {status.ready
                  ? "ready"
                  : (FRAME_PROBLEMS[status.problem ?? ""] ?? "not ready")}
                {" · "}
                {status.available}/{status.required} bars · last close{" "}
                {when(status.last_close)}
              </dd>
            </div>
          ))}
        </dl>
      </section>
      {data.positions.length > 0 && (
        <section>
          <h2 className="rh-h2">Your paper position</h2>
          <PositionRows positions={data.positions} />
        </section>
      )}
      <section aria-labelledby="why-h">
        <h2 id="why-h" className="rh-h2">
          Why the desk would or wouldn't buy
        </h2>
        {!data.eligible && (
          <p className="rh-empty">
            Waiting for data: {data.ineligible_reason}.
          </p>
        )}
        <div className="rh-why">
          {data.signals.map((s) => {
            const all = s.conditions.every((c) => c.met);
            return (
              <div
                key={s.strategy_id}
                className={`rh-card ${all ? "is-met" : ""}`}
              >
                <h3>
                  {STRATEGY_NAMES[s.strategy_id]}
                  <span className={`rh-chip ${all ? "up" : "flat"}`}>
                    {all ? "conditions met" : "not now"}
                  </span>
                </h3>
                <ul>
                  {s.conditions.map((c) => (
                    <li key={c.label} className={c.met ? "is-met" : ""}>
                      <span aria-hidden>{c.met ? "✓" : "✗"}</span> {c.label}
                    </li>
                  ))}
                </ul>
              </div>
            );
          })}
        </div>
        <p className="rh-note">
          Every buy also needs Robinhood's own price for the exact ticket, a
          round trip under{" "}
          {snap
            ? pct(Number(snap.limits.max_roundtrip_cost)).replace("+", "")
            : "the limit"}
          , and room in the book. Closed 15-minute candles trigger checks
          throughout the day, using the daily regime, 4-hour trend and hourly
          setup. More data does not require more trades.
        </p>
      </section>
      <section aria-labelledby="stats-h">
        <h2 id="stats-h" className="rh-h2">
          Stats
        </h2>
        <dl className="rh-stats">
          {stats.map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
        {data.quote_crossed && (
          <p className="rh-note">
            The reported bid exceeds the ask across partner exchanges. These
            prices exclude order size and fees; paper fills use exact-size
            estimates.
          </p>
        )}
      </section>
      <section aria-labelledby="dec-h">
        <h2 id="dec-h" className="rh-h2">
          Decisions on this coin
        </h2>
        {data.decisions.length ? (
          <ul className="rh-rows">
            {data.decisions.map((d, i) => (
              <li key={`${d.at}-${i}`}>
                <div>
                  <span>
                    <strong>
                      {d.action === "ENTER"
                        ? "Buy"
                        : d.action === "EXIT"
                          ? "Sell"
                          : "Skipped"}{" "}
                      · {STRATEGY_NAMES[d.strategy_id]}
                    </strong>
                    <small>
                      {when(d.at)} · {d.reasons.map(reason).join(", ")}
                    </small>
                  </span>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="rh-empty">No decisions on this coin yet.</p>
        )}
      </section>
    </>
  );
}

function StrategyCard({ s, btc }: { s: Strategy; btc: Strategy | undefined }) {
  const sim = s.backtest;
  const perYear = num(sim?.pnl_per_dollar_year ?? null);
  const btcYear = num(btc?.backtest?.pnl_per_dollar_year ?? null);
  return (
    <div
      className={`rh-card rh-strategy ${s.kind === "BASELINE" ? "is-baseline" : ""}`}
    >
      <h3>
        {STRATEGY_NAMES[s.id] ?? s.id}
        {s.kind === "STRATEGY" && perYear !== null && btcYear !== null && (
          <span className={`rh-chip ${perYear > btcYear ? "up" : "down"}`}>
            {perYear > btcYear ? "beat holding BTC" : "behind holding BTC"} in
            history
          </span>
        )}
      </h3>
      <p>{s.about}</p>
      <p className="rh-note">
        {s.kind === "LEARNED"
          ? "Chosen by the learning lab · judged on months it never saw"
          : `Rules v${s.spec_version}${s.spec_version === 2 ? " · experimental · no historical validation yet" : " · daily baseline"}`}
      </p>
      {(s.legacy_open > 0 || s.legacy_closed > 0) && (
        <p className="rh-note">
          Daily v1 kept separately: {s.legacy_open} open · {s.legacy_closed}{" "}
          closed · {usd(s.legacy_realized)} realized.
        </p>
      )}
      <dl className="rh-stats rh-stats-tight">
        <div>
          <dt>Paper now</dt>
          <dd>
            {s.open} open · {s.closed} closed · {s.wins} wins
          </dd>
        </div>
        <div>
          <dt>Paper P&amp;L</dt>
          <dd className={tone(num(s.realized))}>
            {signedUsd(num(s.realized))}
          </dd>
        </div>
        <div>
          <dt>3-year history (sim)</dt>
          <dd>
            {sim
              ? `${sim.trades} trades · ${sim.win_rate ? Math.round(Number(sim.win_rate) * 100) : 0}% wins`
              : "unvalidated for these rules"}
          </dd>
        </div>
        <div>
          <dt>Per dollar-year held</dt>
          <dd className={tone(perYear)}>{pct(perYear, 0)}</dd>
        </div>
      </dl>
    </div>
  );
}

function LearningPage() {
  const { data, error } = usePoll<Learning>("/api/rh/learning", 60_000);
  if (error) return <p className="rh-empty">{error}</p>;
  if (!data) return <p className="rh-empty">Loading…</p>;
  const latest = data.latest;
  const promoted = latest?.promoted ?? null;
  const benched = data.forward.benched;
  const status = !latest
    ? "First run starting"
    : promoted && !benched
      ? data.mode === "paper"
        ? "Paper trading a learned strategy"
        : "A procedure passed (recording only)"
      : benched
        ? "Benched: paper results contradicted history"
        : "Learning · nothing has earned a place yet";
  const years = latest ? (latest.days / 365).toFixed(1) : "—";
  return (
    <>
      <h1 className="rh-title">Learning</h1>
      <p className="rh-note">
        <span className={`rh-chip ${promoted && !benched ? "up" : "flat"}`}>
          {status}
        </span>
      </p>
      <p className="rh-note">
        Every month the desk tests {latest?.candidates ?? "~250"} rule variants
        and a statistical model on all the history it has. It picks using only
        the past {data.rules.train_days} days, then is graded on the next{" "}
        {data.rules.test_days} days it had not seen. A choice trades in PAPER
        only if, on those unseen months, it made money after Robinhood costs and
        beat holding BTC over the same days, with statistical confidence.
      </p>
      {latest ? (
        <>
          <section className="rh-card rh-power rh-learn-facts">
            <div>
              <span>Unseen months</span>
              <strong>{latest.folds}</strong>
            </div>
            <div>
              <span>Coins · years</span>
              <strong>
                {latest.symbols} · {years}
              </strong>
            </div>
            <div>
              <span>Costs measured</span>
              <strong>
                {latest.costs.measured}/
                {latest.costs.measured + latest.costs.assumed}
              </strong>
            </div>
            <div>
              <span>Next run</span>
              <strong>{data.next_run ?? "—"}</strong>
            </div>
          </section>
          <h2 className="rh-h2">On months it never saw</h2>
          <div className="rh-grid">
            {Object.entries(latest.procedures).map(([key, p]) => {
              const last = p.curve.length ? p.curve[p.curve.length - 1] : null;
              return (
                <div
                  key={key}
                  className={`rh-card ${p.passed ? "is-met" : ""}`}
                >
                  <h3>
                    {p.label}
                    <span className={`rh-chip ${p.passed ? "up" : "flat"}`}>
                      {p.passed
                        ? promoted === key
                          ? "promoted"
                          : "passed"
                        : "not yet"}
                    </span>
                  </h3>
                  <LineChart
                    values={p.curve.map((c) => c[1])}
                    labels={p.curve.map((c) => c[0])}
                    height={110}
                    ariaLabel={`${p.label}: cumulative paper dollars on unseen months`}
                  />
                  <dl className="rh-stats rh-stats-tight">
                    <div>
                      <dt>Unseen trades</dt>
                      <dd>
                        {p.trades} ·{" "}
                        {p.trades ? Math.round((p.wins / p.trades) * 100) : 0}%
                        wins
                      </dd>
                    </div>
                    <div>
                      <dt>Avg per trade, after costs</dt>
                      <dd className={tone(p.mean_net)}>{pct(p.mean_net)}</dd>
                    </div>
                    <div>
                      <dt>vs holding BTC</dt>
                      <dd className={tone(p.mean_excess_vs_btc)}>
                        {pct(p.mean_excess_vs_btc)}
                      </dd>
                    </div>
                    <div>
                      <dt>$10 tickets · BTC same days</dt>
                      <dd>
                        {last
                          ? `${signedUsd(last[1])} · ${signedUsd(last[2])}`
                          : "—"}
                      </dd>
                    </div>
                  </dl>
                  {!p.passed && p.failures.length > 0 && (
                    <ul className="rh-list">
                      {p.failures.map((f) => (
                        <li key={f}>{f}</li>
                      ))}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>
          <h2 className="rh-h2">Today&apos;s choice</h2>
          <ul className="rh-rows">
            {data.selection.map((item) => (
              <li key={`${item.procedure}-${item.id}`}>
                <div>
                  <span>
                    <strong>
                      {latest.procedures[item.procedure]?.label ??
                        item.procedure}
                      {promoted === item.procedure ? " · trading" : ""}
                    </strong>
                    <small>{item.describe}</small>
                  </span>
                </div>
              </li>
            ))}
          </ul>
          <h2 className="rh-h2">Best variants over the past year</h2>
          <ul className="rh-rows">
            {latest.leaderboard.map((row, i) => (
              <li key={row.id}>
                <div>
                  <span>
                    <strong>
                      #{i + 1} · {row.trades} trades · avg {pct(row.mean_net)}
                    </strong>
                    <small>{row.describe}</small>
                  </span>
                </div>
              </li>
            ))}
          </ul>
          <p className="rh-note">
            A good past year is not enough: picking last year&apos;s winner is
            exactly what the unseen-month grades above test.
          </p>
        </>
      ) : (
        <p className="rh-empty">
          The first run starts a few minutes after the desk starts: it downloads
          up to 4 years of daily history first. Or run{" "}
          <code>uv run desk rh learn</code>.
        </p>
      )}
      <h2 className="rh-h2">The learned strategy&apos;s own paper record</h2>
      <dl className="rh-stats">
        <div>
          <dt>Open · closed</dt>
          <dd>
            {data.forward.open} · {data.forward.closed_all}
          </dd>
        </div>
        <div>
          <dt>Realized</dt>
          <dd className={tone(num(data.forward.realized))}>
            {signedUsd(num(data.forward.realized))}
          </dd>
        </div>
        <div>
          <dt>Current choice: trades · avg</dt>
          <dd>
            {data.forward.closed_current} · {pct(data.forward.mean_net)}
          </dd>
        </div>
        <div>
          <dt>Benched if paper contradicts history</dt>
          <dd>{data.forward.benched ? "benched" : "no"}</dd>
        </div>
      </dl>
      {data.runs.length > 0 && (
        <>
          <h2 className="rh-h2">Runs</h2>
          <ul className="rh-rows">
            {data.runs.map((r) => (
              <li key={r.id}>
                <div>
                  <span>
                    <strong>
                      {when(r.created_at)} · data to {r.data_end}
                    </strong>
                    <small>
                      {r.promoted
                        ? `promoted: ${latest?.procedures[r.promoted]?.label ?? r.promoted}`
                        : "nothing passed"}
                    </small>
                  </span>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

function StrategiesPage({ snap }: { snap: Snapshot }) {
  const btc = snap.strategies.find((s) => s.id === "B1_HOLD_BTC");
  return (
    <>
      <h1 className="rh-title">Strategies</h1>
      <p className="rh-note">
        The three v2 strategies combine daily, 4-hour, hourly and 15-minute
        evidence. They are experimental and need forward validation. Holding BTC
        and random entries remain daily v1 baselines. The old daily v1
        simulation does not validate these new rules; current v2 results and old
        v1 positions are counted separately.
      </p>
      {snap.backtest && (
        <p className="rh-note">
          Daily v1 simulation only {snap.backtest.sessions} ·{" "}
          {snap.backtest.universe.with_history} coins · round trip{" "}
          {pct(
            Number(snap.backtest.cost_model.assumed_roundtrip_fraction),
          ).replace("+", "")}{" "}
          assumed · BTC held{" "}
          {pct(num(snap.backtest.btc_buy_and_hold_return), 0)}
        </p>
      )}
      <div className="rh-grid">
        {snap.strategies.map((s) => (
          <StrategyCard key={s.id} s={s} btc={btc} />
        ))}
      </div>
    </>
  );
}

function SystemPage({ snap }: { snap: Snapshot }) {
  const last = snap.session.last;
  const rows: [string, string][] = [
    [
      "Robinhood prices",
      snap.robinhood_prices
        ? `live · last ${when(snap.last_quote_at)}`
        : "not flowing",
    ],
    ["Coins", snap.universe_source],
    ["Last daily bar", when(snap.last_daily_bar, false)],
    [
      "Last candle check",
      last
        ? `${last.day} · BTC ${last.regime === "RISK_ON" ? "uptrend" : last.regime === "RISK_OFF" ? "downtrend" : "unknown"} · ${last.eligible} coins eligible`
        : "none yet",
    ],
    [
      "Next 15-minute candle close + publication grace",
      when(snap.session.next_at),
    ],
    [
      "Analysis",
      `24/7 · checks every ${snap.session.check_interval_seconds}s as closed data arrives`,
    ],
    [
      "Data ready",
      `${snap.session.ready_assets}/${snap.session.total_assets} assets across 1d / 4h / 1h / 15m`,
    ],
    [
      "Ticket",
      `${pct(Number(snap.limits.ticket_fraction), 0).replace("+", "")} of the $100 account (+ realized)`,
    ],
    [
      "Round-trip limit",
      pct(Number(snap.limits.max_roundtrip_cost)).replace("+", ""),
    ],
    ["Max open", String(snap.limits.max_open_positions)],
    ["Learning cash", usd(snap.account.learning_cash)],
  ];
  return (
    <>
      <h1 className="rh-title">System</h1>
      {!snap.robinhood_prices && (
        <div className="rh-card rh-howto">
          <h3>Connect Robinhood prices (read-only)</h3>
          <ol>
            <li>
              In a terminal: <code>uv run desk rh keygen</code>. It prints a
              public key.
            </li>
            <li>
              On robinhood.com → Account → Crypto → API trading, add a key with
              that public key. Enable read actions only.
            </li>
            <li>
              Put <code>ROBINHOOD_API_KEY=rh-api-…</code> in{" "}
              <code>~/.config/desk/secrets.env</code>.
            </li>
            <li>
              Check with <code>uv run desk rh doctor</code>, then restart{" "}
              <code>uv run desk rh paper</code>.
            </li>
          </ol>
        </div>
      )}
      <dl className="rh-stats">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      {snap.backtest && (
        <section>
          <h2 className="rh-h2">Simulation caveats</h2>
          <ul className="rh-list">
            {snap.backtest.caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </section>
      )}
      {Object.keys(snap.session.pending).length > 0 && (
        <section>
          <h2 className="rh-h2">Waiting for complete current data</h2>
          <p className="rh-note">
            These coins are skipped until their data is complete; nothing is
            broken. Coinbase leaves out any period in which nothing traded, and
            these rules need every period, so a quieter coin waits until its
            empty periods are older than the window the rules look at.
          </p>
          <ul className="rh-list">
            {Object.entries(snap.session.pending).map(([symbol, why]) => (
              <li key={symbol}>
                <strong>{symbol.replace("-USD", "")}</strong>: {why}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

export default function RhApp() {
  const route = useRoute();
  const { data: snap, error } = usePoll<Snapshot>("/api/rh/snapshot", 15_000);
  useEffect(() => {
    const page =
      route[0] === "coin"
        ? (route[1] ?? "").replace("-USD", "")
        : route[0] || "Investing";
    document.title = `${page.charAt(0).toUpperCase()}${page.slice(1)} · KESTREL Crypto · PAPER`;
  }, [route]);
  let main;
  if (!snap) {
    main = <p className="rh-empty">{error || "Loading…"}</p>;
  } else if (route[0] === "coin" && route[1]) {
    main = <CoinPage symbol={route[1]} snap={snap} />;
  } else if (route[0] === "strategies") {
    main = <StrategiesPage snap={snap} />;
  } else if (route[0] === "learning") {
    main = <LearningPage />;
  } else if (route[0] === "activity") {
    main = (
      <>
        <h1 className="rh-title">Activity</h1>
        <ActivityRows items={snap.activity} />
      </>
    );
  } else if (route[0] === "system") {
    main = <SystemPage snap={snap} />;
  } else {
    main = <Home snap={snap} />;
  }
  return (
    <>
      <Header snap={snap} />
      {error && snap && (
        <p className="rh-banner">
          Connection problem: {error}. Showing the last data.
        </p>
      )}
      <div className="rh-layout">
        <main id="main">{main}</main>
        {snap && <Watchlist coins={snap.coins} />}
      </div>
      <footer className="rh-footer">
        Paper trading on Robinhood's prices. No order can be placed from this
        desk. Not affiliated with Robinhood.
      </footer>
    </>
  );
}
