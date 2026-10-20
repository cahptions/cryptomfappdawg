import { useEffect, useId, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { control, loadDetail, useDashboard } from "./api";
import TokenChart from "./TokenChart";
import type {
  ActivityGroup,
  AuditEvent,
  Candidate,
  Detail,
  DiscoveryTest,
  DiscoveryTestSegment,
  Health,
  Matrix,
  NumberValue,
  PaperActivity,
  PaperClosedTrade,
  PaperFunnel,
  PaperOpenPosition,
  PaperStatus,
  PaperTab,
  Position,
  Snapshot,
  Tone,
  View,
} from "./types";

const NAV: { id: View; label: string; icon: string; key?: string }[] = [
  { id: "overview", label: "Overview", icon: "grid", key: "g o" },
  { id: "scanner", label: "Scanner", icon: "scan", key: "g s" },
  { id: "paper", label: "Paper trading", icon: "receipt", key: "g t" },
  { id: "positions", label: "Positions", icon: "layers", key: "g p" },
  { id: "strategies", label: "Strategies", icon: "sliders" },
  { id: "research", label: "Research", icon: "chart", key: "g r" },
  { id: "audit", label: "Audit", icon: "list" },
  { id: "system", label: "System", icon: "pulse" },
  { id: "live", label: "Live gates", icon: "shield" },
];
const ICON_PATHS: Record<string, ReactNode> = {
  grid: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </>
  ),
  scan: (
    <>
      <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5M3 12h18" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  layers: (
    <>
      <path d="m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5" />
    </>
  ),
  sliders: (
    <>
      <path d="M4 7h5m4 0h7M4 17h9m4 0h3M9 4v6m4 4v6" />
      <circle cx="11" cy="7" r="2" />
      <circle cx="15" cy="17" r="2" />
    </>
  ),
  chart: (
    <>
      <path d="M4 3v17h17M7 14l4-5 4 3 6-8" />
    </>
  ),
  list: (
    <>
      <path d="M9 5h12M9 12h12M9 19h12M3 5h1M3 12h1M3 19h1" />
    </>
  ),
  pulse: (
    <>
      <path d="M2 12h5l3-8 4 16 3-8h5" />
    </>
  ),
  shield: (
    <>
      <path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  arrow: (
    <>
      <path d="M5 12h14m-5-5 5 5-5 5" />
    </>
  ),
  arrowup: (
    <>
      <path d="M6 18 18 6M6 6h12v12" />
    </>
  ),
  chevron: <path d="m9 5 7 7-7 7" />,
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 5 5" />
    </>
  ),
  play: <path d="m8 4 12 8-12 8V4Z" />,
  stop: <rect x="5" y="5" width="14" height="14" rx="2" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  download: (
    <>
      <path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  check: <path d="m5 12 4 4L19 6" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v6m0-10v1" />
    </>
  ),
  refresh: (
    <>
      <path d="M20 8a8 8 0 1 0 0 8M20 3v6h-6" />
    </>
  ),
  terminal: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="2" />
      <path d="m7 9 3 3-3 3m6 0h4" />
    </>
  ),
  wallet: (
    <>
      <path d="M20 7H5a2 2 0 0 1 0-4h13v4M4 7v13h17V7m0 5h-6v4h6" />
    </>
  ),
  receipt: (
    <>
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9 8h6M9 12h6M9 16h3" />
    </>
  ),
};
function Icon({ name, className = "" }: { name: string; className?: string }) {
  return (
    <svg
      className={`icon ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICON_PATHS[name] ?? ICON_PATHS.info}
    </svg>
  );
}
function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 40 40" aria-hidden="true">
      <defs>
        <linearGradient id="wing" x1="0" x2="1" y1="0" y2="1">
          <stop stopColor="#3cd2ff" />
          <stop offset="1" stopColor="#9945ff" />
        </linearGradient>
      </defs>
      <path d="M4 9 20 15 36 4 25 23 20 35 15 23Z" fill="url(#wing)" />
      <path d="m5 10 15 12 15-16-15 11Z" fill="#07090c" opacity=".65" />
    </svg>
  );
}
function number(value: NumberValue): number | null {
  if (value === null || value === undefined || value === "") return null;
  const result = Number(value);
  return Number.isFinite(result) ? result : null;
}
function fmt(value: NumberValue, digits = 3): string {
  const n = number(value);
  return n === null
    ? "—"
    : n.toLocaleString(undefined, {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
}
function signed(value: NumberValue, digits = 3) {
  const n = number(value);
  return n === null ? "—" : `${n >= 0 ? "+" : "−"}${fmt(Math.abs(n), digits)}`;
}
function pct(value: NumberValue) {
  const n = number(value);
  return n === null
    ? "—"
    : `${n >= 0 ? "+" : "−"}${fmt(Math.abs(n) * 100, 2)}%`;
}
function dollars(value: NumberValue) {
  const n = number(value);
  return n === null
    ? "—"
    : `$${new Intl.NumberFormat(undefined, { notation: n >= 10_000 ? "compact" : "standard", maximumFractionDigits: n < 1 ? 6 : 1 }).format(n)}`;
}
function short(value = "") {
  return value.length > 15
    ? `${value.slice(0, 6)}…${value.slice(-5)}`
    : value || "Unknown";
}
function markFreshness(at?: string) {
  if (!at) return "time unavailable";
  const age = Math.floor((Date.now() - new Date(at).valueOf()) / 1000);
  if (!Number.isFinite(age) || age < 0) return "unverified timestamp";
  const elapsed =
    age < 60
      ? `${age}s`
      : age < 3600
        ? `${Math.floor(age / 60)}m`
        : `${Math.floor(age / 3600)}h`;
  return `${age <= 30 ? "fresh" : "stale"} · ${elapsed} old`;
}
function words(value = "") {
  return value.replaceAll("_", " ").replaceAll("-", " ");
}
function time(value?: string, full = false) {
  const date = value ? new Date(value) : null;
  return !date || Number.isNaN(date.valueOf())
    ? "Not recorded"
    : new Intl.DateTimeFormat(
        undefined,
        full
          ? {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            }
          : {
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
              hour12: false,
            },
      ).format(date);
}
function tone(value?: string) {
  const s = (value ?? "").toUpperCase();
  return /PASS|HEALTHY|^OK$|ACTIVE|EXECUTABLE|READY/.test(s) &&
    !/NOT|UNREADY|INSUFFICIENT|INACTIVE/.test(s)
    ? "positive"
    : /FAIL|REJECT|CRITICAL|HALT|ERROR|UNAVAILABLE/.test(s)
      ? "negative"
      : /DEFER|STALE|DEGRADED|WARN|PENDING/.test(s)
        ? "warning"
        : "neutral";
}
function count(values?: Record<string, number>) {
  return Object.values(values ?? {}).reduce((a, b) => a + b, 0);
}
function describe(value: unknown): string {
  if (value === null || value === undefined) return "No additional detail";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}
function Badge({ children, status }: { children: ReactNode; status?: string }) {
  return (
    <span className={`badge ${tone(status)}`}>
      <span className="status-point" />
      {children}
    </span>
  );
}
// The 24-hour discovery test, collected in pieces: every cleanly finished run adds its active hours.
function DiscoveryTestPanel({
  test,
  compact = false,
}: {
  test?: DiscoveryTest | null;
  compact?: boolean;
}) {
  if (!test) return null;
  const share = Math.min(1, test.counted_hours / test.required_hours);
  const running = test.segments.find((s) => s.state === "RUNNING");
  const label = (s: DiscoveryTestSegment) =>
    s.state === "COUNTED"
      ? "counted"
      : s.state === "RUNNING"
        ? "running now"
        : s.state === "INTERRUPTED"
          ? "lost (did not finish)"
          : "not counted";
  return (
    <Panel
      title="24-hour discovery test"
      eyebrow="COLLECTED IN PIECES"
      className="span-12"
      action={
        <Badge status={test.passed ? "PASS" : "PENDING"}>
          {test.passed ? "Passed" : "In progress"}
        </Badge>
      }
    >
      <div className="pad discovery-test">
        <div className="discovery-test-head">
          <strong className="num">
            {test.counted_hours.toFixed(1)} / {test.required_hours} h
          </strong>
          <span className="secondary">
            longest piece {test.longest_segment_hours.toFixed(1)} h (one must
            reach {test.segment_hours} h)
            {running && running.hours !== null
              ? ` · current piece running ${running.hours.toFixed(1)} h`
              : ""}
          </span>
        </div>
        <svg
          viewBox="0 0 300 6"
          preserveAspectRatio="none"
          className="discovery-test-bar"
          role="img"
          aria-label={`${Math.round(share * 100)}% of the 24-hour test`}
        >
          <rect width="300" height="6" rx="3" className="meter-bg" />
          <rect
            width={Math.max(0, 300 * share)}
            height="6"
            rx="3"
            className="discovery-test-fill"
          />
        </svg>
        <p className="secondary text-small">
          {test.rule}. Pieces run 4 hours each and restart by themselves;
          switching the laptop off keeps what is already counted.
        </p>
        {!compact && test.segments.length > 0 && (
          <ul className="discovery-test-list">
            {test.segments.slice(0, 12).map((s) => (
              <li key={s.run_id}>
                <span className="num">{time(s.started_at, true)}</span>
                <span className="num">
                  {s.hours === null ? "—" : `${s.hours.toFixed(2)} h`}
                </span>
                <Badge
                  status={
                    s.state === "COUNTED"
                      ? "PASS"
                      : s.state === "RUNNING"
                        ? "ACTIVE"
                        : "WARN"
                  }
                >
                  {label(s)}
                </Badge>
                <span className="secondary text-small">
                  {s.problems.join(", ")}
                  {s.sleep_gaps ? " (sleep excluded)" : ""}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </Panel>
  );
}

function Panel({
  title,
  eyebrow,
  children,
  action,
  className = "",
}: {
  title: string;
  eyebrow?: string;
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          {eyebrow && <div className="eyebrow">{eyebrow}</div>}
          <h2>{title}</h2>
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
function Empty({
  icon = "scan",
  title,
  detail,
  compact = false,
}: {
  icon?: string;
  title: string;
  detail: string;
  compact?: boolean;
}) {
  return (
    <div className={`empty-state ${compact ? "compact" : ""}`}>
      <span className="empty-icon">
        <Icon name={icon} />
      </span>
      <h3>{title}</h3>
      <p>{detail}</p>
    </div>
  );
}
function Metric({
  label,
  value,
  suffix,
  detail,
  title,
  positive = false,
}: {
  label: string;
  value: ReactNode;
  suffix?: string;
  detail: string;
  title: string;
  positive?: boolean;
}) {
  return (
    <div className="metric" title={title}>
      <div className="metric-label">
        {label}
        <Icon name="info" />
      </div>
      <div className={`metric-value ${positive ? "positive-text" : ""}`}>
        {value}
        {suffix && <span>{suffix}</span>}
      </div>
      <div className="metric-detail">{detail}</div>
    </div>
  );
}

function EquityChart({
  points = [],
  label,
  emptyTitle = "Your performance curve starts with evidence.",
  emptyDetail = "Recorded account observations will appear here.",
}: {
  points?: Snapshot["equity"];
  label?: string;
  emptyTitle?: string;
  emptyDetail?: string;
}) {
  const id = useId().replaceAll(":", "");
  const [hover, setHover] = useState<number | null>(null);
  const valid = points.filter(
    (p) =>
      number(p.value_sol) !== null && Number.isFinite(new Date(p.at).valueOf()),
  );
  const equity = valid.filter((p) => p.kind === "equity");
  const data = (
    equity.length >= 2 ? equity : valid.filter((p) => p.kind !== "equity")
  ).slice(-600);
  const cash = data[0]?.kind !== "equity";
  const values = data.map((p) => Number(p.value_sol));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const padding = Math.max((max - min) * 0.2, Math.abs(max) * 0.001, 0.001);
  const low = min - padding;
  const high = max + padding;
  const x = (i: number) => 65 + (i / Math.max(data.length - 1, 1)) * 720;
  const y = (v: number) => 202 - ((v - low) / (high - low)) * 170;
  const line = values
    .map((v, i) => `${i ? "L" : "M"} ${x(i)} ${y(v)}`)
    .join(" ");
  const focused = hover === null ? null : data[hover];
  if (data.length < 2)
    return (
      <div className="chart-empty">
        <svg viewBox="0 0 840 220" aria-hidden="true">
          <path
            d="M35 35H810M35 85H810M35 135H810M35 185H810M165 20V200M330 20V200M495 20V200M660 20V200"
            className="chart-grid"
          />
        </svg>
        <div>
          <span className="chart-empty-mark">
            <Icon name="chart" />
          </span>
          <h3>{emptyTitle}</h3>
          <p>
            {emptyDetail}
            <br />
            No simulated returns or projected gains.
          </p>
        </div>
      </div>
    );
  return (
    <div className="chart-wrap">
      <div className="chart-legend">
        <span className="series-swatch" />
        {label ?? (cash ? "Realized cash · SOL" : "Account equity · SOL")}
        <span className="muted">{data.length} recorded observations</span>
      </div>
      <svg
        className="equity-chart"
        viewBox="0 0 840 240"
        role="img"
        aria-label={`${label ?? (cash ? "Realized cash in SOL" : "Account equity in SOL")} across ${data.length} recorded observations`}
        onPointerMove={(e) => {
          const b = e.currentTarget.getBoundingClientRect();
          setHover(
            Math.max(
              0,
              Math.min(
                data.length - 1,
                Math.round(
                  ((((e.clientX - b.left) / b.width) * 840 - 65) / 720) *
                    (data.length - 1),
                ),
              ),
            ),
          );
        }}
        onPointerLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="0" y2="1">
            <stop stopColor="#009fc8" stopOpacity=".2" />
            <stop offset="1" stopColor="#009fc8" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0, 1, 2, 3].map((i) => {
          const yy = 32 + (i * 170) / 3;
          return (
            <g key={i}>
              <line x1="65" y1={yy} x2="785" y2={yy} className="chart-grid" />
              <text x="52" y={yy + 4} textAnchor="end" className="chart-label">
                {fmt(high - (i * (high - low)) / 3, 3)}
              </text>
            </g>
          );
        })}
        <path d={`${line} L 785 202 L 65 202 Z`} fill={`url(#${id})`} />
        <path d={line} className="chart-series" />
        <text x="65" y="229" className="chart-label">
          {time(data[0].at)}
        </text>
        <text x="785" y="229" textAnchor="end" className="chart-label">
          {time(data[data.length - 1].at)}
        </text>
        {focused && hover !== null && (
          <g>
            <line
              x1={x(hover)}
              x2={x(hover)}
              y1="22"
              y2="202"
              className="chart-crosshair"
            />
            <circle
              cx={x(hover)}
              cy={y(Number(focused.value_sol))}
              r="4"
              className="chart-point"
            />
            <g
              transform={`translate(${Math.max(65, Math.min(x(hover) - 78, 625))}, 4)`}
            >
              <rect
                width="160"
                height="43"
                rx="6"
                className="chart-tooltip-bg"
              />
              <text x="10" y="17" className="chart-tooltip-value">
                {fmt(focused.value_sol, 6)} SOL
              </text>
              <text x="10" y="33" className="chart-label">
                {time(focused.at)}
              </text>
            </g>
          </g>
        )}
      </svg>
    </div>
  );
}

// Safety verdict, measured round-trip cost of the exact ticket and on-chain holder concentration.
function SafetyCell({ candidate: c }: { candidate: Candidate }) {
  if (!c.security_outcome) {
    return (
      <span className="secondary text-small">
        {c.state === "DEFER" && c.last_rule_id === "AWAITING_POOL"
          ? "Not checked: no pool yet"
          : "Not checked yet"}
      </span>
    );
  }
  const cost = number(c.roundtrip_cost);
  const top10 = number(c.top10_pct);
  const facts = [
    cost === null ? null : `round trip ${(cost * 100).toFixed(1)}%`,
    top10 === null ? null : `top-10 hold ${top10.toFixed(0)}%`,
  ].filter(Boolean);
  const label =
    c.security_outcome === "PASS"
      ? "Safe"
      : c.security_outcome === "REJECT"
        ? "Unsafe"
        : "Unproven";
  return (
    <span className="safety-cell">
      <Badge status={c.security_outcome}>{label}</Badge>
      {facts.length > 0 && (
        <small className="rule-label">{facts.join(" · ")}</small>
      )}
      {c.security_outcome !== "PASS" && c.security_reasons?.length ? (
        <small
          className="rule-label"
          title={c.security_reasons.map(words).join(", ")}
        >
          {c.security_reasons
            .slice(0, 2)
            .map((r) => words(r).toLowerCase())
            .join(", ")}
        </small>
      ) : null}
    </span>
  );
}

function Scanner({
  candidates = [],
  search,
  setSearch,
  onDetail,
  compact = false,
}: {
  candidates?: Candidate[];
  search: string;
  setSearch: (value: string) => void;
  onDetail: (detail: Detail) => void;
  compact?: boolean;
}) {
  const [filter, setFilter] = useState("ALL");
  const [sort, setSort] = useState<{ key: keyof Candidate; direction: number }>(
    { key: "observed_at", direction: -1 },
  );
  const searchRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const focus = () => searchRef.current?.focus();
    window.addEventListener("kestrel-search", focus);
    return () => window.removeEventListener("kestrel-search", focus);
  }, []);
  const states = Array.from(
    new Set(candidates.map((c) => c.state).filter(Boolean)),
  );
  const visible = useMemo(
    () =>
      candidates
        .filter(
          (c) =>
            (filter === "ALL" || c.state === filter) &&
            `${c.mint} ${c.symbol ?? ""} ${c.name ?? ""} ${c.last_rule_id ?? ""}`
              .toLowerCase()
              .includes(search.toLowerCase()),
        )
        .sort((a, b) => {
          const av = a[sort.key];
          const bv = b[sort.key];
          if (
            sort.key === "liquidity_usd" ||
            sort.key === "volume_h1" ||
            sort.key === "price_usd" ||
            sort.key === "stage"
          )
            return (
              ((number(av as NumberValue) ?? -Infinity) -
                (number(bv as NumberValue) ?? -Infinity)) *
              sort.direction
            );
          return (
            String(av ?? "").localeCompare(String(bv ?? "")) * sort.direction
          );
        }),
    [candidates, filter, search, sort],
  );
  const order = (key: keyof Candidate) =>
    setSort((s) => ({ key, direction: s.key === key ? -s.direction : -1 }));
  const th = (label: string, key: keyof Candidate, right = false) => (
    <th
      className={right ? "numeric" : ""}
      aria-sort={
        sort.key === key
          ? sort.direction === 1
            ? "ascending"
            : "descending"
          : "none"
      }
    >
      <button className="table-sort" onClick={() => order(key)}>
        {label}
        <span className={sort.key === key ? "sort-active" : ""}>
          {sort.key === key && sort.direction === 1 ? "↑" : "↓"}
        </span>
      </button>
    </th>
  );
  return (
    <>
      <div className="table-toolbar">
        <label className="search-field">
          <Icon name="search" />
          <input
            ref={searchRef}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search token or mint"
            aria-label="Search token or mint"
          />
          <kbd>/</kbd>
        </label>
        <label className="select-label">
          <span className="sr-only">Filter candidate state</span>
          <select
            aria-label="Filter candidate state"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="ALL">All states</option>
            {states.map((s) => (
              <option key={s} value={s}>
                {words(s)}
              </option>
            ))}
          </select>
        </label>
        <span className="table-count">
          {visible.length} {visible.length === 1 ? "candidate" : "candidates"}
        </span>
      </div>
      {visible.length ? (
        <div className="table-scroll">
          <table className="scanner-table">
            <thead>
              <tr>
                {th("Asset", "symbol")}
                {th("State", "state")}
                {!compact && th("Safety & cost", "security_outcome")}
                {!compact && th("Discovery", "reason")}
                {th("Liquidity", "liquidity_usd", true)}
                {th("1h volume", "volume_h1", true)}
                {!compact && th("Price", "price_usd", true)}
                {th("Observed", "observed_at", true)}
                <th>
                  <span className="sr-only">Details</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {visible.slice(0, compact ? 6 : 200).map((c) => (
                <tr key={c.id}>
                  <td>
                    <button
                      className="icon-button"
                      title={`Chart ${c.symbol || short(c.mint)}`}
                      aria-label={`Chart ${c.symbol || short(c.mint)}`}
                      onClick={() =>
                        onDetail({
                          kind: "candidate",
                          title: c.symbol || short(c.mint),
                          endpoint: `/api/candidates/${encodeURIComponent(c.id)}`,
                        })
                      }
                    >
                      <Icon name="chart" />
                    </button>
                    <button
                      className="token-button"
                      onClick={() =>
                        onDetail({
                          kind: "candidate",
                          title: c.symbol || short(c.mint),
                          endpoint: `/api/candidates/${encodeURIComponent(c.id)}`,
                        })
                      }
                    >
                      <Identicon mint={c.mint} />
                      <span>
                        <strong>{c.symbol || short(c.mint)}</strong>
                        <small title={c.mint}>{short(c.mint)}</small>
                      </span>
                    </button>
                  </td>
                  <td>
                    <Badge status={c.state}>
                      {words(c.state || "PENDING")}
                    </Badge>
                    <small className="rule-label" title={c.last_rule_id}>
                      {c.last_rule_id
                        ? words(c.last_rule_id).toLowerCase()
                        : `Stage ${c.stage ?? "—"}`}
                    </small>
                  </td>
                  {!compact && (
                    <td>
                      <SafetyCell candidate={c} />
                    </td>
                  )}
                  {!compact && (
                    <td className="secondary text-small">
                      {words(c.reason || "Not recorded")}
                    </td>
                  )}
                  <td className="numeric num">{dollars(c.liquidity_usd)}</td>
                  <td className="numeric num">{dollars(c.volume_h1)}</td>
                  {!compact && (
                    <td className="numeric num">{dollars(c.price_usd)}</td>
                  )}
                  <td
                    className="numeric secondary num text-small"
                    title={time(c.observed_at, true)}
                  >
                    {time(c.observed_at)}
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      title={`Open ${c.symbol || short(c.mint)} dossier`}
                      aria-label={`Open ${c.symbol || short(c.mint)} dossier`}
                      onClick={() =>
                        onDetail({
                          kind: "candidate",
                          title: c.symbol || short(c.mint),
                          endpoint: `/api/candidates/${encodeURIComponent(c.id)}`,
                        })
                      }
                    >
                      <Icon name="chevron" />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          compact={compact}
          title={
            search || filter !== "ALL"
              ? "No candidates match these filters"
              : "Listening for the next opportunity"
          }
          detail={
            search || filter !== "ALL"
              ? "Try another mint, symbol, or state."
              : "Start discovery from the terminal. Candidates appear only after a source observation is recorded."
          }
        />
      )}
      {visible.length > (compact ? 6 : 200) && (
        <div className="panel-foot">
          Showing {compact ? 6 : 200} of {visible.length} loaded candidates.
          Filter to narrow the view.
        </div>
      )}
    </>
  );
}
function Identicon({ mint = "" }: { mint?: string }) {
  let seed = 0;
  for (const c of mint) seed = (seed * 31 + c.charCodeAt(0)) >>> 0;
  return (
    <span className={`identicon palette-${seed % 4}`}>
      <svg viewBox="0 0 25 25" aria-hidden="true">
        {Array.from({ length: 15 }, (_, i) => {
          const col = i % 3;
          const row = Math.floor(i / 3);
          return (seed >>> (i % 24)) & 1 ? (
            <g key={i}>
              <rect x={col * 5} y={row * 5} width="5" height="5" />
              {col !== 2 && (
                <rect x={(4 - col) * 5} y={row * 5} width="5" height="5" />
              )}
            </g>
          ) : null;
        })}
      </svg>
    </span>
  );
}

function Positions({
  positions = [],
  onDetail,
  compact = false,
}: {
  positions?: Position[];
  onDetail: (detail: Detail) => void;
  compact?: boolean;
}) {
  if (!positions.length)
    return (
      <Empty
        icon="layers"
        compact={compact}
        title="Flat is a position."
        detail="No open PAPER positions. The desk waits for a qualifying signal and an executable route."
      />
    );
  return (
    <div className="positions-grid">
      {positions.slice(0, compact ? 2 : 50).map((p) => (
        <button
          className="position-card"
          key={p.id}
          onClick={() =>
            onDetail({
              kind: "position",
              title: p.symbol || short(p.mint || p.id),
              endpoint: `/api/positions/${encodeURIComponent(p.id)}`,
            })
          }
        >
          <div className="position-head">
            <Identicon mint={p.mint || p.id} />
            <span>
              <strong>{p.symbol || short(p.mint || p.id)}</strong>
              <small>{words(p.strategy || "Strategy not recorded")}</small>
            </span>
            <Badge status={p.state}>{words(p.state || "OPEN")}</Badge>
          </div>
          <div className="position-numbers">
            <div>
              <span>Entry size</span>
              <strong className="num">
                {fmt(p.ticket_sol)} <small>SOL</small>
              </strong>
            </div>
            <div>
              <span>
                {p.mark_kind === "EXECUTABLE"
                  ? "Executable exit"
                  : "Latest mark"}
              </span>
              <strong className="num">
                {fmt(p.mark_sol)} <small>SOL</small>
              </strong>
            </div>
            <div>
              <span>Unrealized · marked</span>
              <strong
                className={`num ${(number(p.mark_sol) ?? 0) >= (number(p.ticket_sol) ?? 0) ? "positive-text" : "negative-text"}`}
              >
                {number(p.mark_sol) === null || number(p.ticket_sol) === null
                  ? "—"
                  : signed(Number(p.mark_sol) - Number(p.ticket_sol))}{" "}
                <small>SOL</small>
              </strong>
            </div>
          </div>
          <div className="position-foot">
            <span className={`mark-kind ${tone(p.mark_kind)}`}>
              {words(p.mark_kind || "MARK UNAVAILABLE")}
            </span>
            <span>Mark {markFreshness(p.mark_at || p.last_mark_at)}</span>
            <Icon name="arrow" />
          </div>
        </button>
      ))}
    </div>
  );
}

function Confidence({
  matrices = [],
  onDetail,
  compact = false,
}: {
  matrices?: Matrix[];
  onDetail: (detail: Detail) => void;
  compact?: boolean;
}) {
  if (!matrices.length)
    return (
      <Empty
        icon="shield"
        compact
        title="Confidence is earned."
        detail="No evaluated strategy evidence yet. Estimates, intervals, and sample sizes appear after the first closed PAPER trades."
      />
    );
  return (
    <div className="confidence-list">
      {matrices.slice(0, compact ? 1 : 100).map((matrix) => (
        <div
          className="strategy-block"
          key={`${matrix.strategy_id}:${matrix.strategy_version}`}
        >
          <button
            className="strategy-name"
            onClick={() =>
              onDetail({
                kind: "strategy",
                title: words(matrix.strategy_id),
                endpoint: `/api/strategies/${encodeURIComponent(matrix.strategy_id)}`,
              })
            }
          >
            <span>
              <strong>{words(matrix.strategy_id)}</strong>
              <small>
                {matrix.strategy_version
                  ? `Version ${matrix.strategy_version}`
                  : "Version not recorded"}{" "}
                · n = {matrix.trades ?? 0} closed trades
              </small>
            </span>
            <Icon name="arrowup" />
          </button>
          <div className="readiness-line">
            <Badge status={matrix.readiness}>
              {words(matrix.readiness || "INSUFFICIENT")}
            </Badge>
            <span className="muted text-small">Evidence readiness</span>
          </div>
          <div className="confidence-grid">
            {(matrix.cells ?? []).slice(0, compact ? 6 : 60).map((cell) => (
              <button
                className="confidence-cell"
                key={cell.dimension}
                title={cell.detail || "Open evidence, estimate, and interval"}
                onClick={() =>
                  onDetail({
                    kind: "evidence",
                    title: words(cell.dimension),
                    value: {
                      strategy: matrix.strategy_id,
                      version: matrix.strategy_version,
                      sample_trades: matrix.trades,
                      ...cell,
                    },
                  })
                }
              >
                <div>
                  <span>{words(cell.dimension)}</span>
                  <span className={`cell-status ${tone(cell.status)}`}>
                    {cell.status || "INSUFFICIENT"}
                  </span>
                </div>
                <strong className="num">{fmt(cell.estimate, 4)}</strong>
                <small className="num">
                  CI [{fmt(cell.lower, 4)}, {fmt(cell.upper, 4)}]
                </small>
                <small className="num">
                  n = {typeof cell.evidence === "number" ? cell.evidence : "—"}{" "}
                  · recorded evidence
                </small>
              </button>
            ))}
          </div>
          {!matrix.cells?.length && (
            <p className="panel-note">
              No confidence dimensions have been evaluated.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}
function Providers({
  health = [],
  compact = false,
}: {
  health?: Health[];
  compact?: boolean;
}) {
  if (!health.length)
    return (
      <Empty
        icon="pulse"
        compact
        title="No provider observations"
        detail="Provider status appears after the first recorded request. Unknown health never implies healthy."
      />
    );
  return (
    <div className="health-list">
      {health.slice(0, compact ? 5 : 50).map((h) => (
        <div className="health-row" key={h.provider}>
          <span className={`health-dot ${tone(h.state)}`} />
          <div className="health-name">
            <strong>{words(h.provider)}</strong>
            <small title={h.last_error || undefined}>
              {h.last_error || `Last success ${time(h.last_success_at)}`}
            </small>
          </div>
          <span className={`health-state ${tone(h.state)}`}>
            {words(h.state || "UNKNOWN")}
          </span>
        </div>
      ))}
    </div>
  );
}
function Audit({
  events = [],
  compact = false,
  onDetail,
}: {
  events?: AuditEvent[];
  compact?: boolean;
  onDetail: (detail: Detail) => void;
}) {
  if (!events.length)
    return (
      <Empty
        icon="list"
        compact
        title="A clean audit trail starts here"
        detail="Recorded decisions, orders, exits, and operator actions appear here as they happen."
      />
    );
  return (
    <ol className="audit-list">
      {events.slice(0, compact ? 5 : 150).map((event, i) => (
        <li key={event.id || `${event.at}:${i}`}>
          <span className="audit-time num" title={time(event.at, true)}>
            {time(event.at)}
          </span>
          <span className={`audit-marker ${tone(event.kind)}`} />
          <button
            onClick={() =>
              onDetail({
                kind: "evidence",
                title: words(event.kind || "Recorded event"),
                value: event,
              })
            }
          >
            <strong>{words(event.kind || "Recorded event")}</strong>
            <span>{describe(event.detail ?? event.payload)}</span>
          </button>
          <Icon name="chevron" />
        </li>
      ))}
    </ol>
  );
}

function Funnel({
  funnel,
  regime,
  onScanner,
}: {
  funnel?: Snapshot["funnel"];
  regime?: Snapshot["regime"];
  onScanner: () => void;
}) {
  const states = funnel?.by_state ?? {};
  const total = count(states);
  return (
    <div className="funnel-content">
      <div className="decision-state">
        <span className="decision-icon">
          <Icon name="shield" />
        </span>
        <div>
          <span className="eyebrow">DEFAULT DECISION</span>
          <h3>No trade without evidence.</h3>
          <p>Security, costs, and strategy conditions must all qualify.</p>
        </div>
      </div>
      <div className="funnel-stats">
        {[
          { label: "Observed", value: total },
          { label: "Eligible", value: states.PASS ?? 0 },
          { label: "Deferred", value: states.DEFER ?? states.PENDING ?? 0 },
          { label: "Rejected", value: states.REJECT ?? 0 },
        ].map((s) => (
          <div key={s.label}>
            <span>{s.label}</span>
            <strong className="num">{s.value.toLocaleString()}</strong>
          </div>
        ))}
      </div>
      <div className="regime-row">
        <span>Market regime</span>
        <Badge status={regime?.status}>
          {words(regime?.label || regime?.status || "UNKNOWN")}
        </Badge>
      </div>
      <p className="panel-note">
        {regime?.reason ||
          "Awaiting sufficient market context. Unknown regime remains explicit."}
      </p>
      <button className="text-button" onClick={onScanner}>
        Inspect discovery funnel <Icon name="arrow" />
      </button>
    </div>
  );
}
function SystemFacts({ data }: { data: Snapshot }) {
  const system = data.system;
  const total = number(system?.memory_total_mb ?? system?.memory?.total_mb);
  const available = number(
    system?.memory_available_mb ?? system?.memory?.available_mb,
  );
  const usedFraction =
    total && available !== null
      ? Math.max(0, Math.min(1, (total - available) / total))
      : null;
  return (
    <div className="system-facts">
      <div className="system-metric">
        <div>
          <span>Memory in use</span>
          <strong className="num">
            {total && available !== null
              ? `${fmt((total - available) / 1024, 1)} / ${fmt(total / 1024, 1)} GB`
              : "—"}
          </strong>
        </div>
        <svg
          viewBox="0 0 300 5"
          preserveAspectRatio="none"
          role="img"
          aria-label={
            usedFraction === null
              ? "Memory unavailable"
              : `${Math.round(usedFraction * 100)} percent memory in use`
          }
        >
          <rect width="300" height="5" rx="2" className="meter-bg" />
          {usedFraction !== null && (
            <rect
              width={300 * usedFraction}
              height="5"
              rx="2"
              className="meter-fill"
            />
          )}
        </svg>
      </div>
      <div className="system-fact-grid">
        <div>
          <span>Swap occupied</span>
          <strong className="num">
            {fmt(
              number(system?.swap_used_mb ?? system?.memory?.swap_used_mb) ===
                null
                ? null
                : Number(system?.swap_used_mb ?? system?.memory?.swap_used_mb) /
                    1024,
              1,
            )}{" "}
            <small>GB</small>
          </strong>
        </div>
        <div>
          <span>Disk available</span>
          <strong className="num">
            {fmt(system?.disk_free_gb, 1)} <small>GB</small>
          </strong>
        </div>
        <div>
          <span>Database size</span>
          <strong className="num">
            {fmt(
              number(
                system?.db_bytes ??
                  (system?.databases
                    ? Object.values(system.databases).reduce(
                        (sum, db) =>
                          sum +
                          (number(db.bytes) ?? 0) +
                          (number(db.wal_bytes) ?? 0),
                        0,
                      )
                    : null),
              ) === null
                ? null
                : Number(
                    system?.db_bytes ??
                      Object.values(system?.databases ?? {}).reduce(
                        (sum, db) =>
                          sum +
                          (number(db.bytes) ?? 0) +
                          (number(db.wal_bytes) ?? 0),
                        0,
                      ),
                  ) /
                    1024 /
                    1024,
              1,
            )}{" "}
            <small>MB</small>
          </strong>
        </div>
        <div>
          <span>Active workers</span>
          <strong className="num">
            {(data.workers ?? []).filter((w) => w.active).length}
          </strong>
        </div>
      </div>
      <p className="panel-note">
        Metrics describe the machine serving this terminal.
      </p>
    </div>
  );
}

function DetailDrawer({
  detail,
  onClose,
}: {
  detail: Detail | null;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState<unknown>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!detail) {
      dialog.current?.close();
      return;
    }
    dialog.current?.showModal();
    setValue(detail.value ?? null);
    setError("");
    if (!detail.endpoint) {
      setLoading(false);
      return;
    }
    let active = true;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10_000);
    setLoading(true);
    void loadDetail(detail.endpoint, controller.signal)
      .then((result) => {
        if (active) setValue(result);
      })
      .catch((cause) => {
        if (active)
          setError(
            controller.signal.aborted
              ? "Detail request timed out. Close and reopen to retry."
              : cause instanceof Error
                ? cause.message
                : "Could not load details.",
          );
      })
      .finally(() => {
        window.clearTimeout(timeout);
        if (active) setLoading(false);
      });
    return () => {
      active = false;
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [detail]);
  return (
    <dialog
      ref={dialog}
      className="detail-drawer"
      onCancel={onClose}
      onClick={(e) => {
        if (e.target === e.currentTarget) {
          const bounds = e.currentTarget.getBoundingClientRect();
          if (e.clientX < bounds.left) onClose();
        }
      }}
      aria-labelledby="drawer-title"
    >
      <div className="drawer-heading">
        <div>
          <div className="eyebrow">
            {detail ? `${detail.kind} dossier` : "Evidence"}
          </div>
          <h2 id="drawer-title">{detail?.title}</h2>
        </div>
        <button
          className="icon-button"
          onClick={onClose}
          aria-label="Close dossier"
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="drawer-content">
        {loading ? (
          <div className="loading-state">
            <span className="loading-ring" />
            Loading recorded evidence…
          </div>
        ) : error ? (
          <div className="notice negative">
            <Icon name="info" />
            {error}
          </div>
        ) : (
          <>
            {detail &&
            (detail.kind === "candidate" || detail.kind === "position") &&
            value &&
            typeof value === "object" &&
            "mint" in value &&
            typeof value.mint === "string" ? (
              <TokenChart
                mint={value.mint}
                poolAddress={
                  "pool_address" in value &&
                  typeof value.pool_address === "string"
                    ? value.pool_address
                    : undefined
                }
              />
            ) : null}
            <Evidence value={value} />
          </>
        )}
      </div>
      <div className="drawer-footer">
        <Icon name="shield" />
        <span>Recorded evidence only · no ownership or return guarantees</span>
      </div>
    </dialog>
  );
}
function Evidence({ value, depth = 0 }: { value: unknown; depth?: number }) {
  if (value === null || value === undefined)
    return <span className="muted">Not recorded</span>;
  if (typeof value === "boolean")
    return (
      <Badge status={value ? "PASS" : "FAIL"}>{value ? "Yes" : "No"}</Badge>
    );
  if (typeof value !== "object")
    return <span className="evidence-value">{String(value)}</span>;
  if (depth > 5)
    return <pre className="raw-evidence">{JSON.stringify(value, null, 2)}</pre>;
  if (Array.isArray(value))
    return value.length ? (
      <div className="evidence-array">
        {value.slice(0, 150).map((item, i) => (
          <div className="evidence-array-item" key={i}>
            <span className="eyebrow">
              Record {String(i + 1).padStart(2, "0")}
            </span>
            <Evidence value={item} depth={depth + 1} />
          </div>
        ))}
      </div>
    ) : (
      <span className="muted">No records</span>
    );
  return (
    <dl className="evidence-list">
      {Object.entries(value).map(([key, child]) => (
        <div
          className={
            child && typeof child === "object" ? "nested-evidence" : ""
          }
          key={key}
        >
          <dt>{words(key)}</dt>
          <dd>
            <Evidence value={child} depth={depth + 1} />
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** The M9 verdict: the only "consider real money" signal, written from recorded data by the readiness report. */
function ReadinessVerdict({
  readiness,
  compact = false,
}: {
  readiness?: Snapshot["readiness"];
  compact?: boolean;
}) {
  if (!readiness?.verdict)
    return (
      <div className="readiness-verdict">
        <div className="readiness-head">
          <Badge status="NEUTRAL">No readiness report yet</Badge>
        </div>
        <p>
          While PAPER runs, a readiness report is written every 6 hours. Run
          desk report readiness in a terminal to write one now.
        </p>
      </div>
    );
  const status =
    readiness.verdict === "ELIGIBLE FOR MANUAL LIVE REVIEW"
      ? "PASS"
      : readiness.verdict === "FAILED VALIDATION"
        ? "FAIL"
        : readiness.verdict === "CONTINUE PAPER TESTING"
          ? "PENDING"
          : "NEUTRAL";
  return (
    <div className="readiness-verdict">
      <div className="readiness-head">
        <Badge status={status}>{readiness.verdict}</Badge>
        <span className="muted text-small">
          {readiness.closed_trades ?? 0} closed trades ·{" "}
          {fmt(readiness.calendar_days, 1)} days · as of{" "}
          {time(readiness.as_of ?? undefined, true)}
        </span>
      </div>
      {!compact && <p>{readiness.next_step}</p>}
      {!compact && readiness.path && (
        <p className="panel-note">
          Full report with every figure's source: <code>{readiness.path}</code>
        </p>
      )}
    </div>
  );
}

const COUNCIL_NAMES: Record<string, string> = {
  C1_BRC: "C1 benchmark-residual continuation",
  C2_ECDR: "C2 dislocation reversal (experimental)",
};
const STANCE_TONE: Record<string, string> = {
  SUPPORT: "PASS",
  OPPOSE: "FAIL",
  CAUTION: "PENDING",
  ABSTAIN: "NEUTRAL",
};

/** Two research advisors record what they would have said at each decision; nothing here changes a trade. */
function CouncilPanel({ council }: { council?: Snapshot["council"] }) {
  if (!council?.enabled)
    return (
      <p className="panel-note">
        Council recording is off (research.council = "off"). Turning it on
        changes no trade.
      </p>
    );
  return (
    <div className="council">
      <p className="panel-note">
        Research only, zero influence. Each advisor states SUPPORT, CAUTION,
        OPPOSE or ABSTAIN for a prospective long at every recorded decision.
        ABSTAIN means it could not know, not a forecast. C1 needs 76 hours of
        unbroken 15-minute history, so expect mostly ABSTAIN at first. SOL/USD
        benchmark minutes collected:{" "}
        {council.benchmark_minutes.toLocaleString()}.
      </p>
      <div className="council-counts">
        {Object.keys(COUNCIL_NAMES).map((advisor) => (
          <span className="subtle-chip" key={advisor}>
            {COUNCIL_NAMES[advisor]} · 24 h:{" "}
            {Object.entries(council.last_24h[advisor] ?? {})
              .map(([stance, n]) => `${words(stance)} ${n}`)
              .join(" · ") || "no records"}
          </span>
        ))}
      </div>
      {council.latest.length ? (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Decision</th>
                <th>Advisor</th>
                <th>Stance</th>
                <th>Primary</th>
                <th>Token</th>
                <th>Why</th>
              </tr>
            </thead>
            <tbody>
              {council.latest.map((row) => (
                <tr key={`${row.advisor}-${row.decision_at}-${row.mint}`}>
                  <td>{time(row.decision_at, true)}</td>
                  <td>{row.advisor}</td>
                  <td>
                    <Badge status={STANCE_TONE[row.stance]}>
                      {words(row.stance)}
                    </Badge>
                  </td>
                  <td>{words(row.primary_action)}</td>
                  <td>
                    <strong>{short(row.mint)}</strong>
                  </td>
                  <td>
                    {row.reasons.map(words).join(", ")}
                    {row.signal.residual_z && (
                      <small> · Z {fmt(row.signal.residual_z, 2)}</small>
                    )}
                    {row.signal.shock_z && (
                      <small> · shock {fmt(row.signal.shock_z, 2)}</small>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          icon="shield"
          compact
          title="No council records yet"
          detail="Records appear within 15 minutes of the first recorded decision while desk paper runs."
        />
      )}
    </div>
  );
}

// ── Paper trading tab ────────────────────────────────────────────────────────────────────────────────
// One bounded read of /api/paper every few seconds while the tab is open and visible. Every sentence is
// written by the server from recorded ledger events; the browser only formats and filters it.
type FeedFilter = "all" | ActivityGroup;
const FEED_FILTERS: { id: FeedFilter; label: string }[] = [
  { id: "all", label: "Everything" },
  { id: "trade", label: "Trades" },
  { id: "signal", label: "Signals" },
  { id: "skip", label: "Skipped" },
  { id: "look", label: "Passed on" },
];
const FUNNEL_STEPS: { key: keyof PaperFunnel; label: string; hint: string }[] =
  [
    {
      key: "decisions",
      label: "Tokens assessed",
      hint: "passed the safety checks; every strategy looked",
    },
    {
      key: "no_trade",
      label: "No strategy wanted it",
      hint: "not trading is the default",
    },
    { key: "signals", label: "A strategy wanted to buy", hint: "" },
    {
      key: "blocked",
      label: "Blocked by a safety gate",
      hint: "STOP, guardian, limits, pauses",
    },
    {
      key: "skipped_size",
      label: "Skipped by sizing rules",
      hint: "minimum ticket, fee floor, cash",
    },
    {
      key: "attempts",
      label: "Quoted for real",
      hint: "two quotes 2.5 s apart; the worse is used",
    },
    {
      key: "skipped_cost",
      label: "Too expensive to trade",
      hint: "round-trip cost above the strategy limit",
    },
    {
      key: "failed",
      label: "Entry failed",
      hint: "no route or simulated transaction failures",
    },
    { key: "opened", label: "Bought", hint: "" },
    { key: "closed", label: "Sold", hint: "" },
  ];
const PAPER_REFRESH_MS = 4_000;

function duration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds))
    return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600)
    return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
  if (s < 86_400)
    return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}m`;
  return `${Math.floor(s / 86_400)}d ${Math.floor((s % 86_400) / 3600)}h`;
}
function share(value: NumberValue, digits = 1): string {
  const n = number(value);
  return n === null ? "—" : `${fmt(n * 100, digits)}%`;
}
function lamportsAsSol(value: NumberValue, digits = 6): string {
  const n = number(value);
  return n === null ? "—" : `${fmt(n / 1e9, digits)} SOL`;
}
function feedTone(value?: Tone): string {
  return value === "critical" ? "negative" : (value ?? "neutral");
}
function signedClass(value: NumberValue): string {
  const n = number(value);
  return n === null || n === 0 ? "" : n > 0 ? "positive-text" : "negative-text";
}

function usePaperTab() {
  const [data, setData] = useState<PaperTab | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let stopped = false;
    let request: AbortController | null = null;
    const load = () => {
      if (stopped || request || document.hidden) return;
      const controller = new AbortController();
      request = controller;
      const timeout = window.setTimeout(() => controller.abort(), 10_000);
      loadDetail("/api/paper", controller.signal)
        .then((value) => {
          if (!stopped) {
            setData(value as PaperTab);
            setError("");
          }
        })
        .catch((cause) => {
          if (!stopped)
            setError(
              controller.signal.aborted
                ? "The paper book took more than 10 seconds to answer. Retrying."
                : cause instanceof Error
                  ? cause.message
                  : "Could not read the paper book.",
            );
        })
        .finally(() => {
          window.clearTimeout(timeout);
          if (request === controller) request = null;
        });
    };
    load();
    const timer = window.setInterval(load, PAPER_REFRESH_MS);
    document.addEventListener("visibilitychange", load);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", load);
      request?.abort();
    };
  }, []);
  return { data, error };
}

function PaperStatusBanner({
  status,
  hours,
}: {
  status: PaperStatus;
  hours?: number;
}) {
  const t = feedTone(status.tone);
  return (
    <div className={`paper-status ${t}`} role="status">
      <span className="paper-status-icon">
        <Icon
          name={
            t === "positive"
              ? "check"
              : t === "neutral"
                ? "clock"
                : t === "info"
                  ? "pulse"
                  : "shield"
          }
        />
      </span>
      <div className="paper-status-text">
        <strong>{status.headline}</strong>
        {status.detail && <span>{status.detail}</span>}
      </div>
      <dl className="paper-status-facts">
        <div>
          <dt>Exit guardian</dt>
          <dd className="num">
            {status.guardian_age_seconds === null ||
            status.guardian_age_seconds === undefined
              ? "not seen"
              : `seen ${duration(status.guardian_age_seconds)} ago`}
          </dd>
        </div>
        <div>
          <dt>Engine</dt>
          <dd className="num">
            {status.engine_age_seconds === null ||
            status.engine_age_seconds === undefined
              ? "not seen"
              : `seen ${duration(status.engine_age_seconds)} ago`}
          </dd>
        </div>
        {hours !== undefined && (
          <div>
            <dt>Book running</dt>
            <dd className="num">{duration(hours * 3600)}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

function PaperFeed({
  items,
  filtered,
  onDetail,
}: {
  items: PaperActivity[];
  filtered: boolean;
  onDetail: (detail: Detail) => void;
}) {
  if (!items.length)
    return (
      <Empty
        icon="receipt"
        compact
        title={
          filtered ? "Nothing of this kind yet" : "Waiting for the first token"
        }
        detail={
          filtered
            ? "Try another filter. The feed keeps the latest 100 trade and skip events plus the latest 50 decisions."
            : "Tokens reach the paper book only after discovery and the safety checks pass. Each look, skip, buy and sell will appear here."
        }
      />
    );
  const open = (item: PaperActivity) => {
    const name = item.symbol || short(item.mint || "");
    if (item.position_id)
      onDetail({
        kind: "position",
        title: name,
        endpoint: `/api/positions/${encodeURIComponent(item.position_id)}`,
      });
    else if (item.candidate_id)
      onDetail({
        kind: "candidate",
        title: name,
        endpoint: `/api/candidates/${encodeURIComponent(item.candidate_id)}`,
      });
    else onDetail({ kind: "evidence", title: item.title, value: item });
  };
  return (
    <ol className="audit-list plain-titles">
      {items.map((item) => (
        <li key={item.id}>
          <span className="audit-time num" title={time(item.at, true)}>
            {time(item.at)}
          </span>
          <span className={`audit-marker ${feedTone(item.tone)}`} />
          <button onClick={() => open(item)}>
            <strong>{item.title}</strong>
            <span title={item.detail || undefined}>
              {item.detail || words(item.kind)}
            </span>
          </button>
          <Icon name="chevron" />
        </li>
      ))}
    </ol>
  );
}

function PaperFunnelTable({
  funnel,
}: {
  funnel: { last_24h?: PaperFunnel; all_time?: PaperFunnel };
}) {
  return (
    <div className="table-scroll">
      <table className="paper-funnel">
        <thead>
          <tr>
            <th>Step</th>
            <th className="numeric">24 h</th>
            <th className="numeric">All time</th>
          </tr>
        </thead>
        <tbody>
          {FUNNEL_STEPS.map((step) => (
            <tr key={step.key}>
              <td>
                <strong>{step.label}</strong>
                {step.hint && <small>{step.hint}</small>}
              </td>
              <td className="numeric num">
                {(funnel.last_24h?.[step.key] ?? 0).toLocaleString()}
              </td>
              <td className="numeric num">
                {(funnel.all_time?.[step.key] ?? 0).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Where the executable return sits between the hard stop and the trailing-stop trigger. */
function ExitLadder({ position }: { position: PaperOpenPosition }) {
  const ticket = number(position.ticket_sol);
  const stop = number(position.hard_stop_return);
  const armAt = number(position.trailing_arms_at_sol);
  const trailAt = number(position.trailing_stop_sol);
  if (!ticket || stop === null || armAt === null) return null;
  const arm = armAt / ticket - 1;
  const trail =
    position.trailing_armed && trailAt !== null ? trailAt / ticket - 1 : null;
  const now = number(position.return);
  const peak = number(position.peak_return);
  const low = Math.min(stop, now ?? stop);
  const high = Math.max(arm, peak ?? arm, now ?? arm);
  const x = (value: number) => ((value - low) / (high - low || 1)) * 300;
  return (
    <div
      className="exit-ladder"
      role="img"
      aria-label={`Return now ${pct(now)}. Hard stop at ${pct(stop)}. Trailing stop ${
        trail === null ? `arms at ${pct(arm)}` : `armed at ${pct(trail)}`
      }.`}
    >
      <svg viewBox="0 0 300 10" preserveAspectRatio="none" aria-hidden="true">
        <rect width="300" height="10" rx="2" className="ladder-track" />
        <rect
          x={x(stop)}
          width={Math.max(0, x(0) - x(stop))}
          height="10"
          className="ladder-loss"
        />
        <rect
          x={x(0)}
          width={Math.max(0, x(arm) - x(0))}
          height="10"
          className="ladder-gain"
        />
        {trail !== null && (
          <rect
            x={x(trail) - 1}
            width="2"
            height="10"
            className="ladder-trail"
          />
        )}
        <rect
          x={x(0) - 0.75}
          width="1.5"
          height="10"
          className="ladder-entry"
        />
        {now !== null && (
          <rect
            x={Math.min(297, Math.max(0, x(now) - 1.5))}
            width="3"
            height="10"
            className="ladder-now"
          />
        )}
      </svg>
      <div className="ladder-labels num">
        <span>stop {pct(stop)}</span>
        <span>now {pct(now)}</span>
        <span>
          {trail === null ? `trail arms ${pct(arm)}` : `trail ${pct(trail)}`}
        </span>
      </div>
    </div>
  );
}

function PaperPositionCard({
  position: p,
  onDetail,
}: {
  position: PaperOpenPosition;
  onDetail: (detail: Detail) => void;
}) {
  const name = p.symbol || short(p.mint || p.id);
  return (
    <button
      className="position-card"
      onClick={() =>
        onDetail({
          kind: "position",
          title: name,
          endpoint: `/api/positions/${encodeURIComponent(p.id)}`,
        })
      }
    >
      <div className="position-head">
        <Identicon mint={p.mint || p.id} />
        <span>
          <strong>{name}</strong>
          <small>
            {p.strategy || "Strategy not recorded"} · held{" "}
            {duration(p.held_seconds)} · {p.regime || "uncalibrated"} regime
          </small>
        </span>
        {p.conviction && <Badge status="PASS">Sized up</Badge>}
        <Badge status={p.state === "OPEN" ? "ACTIVE" : p.state}>
          {words(p.state || "OPEN")}
        </Badge>
      </div>
      <div className="position-numbers">
        <div>
          <span>Bought for</span>
          <strong className="num">
            {fmt(p.ticket_sol, 4)} <small>SOL</small>
          </strong>
        </div>
        <div>
          <span>
            {p.mark_kind === "EXECUTABLE" ? "Could sell for" : "Latest mark"}
          </span>
          <strong className="num">
            {fmt(p.mark_sol, 4)} <small>SOL</small>
          </strong>
        </div>
        <div>
          <span>Return now</span>
          <strong className={`num ${signedClass(p.return)}`}>
            {pct(p.return)}
          </strong>
        </div>
      </div>
      <ExitLadder position={p} />
      <dl className="paper-levels">
        <div>
          <dt>Hard stop</dt>
          <dd className="num">
            {fmt(p.hard_stop_sol, 4)} SOL{" "}
            <span className="muted">{pct(p.hard_stop_return)}</span>
          </dd>
        </div>
        <div>
          <dt>
            {p.trailing_armed
              ? "Trailing stop (armed)"
              : "Trailing stop arms at"}
          </dt>
          <dd className="num">
            {fmt(
              p.trailing_armed ? p.trailing_stop_sol : p.trailing_arms_at_sol,
              4,
            )}{" "}
            SOL
            {number(p.peak_return) !== null && (
              <span className="muted"> · best {pct(p.peak_return)}</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Time limit</dt>
          <dd className="num">
            {p.hold_remaining_seconds === undefined
              ? "—"
              : `${duration(p.hold_remaining_seconds)} left of ${duration(p.max_hold_seconds)}`}
          </dd>
        </div>
        <div>
          <dt>Fees so far · rent locked</dt>
          <dd className="num">
            {fmt(p.entry_fees_sol, 6)} · {fmt(p.rent_locked_sol, 4)} SOL
          </dd>
        </div>
      </dl>
      {p.exit_reason && (
        <p className="paper-exit-note">
          Selling: {p.exit_reason}
          {p.exit_attempts ? ` · attempt ${p.exit_attempts}` : ""}
        </p>
      )}
      <div className="position-foot">
        <span className={`mark-kind ${tone(p.mark_kind || "")}`}>
          {p.mark_kind === "EXECUTABLE"
            ? "EXECUTABLE SELL QUOTE"
            : words(p.mark_kind || "NO SELL QUOTE YET")}
        </span>
        <span>Mark {markFreshness(p.mark_at || undefined)}</span>
        <Icon name="arrow" />
      </div>
    </button>
  );
}

function ClosedTrades({
  trades,
  onDetail,
}: {
  trades: PaperClosedTrade[];
  onDetail: (detail: Detail) => void;
}) {
  const [rows, setRows] = useState(25);
  if (!trades.length)
    return (
      <Empty
        icon="receipt"
        compact
        title="No closed trades yet"
        detail="Each sale appears here with its result after every fee, the reason it sold, and how the observed and stress fills compare."
      />
    );
  return (
    <>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Token</th>
              <th>Sold · local time</th>
              <th className="numeric">Held</th>
              <th className="numeric">Ticket</th>
              <th className="numeric">Result</th>
              <th className="numeric">Return</th>
              <th
                className="numeric"
                title="Same trade under the observed and stress cost profiles"
              >
                Observed / stress
              </th>
              <th
                className="numeric"
                title="Network fees for buy, sell and closing the token account, plus failed attempts"
              >
                Fees paid
              </th>
              <th>Why it sold</th>
              <th>
                <span className="sr-only">Details</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {trades.slice(0, rows).map((t) => {
              const name = t.symbol || short(t.mint || t.id);
              const open = () =>
                onDetail({
                  kind: "position",
                  title: name,
                  endpoint: `/api/positions/${encodeURIComponent(t.id)}`,
                });
              return (
                <tr key={t.id}>
                  <td>
                    <button className="token-button" onClick={open}>
                      <Identicon mint={t.mint || t.id} />
                      <span>
                        <strong>{name}</strong>
                        <small>
                          {t.strategy}
                          {t.conviction ? " · sized up" : ""}
                        </small>
                      </span>
                    </button>
                  </td>
                  <td
                    className="secondary num text-small"
                    title={time(t.closed_at, true)}
                  >
                    {time(t.closed_at, true)}
                  </td>
                  <td className="numeric num">{duration(t.hold_seconds)}</td>
                  <td className="numeric num">{fmt(t.ticket_sol, 4)}</td>
                  <td className={`numeric num ${signedClass(t.pnl_sol)}`}>
                    {signed(t.pnl_sol, 6)}
                  </td>
                  <td className={`numeric num ${signedClass(t.return)}`}>
                    {pct(t.return)}
                  </td>
                  <td className="numeric num secondary">
                    {pct(t.observed_return)} / {pct(t.stress_return)}
                  </td>
                  <td
                    className="numeric num"
                    title={`${t.fee_source === "MEASURED" ? "Measured" : "Conservative model"} network fees`}
                  >
                    {fmt(t.fees_sol, 6)}{" "}
                    <span className="muted">
                      {share(t.fees_pct_of_ticket, 2)}
                    </span>
                  </td>
                  <td className="secondary text-small">
                    {t.exit_reason || "—"}
                  </td>
                  <td>
                    <button
                      className="icon-button"
                      aria-label={`Open ${name} trade`}
                      onClick={open}
                    >
                      <Icon name="chevron" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {trades.length > rows && (
        <div className="panel-foot">
          Showing {rows} of {trades.length} most recent closed trades.{" "}
          <button
            className="text-button"
            onClick={() => setRows((r) => r + 25)}
          >
            Show 25 more
          </button>
        </div>
      )}
    </>
  );
}

function PaperView({ onDetail }: { onDetail: (detail: Detail) => void }) {
  const { data, error } = usePaperTab();
  const [filter, setFilter] = useState<FeedFilter>("all");
  if (!data)
    return error ? (
      <div className="notice warning" role="status">
        <Icon name="info" />
        <span>{error}</span>
      </div>
    ) : (
      <div className="loading-state">
        <span className="loading-ring" />
        Reading the paper book…
      </div>
    );
  if (!data.initialized)
    return (
      <>
        <PaperStatusBanner status={data.status} />
        <div className="dashboard-grid">
          <Panel
            title="Getting started"
            eyebrow="PAPER TRADING"
            className="span-12"
          >
            <Empty
              icon="receipt"
              title="Start PAPER to open the learning book."
              detail="Use Start PAPER above or run desk paper in a terminal. The book opens with $100,000 of simulated cash, and every ticket is sized as your $100 account would size it."
            />
          </Panel>
        </div>
      </>
    );
  const book = data.book ?? {};
  const activity = data.activity ?? [];
  const groups: Record<string, number> = {};
  for (const item of activity)
    if (item.group) groups[item.group] = (groups[item.group] ?? 0) + 1;
  const feed =
    filter === "all"
      ? activity
      : activity.filter((item) => item.group === filter);
  const open = data.open ?? [];
  const fees = data.fees;
  const estimate = fees?.current_estimate;
  const moon = data.moonshot;
  const replica = data.replica;
  const curve: Snapshot["equity"] = data.curve?.length
    ? [
        {
          at: book.started_at ?? data.curve[0].at,
          value_sol: 0,
          kind: "realized",
        },
        ...data.curve.map((point) => ({
          at: point.at,
          value_sol: point.realized_sol,
          kind: "realized",
        })),
      ]
    : [];
  const blockers = book.next_ticket_blockers ?? [];
  return (
    <>
      <PaperStatusBanner status={data.status} hours={book.hours_running} />
      {error && (
        <div className="notice warning" role="status">
          <Icon name="info" />
          <span>{error} Showing the last reading.</span>
        </div>
      )}
      <div className="metrics-strip">
        <Metric
          label="Paper cash"
          value={fmt(book.cash_sol, 2)}
          suffix="SOL"
          detail={`Simulated · ${fmt(book.deposit_sol, 2)} SOL at the start`}
          title="Simulated cash in the learning book ($100,000 at the start). Open positions and locked rent are not included. Not real funds."
        />
        <Metric
          label="Next ticket"
          value={blockers.length ? "—" : fmt(book.next_ticket_sol, 4)}
          suffix={blockers.length ? undefined : "SOL"}
          detail={
            blockers.length
              ? `Not now: ${blockers[0]}`
              : `10% of ${fmt(book.sizing_capital_sol, 4)} SOL ($100 base + profit)`
          }
          title="Size of the next normal entry: 10% of the $100-account base plus realized profit. Losses never shrink the base. An evidence-backed moonshot signal may use up to 15%."
        />
        <Metric
          label="Realized P&L"
          value={signed(book.realized_sol, 4)}
          suffix="SOL"
          positive={(number(book.realized_sol) ?? 0) > 0}
          detail={`${pct(book.return_on_base)} of the $100 base · stress ${signed(book.realized_stress_sol, 4)}`}
          title="Result of closed paper trades under the conservative fill: the worse of two quotes, simulated failures, and every network fee. Observed and stress profiles are shown for comparison."
        />
        <Metric
          label="Open now"
          value={`${book.open_positions ?? 0} / ${book.max_positions ?? "—"}`}
          detail={`${signed(book.unrealized_sol, 4)} SOL unrealized · executable quotes`}
          title="Open paper positions out of the concurrent limit. Unrealized P&L uses executable sell quotes only and can change before an exit."
        />
        <Metric
          label="Win rate"
          value={share(book.win_rate)}
          detail={`${book.closed_trades ?? 0} closed trades`}
          title="Share of closed paper trades with a positive conservative result. A small sample says little; the confidence matrix in Research measures it properly."
        />
      </div>
      <div className="dashboard-grid">
        <Panel
          title="What it's doing"
          eyebrow="NEWEST FIRST · PLAIN ENGLISH"
          className="span-8"
          action={
            <span className="subtle-chip">
              refreshes every {PAPER_REFRESH_MS / 1000} s
            </span>
          }
        >
          <div className="feed-toolbar">
            <div
              className="chart-periods"
              role="group"
              aria-label="Filter activity"
            >
              {FEED_FILTERS.map((option) => (
                <button
                  key={option.id}
                  aria-pressed={filter === option.id}
                  onClick={() => setFilter(option.id)}
                >
                  {option.label}
                  {option.id !== "all" && groups[option.id]
                    ? ` · ${groups[option.id]}`
                    : ""}
                </button>
              ))}
            </div>
          </div>
          <PaperFeed
            items={feed}
            filtered={filter !== "all"}
            onDetail={onDetail}
          />
        </Panel>
        <div className="span-4 panel-stack">
          <Panel title="From token to trade" eyebrow="FUNNEL">
            <PaperFunnelTable funnel={data.funnel ?? {}} />
          </Panel>
          <Panel title="Why entries were skipped" eyebrow="ALL TIME">
            <Counts
              values={data.blocked_reasons}
              empty="Nothing has been blocked or skipped yet."
              plain
            />
          </Panel>
        </div>
        <Panel
          title="Open paper positions"
          eyebrow="EXITS RUN BY THE GUARDIAN"
          className="span-12"
          action={
            <span className="subtle-chip">
              stops checked every 5 s · sell quote every 25 s
            </span>
          }
        >
          {open.length ? (
            <div className="positions-grid">
              {open.map((position) => (
                <PaperPositionCard
                  key={position.id}
                  position={position}
                  onDetail={onDetail}
                />
              ))}
            </div>
          ) : (
            <Empty
              icon="layers"
              compact
              title="Flat right now."
              detail="No open paper positions. A position appears here the moment a strategy buys, with its hard stop, trailing stop and time limit."
            />
          )}
        </Panel>
        <Panel
          title="Closed trades"
          eyebrow="NEWEST FIRST · AFTER EVERY FEE"
          className="span-12"
          action={
            <a className="text-button" href="/api/export/trades.csv" download>
              Download CSV <Icon name="download" />
            </a>
          }
        >
          <ClosedTrades trades={data.closed ?? []} onDetail={onDetail} />
        </Panel>
        <Panel
          title="Realized P&L over time"
          eyebrow="CLOSED TRADES ONLY"
          className="span-8"
        >
          <EquityChart
            points={curve}
            label="Cumulative realized P&L · SOL"
            emptyTitle="The curve starts with the first sale."
            emptyDetail="Cumulative realized P&L appears after the first closed paper trade."
          />
        </Panel>
        <Panel
          title="$100 account replica"
          eyebrow="YOUR ACCOUNT, REPLAYED"
          className="span-4"
        >
          <div className="replica-card">
            <span className="eyebrow">ESTIMATED RETURN</span>
            <strong
              className={`replica-return num ${signedClass(replica?.return)}`}
            >
              {pct(replica?.return)}
            </strong>
            <p>
              The same fills replayed through the real $100 account's limits:
              one ticket at a time per slot, daily loss and drawdown stops, and
              fixed fees. {replica?.note ? `(${replica.note})` : ""}
            </p>
            <dl className="simple-facts">
              <div>
                <dt>Estimated equity</dt>
                <dd className="num">{fmt(replica?.equity_sol, 4)} SOL</dd>
              </div>
              <div>
                <dt>Trades it could take</dt>
                <dd className="num">{replica?.trades_taken ?? "—"}</dd>
              </div>
              <div>
                <dt>Skipped by its limits</dt>
                <dd className="num">{count(replica?.skipped)}</dd>
              </div>
              <div>
                <dt>Maximum drawdown</dt>
                <dd className="num">{share(replica?.max_drawdown, 2)}</dd>
              </div>
            </dl>
          </div>
        </Panel>
        <Panel
          title="Fees, counted on every trade"
          eyebrow="NETWORK + SWAP"
          className="span-6"
          action={
            <Badge status={estimate?.source === "MEASURED" ? "PASS" : "WARN"}>
              {estimate?.source === "MEASURED"
                ? "Measured"
                : "Conservative model"}
            </Badge>
          }
        >
          <div className="pad fee-panel">
            <dl className="simple-facts">
              <div>
                <dt>Network fees paid so far</dt>
                <dd className="num">{fmt(fees?.paid_total_sol, 6)} SOL</dd>
              </div>
              <div>
                <dt>Average per round trip</dt>
                <dd className="num">
                  {number(fees?.average_round_trip_pct) === null
                    ? "—"
                    : `${share(fees?.average_round_trip_pct, 2)} of the ticket`}
                </dd>
              </div>
              <div>
                <dt>Each transaction now</dt>
                <dd className="num">
                  {lamportsAsSol(estimate?.per_transaction_lamports)}
                  <span className="muted">
                    {" "}
                    × {estimate?.transactions_per_round_trip ?? 3} per round
                    trip
                  </span>
                </dd>
              </div>
              <div>
                <dt>Priority fee basis</dt>
                <dd className="num">
                  {estimate?.source === "MEASURED"
                    ? `p50 ${lamportsAsSol(estimate.priority_p50_lamports, 7)} · p90 ${lamportsAsSol(estimate.priority_p90_lamports, 7)} · ${estimate.observations} probes`
                    : `${estimate?.observations ?? 0} of 6 probes needed for measured fees`}
                </dd>
              </div>
              <div>
                <dt>Token-account rent locked</dt>
                <dd className="num">
                  {fmt(fees?.rent_locked_sol, 6)} SOL · refunded on sale
                </dd>
              </div>
            </dl>
          </div>
          <ul className="plain-list">
            {(fees?.explanation ?? []).map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        </Panel>
        <Panel
          title="Moonshot watch"
          eyebrow="H8 · RARE RUNNERS"
          className="span-6"
          action={
            <Badge
              status={moon?.conviction_evidence === "PASS" ? "PASS" : "PENDING"}
            >
              {moon?.conviction_evidence === "PASS"
                ? "Size-up allowed"
                : "Normal size only"}
            </Badge>
          }
        >
          <div className="pad">
            <p className="moonshot-copy">
              H8 buys only a confirmed runner: up 50% in the last hour and still
              climbing, volume accelerating, heavy buying, liquidity growing
              with the price, and no whale or developer concentration. It is
              built to bank the run before the dump: a 15% hard stop, a trailing
              stop that arms at +25% and gives back at most 12%, and a 2-hour
              limit.
            </p>
            <dl className="simple-facts">
              <div>
                <dt>H8 trades · closed / open</dt>
                <dd className="num">
                  {moon?.closed ?? 0} / {moon?.open ?? 0}
                  <span className="muted"> · {moon?.wins ?? 0} won</span>
                </dd>
              </div>
              <div>
                <dt>H8 realized P&L</dt>
                <dd className={`num ${signedClass(moon?.pnl_sol)}`}>
                  {moon?.closed
                    ? `${signed(moon.pnl_sol, 6)} SOL`
                    : "no closed H8 trades yet"}
                </dd>
              </div>
              <div>
                <dt>Ticket · normal / sized up</dt>
                <dd className="num">
                  {fmt(moon?.normal_ticket_sol, 4)} /{" "}
                  {fmt(moon?.sized_up_ticket_sol, 4)} SOL
                </dd>
              </div>
              <div>
                <dt>Sized-up trades</dt>
                <dd className="num">{moon?.sized_up_trades ?? 0}</dd>
              </div>
              <div>
                <dt>Evidence</dt>
                <dd>
                  <Badge
                    status={
                      moon?.conviction_evidence === "PASS" ? "PASS" : "PENDING"
                    }
                  >
                    {words(moon?.conviction_evidence || "NO REPORT")}
                  </Badge>
                </dd>
              </div>
            </dl>
            <p className="panel-note moonshot-detail">
              {moon?.conviction_detail}
              {moon?.rule ? ` · Rule: ${moon.rule}.` : ""}
            </p>
          </div>
        </Panel>
        <Panel title="By strategy" eyebrow="WHO IS TRADING" className="span-8">
          {data.strategies?.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th className="numeric">Closed</th>
                    <th className="numeric">Open</th>
                    <th className="numeric">Win rate</th>
                    <th className="numeric">Average return</th>
                    <th className="numeric">Realized P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {data.strategies.map((row) => (
                    <tr key={row.strategy}>
                      <td>
                        <button
                          className="text-button"
                          onClick={() =>
                            onDetail({
                              kind: "strategy",
                              title: row.strategy,
                              endpoint: `/api/strategies/${encodeURIComponent(row.strategy)}`,
                            })
                          }
                        >
                          {row.strategy}
                        </button>
                      </td>
                      <td className="numeric num">{row.closed ?? 0}</td>
                      <td className="numeric num">{row.open ?? 0}</td>
                      <td className="numeric num">{share(row.win_rate)}</td>
                      <td
                        className={`numeric num ${signedClass(row.mean_return)}`}
                      >
                        {pct(row.mean_return)}
                      </td>
                      <td className={`numeric num ${signedClass(row.pnl_sol)}`}>
                        {signed(row.pnl_sol, 6)} SOL
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              icon="sliders"
              compact
              title="No strategy has traded yet"
              detail="Rows appear when a strategy opens its first paper position."
            />
          )}
        </Panel>
        <Panel
          title="How trades ended"
          eyebrow="EXIT REASONS"
          className="span-4"
        >
          <Counts
            values={data.exit_reasons}
            empty="No closed trades yet."
            plain
          />
        </Panel>
        <Panel
          title="How to read this page"
          eyebrow="NOTES"
          className="span-12"
        >
          <ul className="plain-list notes">
            {(data.notes ?? []).map((line) => (
              <li key={line}>{line}</li>
            ))}
            <li>
              Paper results are evidence for the confidence matrix, not a
              promise. Real money stays off until you decide, and the LIVE gates
              still have to pass.
            </li>
          </ul>
        </Panel>
      </div>
    </>
  );
}

type Modal = "start-shadow" | "start-paper" | "stop" | "help" | null;
function ActionDialog({
  modal,
  onClose,
  onAction,
  busy,
}: {
  modal: Modal;
  onClose: () => void;
  onAction: (action: string, body?: Record<string, unknown>) => Promise<void>;
  busy: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [minutes, setMinutes] = useState("60");
  useEffect(() => {
    if (modal) {
      ref.current?.showModal();
    } else ref.current?.close();
  }, [modal]);
  const start = modal === "start-paper" || modal === "start-shadow";
  const validMinutes = Number(minutes) > 0 && Number(minutes) <= 10080;
  return (
    <dialog
      ref={ref}
      className="action-dialog"
      aria-labelledby="action-title"
      onCancel={(e) => {
        if (busy) e.preventDefault();
        else onClose();
      }}
    >
      <div className="dialog-top">
        <span className="dialog-emblem">
          <Icon
            name={
              modal === "help"
                ? "terminal"
                : modal === "stop"
                  ? "stop"
                  : "shield"
            }
          />
        </span>
        <button
          className="icon-button"
          onClick={onClose}
          disabled={busy}
          aria-label="Close dialog"
        >
          <Icon name="close" />
        </button>
      </div>
      <h2 id="action-title">
        {modal === "help"
          ? "Move at the speed of thought."
          : modal === "stop"
            ? "Stop new entries?"
            : modal === "start-paper"
              ? "Start a PAPER session"
              : "Start a SHADOW session"}
      </h2>
      {modal === "help" ? (
        <>
          <p className="secondary">
            Keyboard shortcuts for your local research desk.
          </p>
          <dl className="shortcuts">
            {[
              ["g o", "Overview"],
              ["g s", "Scanner"],
              ["g t", "Paper trading"],
              ["g p", "Positions"],
              ["g r", "Research"],
              ["/", "Search mint or token"],
              ["Shift S", "Confirm stop new entries"],
              ["Esc", "Close dialog or dossier"],
              ["?", "Keyboard shortcuts"],
            ].map(([key, label]) => (
              <div key={key}>
                <dt>{label}</dt>
                <dd>
                  <kbd>{key}</kbd>
                </dd>
              </div>
            ))}
          </dl>
        </>
      ) : (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!modal || busy || (start && !validMinutes)) return;
            void onAction(modal, start ? { minutes: Number(minutes) } : {});
          }}
        >
          <p className="dialog-copy">
            {modal === "stop"
              ? "The desk will block new entries. Position monitoring and protective exits remain active. This does not close existing positions."
              : modal === "start-paper"
                ? "Run simulated entries and protective exits using recorded market observations and quote costs. No real funds are used."
                : "Observe and record strategy decisions without opening positions. No real funds are used."}
          </p>
          {start && (
            <label className="form-label">
              Session duration{" "}
              <span className="muted">minutes · max 7 days</span>
              <input
                type="number"
                min="1"
                max="10080"
                step="1"
                required
                value={minutes}
                onChange={(e) => setMinutes(e.target.value)}
              />
            </label>
          )}
          <div className="dialog-actions">
            <button
              type="button"
              className="button secondary-button"
              onClick={onClose}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="submit"
              className={`button ${modal === "stop" ? "danger-button" : "primary-button"}`}
              disabled={busy || (start && !validMinutes)}
            >
              {busy
                ? "Submitting…"
                : modal === "stop"
                  ? "Stop new entries"
                  : "Start simulation"}
              <Icon name={modal === "stop" ? "stop" : "arrow"} />
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}

function readView(): View {
  const value = window.location.hash.slice(1).split("?")[0];
  return NAV.some((v) => v.id === value) ? (value as View) : "overview";
}
export default function App() {
  const { data, connection, error, refresh } = useDashboard();
  const snapshot: Snapshot = data ?? {};
  const [view, setView] = useState<View>(readView);
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [modal, setModal] = useState<Modal>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ text: string; bad: boolean } | null>(
    null,
  );
  const prefix = useRef(0);
  const paper = snapshot.paper;
  const mode = (snapshot.mode || "PAPER").toUpperCase();
  const workerActive =
    (snapshot.workers ?? []).some(
      (w) => w.active && w.name !== "guardian" && w.name !== "dashboard",
    ) ||
    (snapshot.jobs ?? []).some(
      (j) => j.returncode === null && j.name !== "report",
    );
  const matrices = snapshot.confidence?.matrices ?? [];
  const navigate = (next: View) => {
    window.location.hash = next;
    setView(next);
  };
  useEffect(() => {
    const hash = () => {
      setView(readView());
      setDetail(null);
    };
    window.addEventListener("hashchange", hash);
    return () => window.removeEventListener("hashchange", hash);
  }, []);
  useEffect(() => {
    document.title = `KESTREL · ${NAV.find((v) => v.id === view)?.label ?? "Desk"} · ${mode}`;
  }, [view, mode]);
  useEffect(() => {
    const keyboard = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        target.closest('input,textarea,select,[contenteditable="true"]') ||
        event.ctrlKey ||
        event.metaKey ||
        event.altKey ||
        modal ||
        detail
      )
        return;
      if (event.key === "?") {
        event.preventDefault();
        setModal("help");
      } else if (event.key === "S" && event.shiftKey) {
        event.preventDefault();
        setModal("stop");
      } else if (event.key === "/") {
        event.preventDefault();
        navigate("scanner");
        window.setTimeout(
          () => window.dispatchEvent(new Event("kestrel-search")),
          0,
        );
      } else if (event.key.toLowerCase() === "g") prefix.current = Date.now();
      else if (Date.now() - prefix.current < 1200) {
        const next = (
          {
            o: "overview",
            s: "scanner",
            t: "paper",
            p: "positions",
            r: "research",
          } as Record<string, View>
        )[event.key.toLowerCase()];
        if (next) {
          event.preventDefault();
          navigate(next);
        }
        prefix.current = 0;
      }
    };
    window.addEventListener("keydown", keyboard);
    return () => window.removeEventListener("keydown", keyboard);
  }, [modal, detail]);
  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(null), 12_000);
    return () => window.clearTimeout(timeout);
  }, [notice]);
  const runAction = async (
    action: string,
    body: Record<string, unknown> = {},
  ) => {
    if (busy) return;
    setBusy(true);
    try {
      const response = await control(action, body);
      const message =
        response && typeof response === "object" && "message" in response
          ? String(response.message)
          : `${words(action)} request accepted. Follow worker status for progress.`;
      setNotice({ text: message, bad: false });
      setModal(null);
      void refresh();
    } catch (cause) {
      setNotice({
        text:
          cause instanceof Error
            ? cause.message
            : "The action could not be completed.",
        bad: true,
      });
      setModal(null);
    } finally {
      setBusy(false);
    }
  };
  const statusLabel = !data
    ? "Connecting to desk"
    : snapshot.status?.halted
      ? "Entries halted"
      : snapshot.status?.label || "Awaiting session";
  const title = {
    overview: "Your edge, in perspective.",
    scanner: "Find the signal.",
    paper: "Watch the book learn.",
    positions: "Every position, accounted for.",
    strategies: "Rules before conviction.",
    research: "Evidence compounds.",
    audit: "Every decision leaves a trail.",
    system: "A clear view of the machine.",
    live: "Real capital requires proof.",
  }[view];
  const subtitle = {
    overview: "A patient desk. A complete picture. Real observations only.",
    scanner: "Source observations, security decisions, and market context.",
    paper:
      "Every look, skip, buy and sell in plain English. $100-account tickets, real quotes, fees included.",
    positions: "Simulated exposure, executable marks, and protective exits.",
    strategies:
      "Versioned strategy evidence. No automatic promotion to real trading.",
    research: "Sample size, uncertainty, and the cost of being wrong.",
    audit:
      "Recorded events from discovery through execution and operator controls.",
    system: "Local workers, provider health, and resource headroom.",
    live: "Read the gates. Arming is available only through the CLI.",
  }[view];
  return (
    <div className={`app mode-${mode.toLowerCase()}`}>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        Skip to content
      </a>
      <aside className="rail">
        <a
          className="rail-brand"
          href="#overview"
          aria-label="KESTREL overview"
        >
          <Mark />
        </a>
        <nav aria-label="Main navigation">
          {NAV.map((item) => (
            <a
              key={item.id}
              href={`#${item.id}`}
              className={`nav-item ${view === item.id ? "active" : ""} ${item.id === "live" ? "live-nav" : ""}`}
              aria-current={view === item.id ? "page" : undefined}
              title={item.key ? `${item.label} (${item.key})` : item.label}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {view === item.id && <span className="nav-indicator" />}
            </a>
          ))}
        </nav>
        <div className="rail-bottom">
          <button
            className="nav-item"
            onClick={() => setModal("help")}
            title="Keyboard shortcuts (?)"
            aria-label="Keyboard shortcuts"
          >
            <span className="help-symbol">?</span>
            <span>Shortcuts</span>
          </button>
          <span className="rail-version">DESK / 01</span>
        </div>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <a className="wordmark" href="#overview">
            KESTREL<span>RESEARCH TERMINAL</span>
          </a>
          <div className="header-divider" />
          <span className={`mode-pill ${mode.toLowerCase()}`}>
            <span className="status-point" />
            {mode}
          </span>
          <span className="simulation-label">
            {mode === "LIVE" ? "REAL CAPITAL" : "SIMULATED CAPITAL"}
          </span>
          <div className="desk-status">
            <span
              className={`health-dot ${snapshot.status?.halted ? "warning" : data ? "positive" : "neutral"}`}
            />
            <span title={snapshot.status?.reason}>{statusLabel}</span>
          </div>
          <div className="topbar-right">
            <div className="provider-dots" aria-label="Provider health">
              {(snapshot.health ?? []).slice(0, 5).map((h) => (
                <span
                  className={`health-dot ${tone(h.state)}`}
                  key={h.provider}
                  title={`${h.provider}: ${h.state || "unknown"}`}
                />
              ))}
            </div>
            <span
              className={`connection-indicator ${connection}`}
              title={error || `Data connection: ${connection}`}
            >
              <Icon name="pulse" />
              <span>
                {connection === "live"
                  ? "CONNECTED"
                  : connection === "retrying"
                    ? "RECONNECTING"
                    : "CONNECTING"}
              </span>
            </span>
            <span
              className="header-time num"
              title="Snapshot time in your local timezone"
            >
              {time(snapshot.generated_at)}
            </span>
          </div>
        </header>
        <main id="main-content" tabIndex={-1}>
          <div className="page-heading">
            <div>
              <div className="page-kicker">
                <span className="tiny-cross">+</span> WORKSPACE <span>/</span>{" "}
                {NAV.find((n) => n.id === view)?.label.toUpperCase()}
              </div>
              <h1>{title}</h1>
              <p>{subtitle}</p>
            </div>
            <div className="page-actions">
              <button
                className="button secondary-button"
                disabled={busy}
                onClick={() => setModal("stop")}
                title="Stop new entries; keep protective exits active (Shift+S)"
              >
                <Icon name="stop" />
                <span>Stop entries</span>
              </button>
              {
                <button
                  className="button primary-button"
                  disabled={busy || workerActive || !!snapshot.status?.halted}
                  title={
                    workerActive
                      ? "A desk worker is already active"
                      : "Start a simulated PAPER session"
                  }
                  onClick={() => setModal("start-paper")}
                >
                  <Icon name="play" />
                  Start PAPER
                </button>
              }
            </div>
          </div>
          {error && (
            <div className="notice warning" role="status">
              <Icon name="info" />
              <span>
                {error}
                {data && " Showing the last recorded snapshot."}
              </span>
              <button className="text-button" onClick={() => void refresh()}>
                Retry
              </button>
            </div>
          )}
          {snapshot.status?.halted && (
            <div className="notice warning" role="status">
              <Icon name="shield" />
              <span>
                Entries halted:{" "}
                {snapshot.status.reason ||
                  "An operator or risk guard has blocked new entries."}{" "}
                Protective monitoring remains separate. To resume, run desk
                resume in your terminal.
              </span>
            </div>
          )}
          {notice && (
            <div
              className={`notice ${notice.bad ? "negative" : "positive"}`}
              role={notice.bad ? "alert" : "status"}
            >
              <Icon name={notice.bad ? "info" : "check"} />
              <span>{notice.text}</span>
              <button
                className="icon-button"
                onClick={() => setNotice(null)}
                aria-label="Dismiss notification"
              >
                <Icon name="close" />
              </button>
            </div>
          )}
          {(view === "overview" || view === "positions") && (
            <div className="metrics-strip">
              <Metric
                label="Learning-book cash"
                value={fmt(paper?.cash_sol)}
                suffix="SOL"
                detail={
                  paper?.initialized
                    ? "Simulated cash · not account equity"
                    : "No PAPER book initialized"
                }
                title="Cash held by the PAPER learning book. Excludes the value of open positions; not real funds."
              />
              <Metric
                label="Sizing capital"
                value={fmt(paper?.sizing_capital_sol)}
                suffix="SOL"
                detail="Reported capital for position sizing"
                title="Sizing capital reported by the paper engine. Risk, confidence, liquidity, and exact-size costs may constrain each entry."
              />
              <Metric
                label="Realized · conservative"
                value={signed(paper?.realized_pnl_sol?.conservative)}
                suffix="SOL"
                detail={`${paper?.closed_trades ?? 0} closed simulated trades`}
                positive={
                  (number(paper?.realized_pnl_sol?.conservative) ?? 0) > 0
                }
                title="Realized P&L from closed PAPER trades under the conservative cost profile. Open returns are not realized profit."
              />
              <Metric
                label="Open positions"
                value={paper?.open_positions ?? "—"}
                detail={`${fmt(paper?.unrealized_executable_sol)} SOL executable unrealized`}
                title="Open PAPER positions. Unrealized P&L includes only executable sell marks and can change before an exit."
              />
              <Metric
                label="$100 account replica"
                value={pct(paper?.replica_100_usd_account?.return)}
                detail={`${paper?.replica_100_usd_account?.trades_taken ?? 0} trades · estimate from learning fills`}
                title={
                  paper?.replica_100_usd_account?.note ||
                  "Estimated return of a $100 account from learning-book fills, risk limits, and fixed fees. Not a funded account or live result."
                }
              />
            </div>
          )}
          {view === "overview" && (
            <div className="dashboard-grid">
              {!snapshot.discovery_test?.passed && (
                <DiscoveryTestPanel test={snapshot.discovery_test} compact />
              )}
              <Panel
                title="Performance, without projection"
                eyebrow="ACCOUNT HISTORY"
                className="span-8 equity-panel"
                action={
                  <span className="subtle-chip">
                    <span className="series-swatch" />
                    {snapshot.equity?.some((p) => p.kind === "equity")
                      ? "Account equity"
                      : "Realized cash"}{" "}
                    · SOL
                  </span>
                }
              >
                <EquityChart points={snapshot.equity} />
                <div className="chart-footer">
                  <span>
                    <Icon name="clock" />
                    Recorded history · local time
                  </span>
                  <span>No forward returns implied</span>
                </div>
              </Panel>
              <Panel
                title="Entry readiness"
                eyebrow="DISCOVERY → DECISION"
                className="span-4"
              >
                <Funnel
                  funnel={snapshot.funnel}
                  regime={snapshot.regime}
                  onScanner={() => navigate("scanner")}
                />
              </Panel>
              <Panel
                title="Open positions"
                eyebrow="EXPOSURE"
                className="span-8"
                action={
                  <button
                    className="text-button"
                    onClick={() => navigate("positions")}
                  >
                    All positions <Icon name="arrow" />
                  </button>
                }
              >
                <Positions
                  positions={paper?.open}
                  onDetail={setDetail}
                  compact
                />
              </Panel>
              <Panel
                title="Strategy confidence"
                eyebrow="EVIDENCE, NOT A SCORE"
                className="span-4 row-span-2"
                action={
                  <button
                    className="icon-button"
                    title="Open research"
                    aria-label="Open research"
                    onClick={() => navigate("research")}
                  >
                    <Icon name="arrowup" />
                  </button>
                }
              >
                <ReadinessVerdict readiness={snapshot.readiness} compact />
                <Confidence matrices={matrices} onDetail={setDetail} compact />
                <div className="confidence-footer">
                  <Icon name="info" />
                  <span>
                    Passing evidence can permit sizing growth. Missing evidence
                    never becomes a confidence percentage.
                  </span>
                </div>
              </Panel>
              <Panel
                title="Market scanner"
                eyebrow="LATEST SOURCE OBSERVATIONS"
                className="span-8"
                action={
                  <button
                    className="text-button"
                    onClick={() => navigate("scanner")}
                  >
                    Full scanner <Icon name="arrow" />
                  </button>
                }
              >
                <Scanner
                  candidates={snapshot.scanner}
                  search={search}
                  setSearch={setSearch}
                  onDetail={setDetail}
                  compact
                />
              </Panel>
              <Panel
                title="Decision stream"
                eyebrow="AUDIT TRAIL"
                className="span-8"
                action={
                  <button
                    className="text-button"
                    onClick={() => navigate("audit")}
                  >
                    View audit <Icon name="arrow" />
                  </button>
                }
              >
                <Audit events={snapshot.audit} onDetail={setDetail} compact />
              </Panel>
              <Panel
                title="Provider health"
                eyebrow="DEPENDENCIES"
                className="span-4"
                action={
                  <button
                    className="icon-button"
                    title="Open system health"
                    aria-label="Open system health"
                    onClick={() => navigate("system")}
                  >
                    <Icon name="arrowup" />
                  </button>
                }
              >
                <Providers health={snapshot.health} compact />
              </Panel>
            </div>
          )}
          {view === "scanner" && (
            <div className="dashboard-grid">
              <Panel
                title="Discovery candidates"
                eyebrow="OBSERVE → SCREEN → ASSESS"
                className="span-12"
                action={
                  <span className="subtle-chip">
                    {count(snapshot.funnel?.by_state).toLocaleString()}{" "}
                    canonical candidates
                  </span>
                }
              >
                <Scanner
                  candidates={snapshot.scanner}
                  search={search}
                  setSearch={setSearch}
                  onDetail={setDetail}
                />
              </Panel>
              <Panel title="Discovery reasons" className="span-6">
                <Counts
                  values={snapshot.funnel?.by_reason}
                  empty="No discovery reasons recorded."
                />
              </Panel>
              <Panel title="Rejection reasons" className="span-6">
                <Counts
                  values={snapshot.funnel?.rejection_reasons}
                  empty="No screening rejections recorded."
                />
              </Panel>
            </div>
          )}
          {view === "paper" && <PaperView onDetail={setDetail} />}
          {view === "positions" && (
            <div className="dashboard-grid">
              <Panel
                title="Active PAPER positions"
                eyebrow="SIMULATED EXECUTION"
                className="span-12"
                action={<Badge status="PAPER">PAPER ONLY</Badge>}
              >
                <Positions positions={paper?.open} onDetail={setDetail} />
              </Panel>
              <Panel title="Realized cost profiles" className="span-6">
                <div className="cost-profiles">
                  {["observed", "conservative", "stress"].map((profile) => (
                    <Metric
                      key={profile}
                      label={profile}
                      value={signed(paper?.realized_pnl_sol?.[profile])}
                      suffix="SOL"
                      detail="Closed PAPER trades only"
                      title={`Realized simulated P&L under the ${profile} cost profile.`}
                    />
                  ))}
                </div>
                <p className="panel-note pad">
                  {paper?.realized_vs_estimated ||
                    "Executable marks are estimates until an exit is recorded."}
                </p>
              </Panel>
              <Panel title="Entry blocks" className="span-6">
                <Counts
                  values={paper?.entry_blocked_reasons}
                  empty="No entry-block events recorded."
                />
              </Panel>
            </div>
          )}
          {view === "strategies" && (
            <div className="dashboard-grid">
              <Panel
                title="Evaluated strategies"
                eyebrow="VERSIONED RULES & EVIDENCE"
                className="span-8"
              >
                <StrategyTable data={snapshot} onDetail={setDetail} />
              </Panel>
              <Panel
                title="Observation mode"
                eyebrow="SHADOW"
                className="span-4"
              >
                <div className="observation-card">
                  <span className="empty-icon">
                    <Icon name="scan" />
                  </span>
                  <h3>Watch the rules work.</h3>
                  <p>
                    SHADOW records strategy decisions without opening positions.
                    It provides an auditable baseline before PAPER execution.
                  </p>
                  <button
                    className="button secondary-button"
                    disabled={busy || workerActive}
                    onClick={() => setModal("start-shadow")}
                  >
                    <Icon name="play" />
                    Start SHADOW
                  </button>
                </div>
              </Panel>
              <Panel title="Confidence matrices" className="span-12">
                <Confidence matrices={matrices} onDetail={setDetail} />
              </Panel>
            </div>
          )}
          {view === "research" && (
            <div className="dashboard-grid">
              <Panel
                title="Readiness verdict"
                eyebrow="WHEN TO CONSIDER REAL MONEY"
                className="span-12"
              >
                <ReadinessVerdict readiness={snapshot.readiness} />
              </Panel>
              <Panel
                title="Evidence council"
                eyebrow="RESEARCH ONLY · ZERO INFLUENCE"
                className="span-12"
              >
                <CouncilPanel council={snapshot.council} />
              </Panel>
              <Panel
                title="Confidence matrix"
                eyebrow="UNCERTAINTY MADE VISIBLE"
                className="span-8"
                action={
                  <span className="subtle-chip">
                    n = {snapshot.confidence?.closed_trades ?? 0} closed trades
                  </span>
                }
              >
                <Confidence matrices={matrices} onDetail={setDetail} />
              </Panel>
              <Panel
                title="$100 account replica"
                eyebrow="CONSTRAINED ESTIMATE"
                className="span-4"
              >
                <div className="replica-card">
                  <span className="eyebrow">ESTIMATED RETURN</span>
                  <strong className="replica-return num">
                    {pct(paper?.replica_100_usd_account?.return)}
                  </strong>
                  <p>
                    {paper?.replica_100_usd_account?.note ||
                      "The replica replays the learning-book fills with small-account risk limits and fixed fees. It is not live performance."}
                  </p>
                  <dl className="simple-facts">
                    <div>
                      <dt>Estimated equity</dt>
                      <dd className="num">
                        {fmt(paper?.replica_100_usd_account?.equity_sol)} SOL
                      </dd>
                    </div>
                    <div>
                      <dt>Trades taken</dt>
                      <dd className="num">
                        {paper?.replica_100_usd_account?.trades_taken ?? "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>Maximum drawdown</dt>
                      <dd className="num">
                        {number(
                          paper?.replica_100_usd_account?.max_drawdown,
                        ) === null
                          ? "—"
                          : `${fmt(Number(paper?.replica_100_usd_account?.max_drawdown) * 100, 2)}%`}
                      </dd>
                    </div>
                  </dl>
                </div>
              </Panel>
              <Panel title="Export your evidence" className="span-12">
                <div className="export-row">
                  <div>
                    <h3>Portable, auditable trade records.</h3>
                    <p>
                      Downloads contain the latest 10,000 recorded PAPER trades.
                      Empty books produce empty exports.
                    </p>
                  </div>
                  <a
                    className="button secondary-button"
                    href="/api/export/trades.csv"
                    download
                  >
                    <Icon name="download" />
                    Download CSV
                  </a>
                  <a
                    className="button secondary-button"
                    href="/api/export/trades.json"
                    download
                  >
                    <Icon name="download" />
                    Download JSON
                  </a>
                  <button
                    className="button secondary-button"
                    disabled={busy}
                    onClick={() => void runAction("report")}
                  >
                    <Icon name="chart" />
                    Generate report
                  </button>
                </div>
              </Panel>
            </div>
          )}
          {view === "audit" && (
            <div className="dashboard-grid">
              <Panel
                title="Recorded events"
                eyebrow="LATEST FIRST · BOUNDED VIEW"
                className="span-12"
                action={
                  <span className="subtle-chip">
                    {snapshot.audit?.length ?? 0} loaded events
                  </span>
                }
              >
                <Audit events={snapshot.audit} onDetail={setDetail} />
              </Panel>
            </div>
          )}
          {view === "system" && (
            <div className="dashboard-grid">
              <DiscoveryTestPanel test={snapshot.discovery_test} />
              <Panel
                title="Machine resources"
                eyebrow="SERVING HOST"
                className="span-6"
              >
                <SystemFacts data={snapshot} />
              </Panel>
              <Panel
                title="Provider health"
                eyebrow="LATEST RECORDED STATE"
                className="span-6"
              >
                <Providers health={snapshot.health} />
              </Panel>
              <Panel title="Worker supervision" className="span-8">
                <Workers workers={snapshot.workers} />
              </Panel>
              <Panel title="Book reconciliation" className="span-4">
                <div className="pad">
                  <Evidence value={paper?.reconciliation} />
                </div>
              </Panel>
              <Panel title="Session jobs" className="span-12">
                <Jobs jobs={snapshot.jobs} />
              </Panel>
              <Panel title="Operations" className="span-12">
                <div className="export-row">
                  <div>
                    <h3>Explicit controls. Recorded actions.</h3>
                    <p>
                      Stop blocks new entries while position monitoring
                      continues. To resume entries, run desk resume in your
                      terminal.
                    </p>
                  </div>
                  <button
                    className="button secondary-button"
                    onClick={() => setModal("start-shadow")}
                    disabled={busy || workerActive}
                  >
                    <Icon name="play" />
                    Start SHADOW
                  </button>
                  <button
                    className="button danger-button"
                    onClick={() => setModal("stop")}
                    disabled={busy}
                  >
                    <Icon name="stop" />
                    Stop entries
                  </button>
                </div>
              </Panel>
            </div>
          )}
          {view === "live" && (
            <div className="dashboard-grid">
              <Panel
                title={
                  snapshot.live?.armed
                    ? snapshot.live?.buy_enabled
                      ? "Signer armed · each BUY re-checks all seven gates"
                      : "Signer armed · entries unavailable"
                    : "Real trading is disarmed"
                }
                eyebrow="REAL CAPITAL BOUNDARY"
                className="span-12 live-panel"
                action={
                  <Badge status={snapshot.live?.armed ? "WARNING" : "DISARMED"}>
                    {snapshot.live?.armed ? "ARMED" : "DISARMED"}
                  </Badge>
                }
              >
                <div className="live-intro">
                  <span className="live-shield">
                    <Icon name="shield" />
                  </span>
                  <div>
                    <h3>
                      {snapshot.live?.book?.started
                        ? `LIVE book: ${snapshot.live.book.open?.length ?? 0} open · ${snapshot.live.book.closed_trades ?? 0} closed · realized ${signed(snapshot.live.book.realized_sol, 6)} SOL`
                        : "No LIVE trades yet."}
                    </h3>
                    <p>
                      {snapshot.live?.reason ||
                        "Every BUY is re-checked by the armed signer against all seven gates immediately before signing."}{" "}
                      This terminal can inspect readiness and disarm LIVE.
                      Arming remains CLI-only.
                    </p>
                    <span className="subtle-chip">
                      Configured capital allocation:{" "}
                      {snapshot.live?.max_capital_sol ?? "not available"} SOL
                    </span>{" "}
                    {snapshot.live?.book?.started && (
                      <span className="subtle-chip">
                        Equity {snapshot.live.book.equity_sol} SOL · cash{" "}
                        {snapshot.live.book.cash_sol} SOL
                      </span>
                    )}{" "}
                    {snapshot.live?.book?.halted && (
                      <span className="subtle-chip">
                        Entries halted: {words(snapshot.live.book.halted)}
                      </span>
                    )}{" "}
                    {(snapshot.live?.book?.pending_requests ?? 0) > 0 && (
                      <span className="subtle-chip">
                        {snapshot.live?.book?.pending_requests} signer
                        request(s) awaiting their recorded outcome
                      </span>
                    )}
                  </div>
                  <button
                    className="button danger-button"
                    disabled={busy || !snapshot.live?.armed}
                    onClick={() => void runAction("disarm")}
                  >
                    <Icon name="shield" />
                    Disarm LIVE
                  </button>
                </div>
                {snapshot.live?.book?.open?.length ? (
                  <div className="table-scroll">
                    <table>
                      <thead>
                        <tr>
                          <th>Token</th>
                          <th>Strategy</th>
                          <th>State</th>
                          <th className="numeric">Ticket SOL</th>
                          <th className="numeric">Executable value SOL</th>
                          <th>Opened</th>
                        </tr>
                      </thead>
                      <tbody>
                        {snapshot.live.book.open.map((row) => (
                          <tr key={row.id}>
                            <td>
                              <strong>{short(row.mint)}</strong>
                            </td>
                            <td>{row.strategy}</td>
                            <td>
                              {words(row.state)}
                              {row.exit_reason && (
                                <small> · {words(row.exit_reason)}</small>
                              )}
                            </td>
                            <td className="numeric num">{row.ticket_sol}</td>
                            <td className="numeric num">
                              {row.value_sol ?? "not marked yet"}
                            </td>
                            <td>
                              {row.opened_at.slice(0, 19).replace("T", " ")}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : null}
                <div className="live-gates">
                  {snapshot.live?.gates?.length ? (
                    snapshot.live.gates.map((gate) => (
                      <div className="live-gate" key={gate.name}>
                        <span
                          className={`gate-icon ${gate.ok ? "positive" : "warning"}`}
                        >
                          <Icon name={gate.ok ? "check" : "shield"} />
                        </span>
                        <div>
                          <strong>{words(gate.name)}</strong>
                          <p>
                            {gate.reason ||
                              gate.detail ||
                              (gate.ok
                                ? "Requirement satisfied"
                                : "Requirement not yet satisfied")}
                          </p>
                        </div>
                        <Badge status={gate.ok ? "PASS" : "PENDING"}>
                          {gate.ok ? "PASS" : "BLOCKED"}
                        </Badge>
                      </div>
                    ))
                  ) : (
                    <Empty
                      icon="shield"
                      compact
                      title="No gate report available"
                      detail="Unavailable readiness evidence cannot authorize real trading."
                    />
                  )}
                </div>
              </Panel>
            </div>
          )}
          <footer className="desk-footer">
            <span>
              <span className="footer-dot" />
              LOCAL FIRST <span className="footer-divider">/</span> NO TRADE IS
              A VALID DECISION
            </span>
            <span>
              Hold position. Strike on evidence.
              <span className="footer-divider">/</span>
              <button onClick={() => setModal("help")}>
                Keyboard <kbd>?</kbd>
              </button>
            </span>
          </footer>
        </main>
      </div>
      <DetailDrawer detail={detail} onClose={() => setDetail(null)} />
      <ActionDialog
        modal={modal}
        onClose={() => setModal(null)}
        onAction={runAction}
        busy={busy}
      />
    </div>
  );
}
function Counts({
  values,
  empty,
  plain = false,
}: {
  values?: Record<string, number>;
  empty: string;
  plain?: boolean;
}) {
  const rows = Object.entries(values ?? {})
    .sort((a, b) => b[1] - a[1])
    .slice(0, 100);
  return rows.length ? (
    <dl className="count-list">
      {rows.map(([key, value]) => (
        <div key={key}>
          <dt>{plain ? key : words(key)}</dt>
          <dd className="num">{value.toLocaleString()}</dd>
        </div>
      ))}
    </dl>
  ) : (
    <p className="panel-note pad">{empty}</p>
  );
}
function Workers({ workers = [] }: { workers?: Snapshot["workers"] }) {
  return workers.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Worker</th>
            <th>Status</th>
            <th>PID</th>
            <th>Heartbeat age</th>
            <th>Supervision</th>
          </tr>
        </thead>
        <tbody>
          {workers.map((w) => (
            <tr key={w.name}>
              <td>
                <strong>{words(w.name)}</strong>
              </td>
              <td>
                <Badge status={w.active ? "ACTIVE" : "INACTIVE"}>
                  {w.active ? "ACTIVE" : "INACTIVE"}
                </Badge>
              </td>
              <td className="num">{w.pid ?? "—"}</td>
              <td className="num">
                {w.age_seconds === undefined || w.age_seconds === null
                  ? "—"
                  : `${fmt(w.age_seconds, 1)}s`}
              </td>
              <td className="secondary">
                {w.managed ? "Console managed" : "External / observed"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty
      icon="terminal"
      compact
      title="No workers recorded"
      detail="Start a SHADOW or PAPER session to see worker supervision and heartbeats."
    />
  );
}
function StrategyTable({
  data,
  onDetail,
}: {
  data: Snapshot;
  onDetail: (detail: Detail) => void;
}) {
  const reports = data.paper?.by_strategy ?? {};
  const matrices = data.confidence?.matrices ?? [];
  const names = Array.from(
    new Set([...Object.keys(reports), ...matrices.map((m) => m.strategy_id)]),
  );
  return names.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Strategy</th>
            <th>Evidence readiness</th>
            <th className="numeric">Closed trades</th>
            <th className="numeric">Conservative P&amp;L</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {names.map((name) => {
            const report = reports[name];
            const matrix = matrices.find((m) => m.strategy_id === name);
            return (
              <tr key={name}>
                <td>
                  <button
                    className="text-button"
                    onClick={() =>
                      onDetail({
                        kind: "strategy",
                        title: words(name),
                        endpoint: `/api/strategies/${encodeURIComponent(name)}`,
                      })
                    }
                  >
                    {words(name)}
                  </button>
                </td>
                <td>
                  <Badge status={matrix?.readiness}>
                    {words(matrix?.readiness || "INSUFFICIENT")}
                  </Badge>
                </td>
                <td className="numeric num">
                  {report?.trades ?? matrix?.trades ?? 0}
                </td>
                <td className="numeric num">{signed(report?.pnl_sol)} SOL</td>
                <td>
                  <button
                    className="icon-button"
                    aria-label={`Open ${words(name)} strategy`}
                    onClick={() =>
                      onDetail({
                        kind: "strategy",
                        title: words(name),
                        endpoint: `/api/strategies/${encodeURIComponent(name)}`,
                      })
                    }
                  >
                    <Icon name="chevron" />
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty
      icon="sliders"
      title="Evidence is still being collected"
      detail="Strategies appear here when a confidence matrix or closed PAPER result has been recorded. Start SHADOW to observe decisions."
    />
  );
}

function Jobs({ jobs = [] }: { jobs?: Snapshot["jobs"] }) {
  return jobs.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Task</th>
            <th>Status</th>
            <th>PID</th>
            <th>Started · local time</th>
            <th>Exit code</th>
          </tr>
        </thead>
        <tbody>
          {jobs
            .slice()
            .reverse()
            .map((j) => (
              <tr key={`${j.name}:${j.pid}:${j.started_at}`}>
                <td>
                  <strong>{words(j.name)}</strong>
                </td>
                <td>
                  <Badge
                    status={
                      j.returncode === null
                        ? "ACTIVE"
                        : j.returncode === 0
                          ? "PASS"
                          : "FAIL"
                    }
                  >
                    {j.returncode === null
                      ? "RUNNING"
                      : j.returncode === 0
                        ? "FINISHED"
                        : "FAILED"}
                  </Badge>
                </td>
                <td className="num">{j.pid ?? "—"}</td>
                <td className="num secondary">{time(j.started_at, true)}</td>
                <td className="num">{j.returncode ?? "—"}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty
      icon="terminal"
      compact
      title="No console-managed jobs"
      detail="Sessions and confidence reports started here appear with their process status. External workers appear above."
    />
  );
}
