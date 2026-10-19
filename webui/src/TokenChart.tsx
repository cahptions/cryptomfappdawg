import { useEffect, useMemo, useRef, useState } from "react";
import type { NumberValue } from "./types";

export interface Candle {
  at: string;
  open: NumberValue;
  high: NumberValue;
  low: NumberValue;
  close: NumberValue;
  volume_usd: NumberValue;
  ema20?: NumberValue;
  ema50?: NumberValue;
  rsi14?: NumberValue;
}
interface ChartData {
  mint: string;
  pool_address?: string;
  timeframe?: string;
  source?: string;
  observed_at?: string;
  stale?: boolean;
  points?: Candle[];
  indicators?: unknown;
  status?: string;
  note?: string;
}
function n(value: NumberValue): number | null {
  if (value === null || value === undefined || value === "") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}
function price(value: NumberValue): string {
  const parsed = n(value);
  if (parsed === null) return "—";
  return new Intl.NumberFormat(undefined, {
    minimumFractionDigits: 0,
    maximumSignificantDigits: 6,
  }).format(parsed);
}
function stamp(value?: string, date = false): string {
  const at = value ? new Date(value) : null;
  return !at || !Number.isFinite(at.valueOf())
    ? "Not recorded"
    : new Intl.DateTimeFormat(undefined, {
        ...(date ? { month: "short", day: "numeric" } : {}),
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(at);
}

/** Recorded bars only. No client candle synthesis, extrapolation or indicator warmup fill. */
export default function TokenChart({
  mint,
  poolAddress,
}: {
  mint: string;
  poolAddress?: string;
}) {
  const [data, setData] = useState<ChartData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [windowSize, setWindowSize] = useState(144);
  const [hover, setHover] = useState<number | null>(null);
  const inFlight = useRef<AbortController | null>(null);
  useEffect(() => {
    let active = true;
    setData(null);
    setError("");
    setLoading(true);
    setHover(null);
    const update = async () => {
      if (!active || inFlight.current || document.hidden) return;
      const controller = new AbortController();
      inFlight.current = controller;
      const timeout = window.setTimeout(() => controller.abort(), 10_000);
      try {
        const query = new URLSearchParams({ limit: "200" });
        if (poolAddress) query.set("pool_address", poolAddress);
        const response = await fetch(
          `/api/charts/${encodeURIComponent(mint)}?${query}`,
          {
            signal: controller.signal,
            credentials: "same-origin",
            cache: "no-store",
          },
        );
        if (!response.ok)
          throw new Error(`Chart unavailable (${response.status})`);
        const result: ChartData = await response.json();
        if (active) {
          setData(result);
          setError("");
        }
      } catch (cause) {
        if (active)
          setError(
            controller.signal.aborted
              ? "Chart request timed out. Retrying while visible."
              : cause instanceof Error
                ? cause.message
                : "Chart could not be loaded.",
          );
      } finally {
        window.clearTimeout(timeout);
        if (inFlight.current === controller) inFlight.current = null;
        if (active) setLoading(false);
      }
    };
    void update();
    const interval = window.setInterval(() => void update(), 30_000);
    const visible = () => {
      if (!document.hidden) void update();
    };
    document.addEventListener("visibilitychange", visible);
    return () => {
      active = false;
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", visible);
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [mint, poolAddress]);
  const points = useMemo(
    () =>
      (data?.points ?? [])
        .filter(
          (p) =>
            Number.isFinite(new Date(p.at).valueOf()) &&
            [p.open, p.high, p.low, p.close].every(
              (value) => n(value) !== null && Number(value) > 0,
            ),
        )
        .slice(-Math.min(windowSize, 200)),
    [data, windowSize],
  );
  const latest = points[points.length - 1];
  const focused =
    hover !== null ? points[Math.min(hover, points.length - 1)] : latest;
  const high = Math.max(
    ...points.flatMap((p) => [
      Number(p.high),
      n(p.ema20) ?? Number(p.high),
      n(p.ema50) ?? Number(p.high),
    ]),
  );
  const low = Math.min(
    ...points.flatMap((p) => [
      Number(p.low),
      n(p.ema20) ?? Number(p.low),
      n(p.ema50) ?? Number(p.low),
    ]),
  );
  const pad = Math.max((high - low) * 0.08, high * 0.0001, Number.MIN_VALUE);
  const span = high - low + pad * 2;
  const x = (index: number) =>
    66 + ((index + 0.5) / Math.max(points.length, 1)) * 626;
  const y = (value: number) => 196 - ((value - low + pad) / span) * 168;
  const candleWidth = Math.max(
    1,
    Math.min(8, (626 / Math.max(points.length, 1)) * 0.65),
  );
  const maxVolume = Math.max(1, ...points.map((p) => n(p.volume_usd) ?? 0));
  const line = (key: "ema20" | "ema50" | "rsi14") => {
    let drawing = false;
    return points
      .map((point, index) => {
        const value = n(point[key]);
        if (value === null) {
          drawing = false;
          return "";
        }
        const command = drawing ? "L" : "M";
        drawing = true;
        return `${command} ${x(index)} ${key === "rsi14" ? 368 - (value / 100) * 66 : y(value)}`;
      })
      .join(" ");
  };
  const warm = (key: "ema20" | "ema50" | "rsi14") => n(latest?.[key]) === null;
  return (
    <section
      className="token-chart"
      aria-label="Token price chart and indicators"
    >
      <div className="token-chart-top">
        <div>
          <span className="eyebrow">RECORDED MARKET TAPE</span>
          <h3>
            Price &amp; momentum <span>5m · USD</span>
          </h3>
        </div>
        <div className="chart-periods" aria-label="Visible chart period">
          {[
            { size: 48, label: "4h" },
            { size: 144, label: "12h" },
            { size: 200, label: "All" },
          ].map((period) => (
            <button
              key={period.size}
              aria-pressed={windowSize === period.size}
              onClick={() => {
                setWindowSize(period.size);
                setHover(null);
              }}
            >
              {period.label}
            </button>
          ))}
        </div>
      </div>
      <div className="token-chart-legend">
        <span className="ema20-label">
          ━ EMA 20 {warm("ema20") ? "· warming up" : price(focused?.ema20)}
        </span>
        <span className="ema50-label">
          ━ EMA 50 {warm("ema50") ? "· warming up" : price(focused?.ema50)}
        </span>
        <span
          className={`chart-history-status ${data?.stale ? "warning" : "muted"}`}
        >
          {data?.stale ? "STALE HISTORY" : `${points.length} recorded bars`}
        </span>
      </div>
      {loading && !data ? (
        <div className="token-chart-empty">
          <span className="loading-ring" />
          <h4>Loading recorded candles…</h4>
        </div>
      ) : !points.length ? (
        <div className="token-chart-empty">
          <svg viewBox="0 0 44 44" aria-hidden="true">
            <path d="M11 7v26m0-18v10m11-20v26m0-19v10m11-6v23m0-16v8" />
            <path d="M8 15h6v10H8Zm11-3h6v10h-6Zm11 11h6v8h-6Z" />
          </svg>
          <h4>No recorded candle history yet.</h4>
          <p>
            {error ||
              data?.note ||
              "History appears after the collector records this pool. Indicators need actual observations before they can warm up."}
          </p>
        </div>
      ) : (
        <>
          {error && (
            <p className="chart-error" role="status">
              {error} Showing the last chart response.
            </p>
          )}
          <div className="candle-readout num" aria-live="off">
            <span>{stamp(focused?.at)}</span>
            <span>
              O <b>{price(focused?.open)}</b>
            </span>
            <span>
              H <b>{price(focused?.high)}</b>
            </span>
            <span>
              L <b>{price(focused?.low)}</b>
            </span>
            <span>
              C <b>{price(focused?.close)}</b>
            </span>
          </div>
          <div className="token-chart-scroll">
            <svg
              className="candlestick-chart"
              viewBox="0 0 760 408"
              role="img"
              aria-label={`${points.length} recorded 5-minute USD candles with EMA20, EMA50, volume and RSI14`}
              onPointerMove={(event) => {
                const b = event.currentTarget.getBoundingClientRect();
                const position = ((event.clientX - b.left) / b.width) * 760;
                setHover(
                  Math.max(
                    0,
                    Math.min(
                      points.length - 1,
                      Math.floor(((position - 66) / 626) * points.length),
                    ),
                  ),
                );
              }}
              onPointerLeave={() => setHover(null)}
            >
              {[0, 1, 2, 3].map((i) => (
                <g key={i}>
                  <line
                    x1="66"
                    y1={28 + i * 56}
                    x2="692"
                    y2={28 + i * 56}
                    className="chart-grid"
                  />
                  <text
                    x="57"
                    y={32 + i * 56}
                    textAnchor="end"
                    className="chart-label"
                  >
                    {price(high + pad - (i / 3) * span)}
                  </text>
                </g>
              ))}
              {points.map((p, index) => {
                const up = Number(p.close) >= Number(p.open);
                const top = Math.min(y(Number(p.open)), y(Number(p.close)));
                return (
                  <g key={p.at} className={up ? "candle-up" : "candle-down"}>
                    <title>
                      {stamp(p.at, true)} · {up ? "up" : "down"} · open $
                      {price(p.open)}, high ${price(p.high)}, low $
                      {price(p.low)}, close ${price(p.close)}
                    </title>
                    <line
                      x1={x(index)}
                      y1={y(Number(p.high))}
                      x2={x(index)}
                      y2={y(Number(p.low))}
                    />
                    <rect
                      x={x(index) - candleWidth / 2}
                      y={top}
                      width={candleWidth}
                      height={Math.max(
                        1,
                        Math.abs(y(Number(p.open)) - y(Number(p.close))),
                      )}
                    />
                  </g>
                );
              })}
              <path d={line("ema20")} className="ema20-line" />
              <path d={line("ema50")} className="ema50-line" />
              <line x1="66" x2="692" y1="223" y2="223" className="chart-grid" />
              <text x="66" y="218" className="chart-label">
                VOLUME · USD
              </text>
              <text x="692" y="218" textAnchor="end" className="chart-label">
                {price(focused?.volume_usd)}
              </text>
              {points.map((p, i) =>
                n(p.volume_usd) === null ? null : (
                  <rect
                    key={p.at}
                    x={x(i) - candleWidth / 2}
                    y={278 - (Number(p.volume_usd) / maxVolume) * 44}
                    width={candleWidth}
                    height={(Number(p.volume_usd) / maxVolume) * 44}
                    className={
                      Number(p.close) >= Number(p.open)
                        ? "volume-up"
                        : "volume-down"
                    }
                  />
                ),
              )}
              <line x1="66" x2="692" y1="284" y2="284" className="chart-grid" />
              <text x="66" y="298" className="chart-label">
                RSI 14 · {warm("rsi14") ? "warming up" : price(focused?.rsi14)}
              </text>
              {[30, 70].map((value) => (
                <g key={value}>
                  <line
                    x1="66"
                    x2="692"
                    y1={368 - (value / 100) * 66}
                    y2={368 - (value / 100) * 66}
                    className="rsi-threshold"
                  />
                  <text
                    x="57"
                    y={372 - (value / 100) * 66}
                    textAnchor="end"
                    className="chart-label"
                  >
                    {value}
                  </text>
                </g>
              ))}
              <path d={line("rsi14")} className="rsi-line" />
              <text x="66" y="394" className="chart-label">
                {stamp(points[0].at, true)}
              </text>
              <text x="692" y="394" textAnchor="end" className="chart-label">
                {stamp(latest.at, true)}
              </text>
              {hover !== null && points[hover] && (
                <g>
                  <line
                    x1={x(hover)}
                    x2={x(hover)}
                    y1="18"
                    y2="370"
                    className="chart-crosshair"
                  />
                  <circle
                    cx={x(hover)}
                    cy={y(Number(points[hover].close))}
                    r="3"
                    className="chart-point"
                  />
                </g>
              )}
            </svg>
          </div>
        </>
      )}
      <div className="token-chart-footer">
        <span>
          {data?.source || "Recorded market observations"} · updated{" "}
          {stamp(data?.observed_at, true)}
        </span>
        <span>
          EMA: trend · RSI: momentum · indicators do not authorize a trade
        </span>
      </div>
    </section>
  );
}
