"use strict";
// The stock page's company sections, Robinhood-style: about, statistics, news, ratings, earnings, financials,
// short interest, insider activity, dividends, related lists and peers. Every section names its source; a missing
// value shows as a dash, never an estimate. Bars and dots are HTML so they stay crisp at any width.
const KestrelStockPage = (() => {
  const MINUS = "−";
  const sign = (x) => (x > 0 ? "+" : x < 0 ? MINUS : "");
  const fmt = {
    compact(n, digits = 2) {
      if (n == null || !Number.isFinite(n)) return "—";
      const s = new Intl.NumberFormat("en-US", {
        notation: "compact",
        maximumFractionDigits: digits,
      }).format(Math.abs(n));
      return (n < 0 ? MINUS : "") + s;
    },
    usd(n, digits = 2) {
      if (n == null || !Number.isFinite(n)) return "—";
      return (
        (n < 0 ? MINUS : "") +
        "$" +
        Math.abs(n).toLocaleString("en-US", {
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
        })
      );
    },
    usdCompact(n) {
      if (n == null || !Number.isFinite(n)) return "—";
      return (n < 0 ? MINUS : "") + "$" + fmt.compact(Math.abs(n));
    },
    signedUsd(n) {
      return n == null ? "—" : sign(n) + fmt.usd(Math.abs(n));
    },
    pct(x, digits = 2) {
      return x == null || !Number.isFinite(x)
        ? "—"
        : (x < 0 ? MINUS : "") + Math.abs(100 * x).toFixed(digits) + "%";
    },
    signedPct(x, digits = 2) {
      return x == null
        ? "—"
        : sign(x) + Math.abs(100 * x).toFixed(digits) + "%";
    },
    num(n, digits = 2) {
      return n == null || !Number.isFinite(n)
        ? "—"
        : (n < 0 ? MINUS : "") +
            Math.abs(n).toLocaleString("en-US", {
              maximumFractionDigits: digits,
            });
    },
    date(iso, year = true) {
      if (!iso) return "—";
      return new Date(iso.slice(0, 10) + "T12:00:00Z").toLocaleDateString(
        "en-US",
        {
          month: "short",
          day: "numeric",
          ...(year ? { year: "numeric" } : {}),
          timeZone: "UTC",
        },
      );
    },
    period(iso) {
      const d = new Date(iso.slice(0, 10) + "T12:00:00Z");
      return `${d.toLocaleDateString("en-US", { month: "short", timeZone: "UTC" })} ’${String(d.getUTCFullYear()).slice(2)}`;
    },
    ago(iso) {
      const text = fmt.age(iso);
      return /^[A-Z]/.test(text) ? `on ${text}` : `${text} ago`;
    },
    age(iso) {
      const minutes = (Date.now() - Date.parse(iso)) / 60000;
      if (!Number.isFinite(minutes)) return "";
      if (minutes < 60) return `${Math.max(1, Math.round(minutes))}m`;
      if (minutes < 1440) return `${Math.round(minutes / 60)}h`;
      if (minutes < 10080) return `${Math.round(minutes / 1440)}d`;
      return fmt.date(iso, false);
    },
  };
  const tone = (x) => (x == null || x === 0 ? "" : x > 0 ? "up" : "down");

  function section(id, title, caption) {
    const box = el("section", undefined, "sp-section"),
      head = el("div", undefined, "sp-head");
    box.id = `sp-${id}`;
    box.setAttribute("aria-labelledby", `sp-${id}-title`);
    const h = el("h2", title);
    h.id = `sp-${id}-title`;
    head.append(h);
    box.append(head);
    if (caption) box.append(el("p", caption, "sp-caption"));
    return box;
  }
  function source(text) {
    return el("p", text, "sp-source");
  }
  function external(label, href) {
    if (typeof href !== "string" || !/^https?:\/\//.test(href)) return null;
    const a = link(`${label} ↗`, href);
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    return a;
  }
  function stats(entries, cls = "sp-stats") {
    const grid = el("dl", undefined, cls);
    for (const [label, value, note] of entries) {
      const item = el("div", undefined, "sp-stat");
      const dd = el("dd");
      if (value instanceof Node) dd.append(value);
      else dd.textContent = value ?? "—";
      item.append(el("dt", label), dd);
      if (note) item.title = note;
      grid.append(item);
    }
    return grid;
  }

  // ── charts ──────────────────────────────────────────────────────────────────────────────
  function niceTicks(lo, hi, count = 4) {
    if (lo === hi) hi = lo + 1;
    const raw = (hi - lo) / count,
      power = 10 ** Math.floor(Math.log10(raw)),
      step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw);
    const start = Math.floor(lo / step) * step,
      ticks = [];
    for (let t = start; t <= hi + step * 0.001; t += step)
      ticks.push(Math.abs(t) < step * 1e-9 ? 0 : t);
    if (ticks.at(-1) < hi) ticks.push(ticks.at(-1) + step);
    return ticks;
  }
  function tooltipFor(root) {
    const tip = el("div", undefined, "viz-tip");
    tip.hidden = true;
    tip.setAttribute("role", "tooltip");
    root.append(tip);
    return (anchor, lines) => {
      if (!lines) {
        tip.hidden = true;
        return;
      }
      tip.replaceChildren(
        ...lines.map(([label, value, cls], i) => {
          const row = el("div", undefined, i ? "viz-tip-row" : "viz-tip-head");
          if (i === 0) row.textContent = label;
          else {
            const key = el("span", label);
            if (cls) key.className = `key ${cls}`;
            row.append(key, el("strong", value));
          }
          return row;
        }),
      );
      tip.hidden = false;
      const box = root.getBoundingClientRect(),
        at = anchor.getBoundingClientRect(),
        x = at.left - box.left + at.width / 2,
        w = tip.offsetWidth;
      tip.style.left = `${Math.max(0, Math.min(box.width - w, x - w / 2))}px`;
      tip.style.top = `${Math.max(0, at.top - box.top - tip.offsetHeight - 10)}px`;
    };
  }
  // Grouped columns around a zero baseline. series: [{key, label, cls, slot}], rows: [{label, values, tip}]
  function columns(
    rows,
    series,
    format,
    { height = 200, dots = [], zero = true } = {},
  ) {
    const values = rows.flatMap((r) =>
      [...series, ...dots].map((s) => r.values[s.key]).filter((v) => v != null),
    );
    const root = el("div", undefined, "viz");
    if (!values.length) return root;
    const low = Math.min(...values),
      high = Math.max(...values),
      pad = zero ? 0 : (high - low) * 0.25 || Math.abs(high) * 0.1 || 1,
      ticks = zero
        ? niceTicks(Math.min(0, low), Math.max(0, high))
        : niceTicks(low - pad, high + pad),
      lo = ticks[0],
      hi = ticks.at(-1),
      pos = (v) => ((v - lo) / (hi - lo)) * 100;
    const area = el("div", undefined, "viz-area"),
      groups = el("div", undefined, "viz-groups"),
      axis = el("div", undefined, "viz-x");
    area.style.height = `${height}px`;
    for (const t of ticks) {
      const rule = el("div", undefined, t === 0 ? "viz-rule zero" : "viz-rule");
      rule.style.bottom = `${pos(t)}%`;
      rule.append(el("span", format(t)));
      area.append(rule);
    }
    const show = tooltipFor(root);
    const slots = series.length
      ? Math.max(...series.map((s) => s.slot ?? 0)) + 1
      : 0;
    for (const row of rows) {
      const group = el("div", undefined, "viz-group");
      group.tabIndex = 0;
      group.setAttribute(
        "aria-label",
        row.tip.map(([a, b]) => (b ? `${a} ${b}` : a)).join(", "),
      );
      const holders = Array.from({ length: slots }, () => {
        const slot = el("div", undefined, "viz-slot");
        group.append(slot);
        return slot;
      });
      for (const s of series) {
        const v = row.values[s.key];
        if (v == null || v === 0) continue;
        const bar = el(
          "span",
          undefined,
          `viz-bar ${s.cls}${v < 0 ? " neg" : ""}`,
        );
        bar.style.bottom = `${pos(Math.min(0, v))}%`;
        bar.style.height = `${Math.max(0.6, Math.abs(pos(v) - pos(0)))}%`;
        holders[s.slot ?? 0].append(bar);
      }
      for (const d of dots) {
        const v = row.values[d.key];
        if (v == null) continue;
        const dot = el("span", undefined, `viz-dot ${d.cls(row)}`);
        dot.style.bottom = `${pos(v)}%`;
        group.append(dot);
      }
      group.onpointerenter = group.onfocus = () => show(group, row.tip);
      group.onpointerleave = group.onblur = () => show(group, null);
      groups.append(group);
      axis.append(el("span", row.label));
    }
    area.append(groups);
    root.append(area, axis);
    return root;
  }
  function legend(items) {
    const box = el("div", undefined, "viz-legend");
    for (const [label, cls, shape = "bar"] of items) {
      const item = el("span");
      item.append(el("i", undefined, `swatch ${shape} ${cls}`), label);
      box.append(item);
    }
    return box;
  }
  function svgEl(tag, attrs = {}) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    return n;
  }
  function spark(values, cls = "") {
    const svg = svgEl("svg", {
      viewBox: "0 0 100 32",
      preserveAspectRatio: "none",
      class: `sp-spark ${cls}`,
      "aria-hidden": "true",
    });
    const xs = values.filter((v) => v != null);
    if (xs.length < 2) return svg;
    const lo = Math.min(...xs),
      hi = Math.max(...xs),
      span = hi - lo || 1;
    svg.append(
      svgEl("polyline", {
        points: xs
          .map(
            (v, i) =>
              `${((i / (xs.length - 1)) * 100).toFixed(2)},${(30 - ((v - lo) / span) * 28).toFixed(2)}`,
          )
          .join(" "),
      }),
    );
    return svg;
  }

  // ── sections ────────────────────────────────────────────────────────────────────────────
  function about(profile, data, state) {
    const info = profile.about || {},
      box = section("about", "About");
    const description = info.description;
    if (description?.text) {
      const text = el("p", description.text, "sp-about-text");
      const long = description.text.length > 360;
      if (long && !state.aboutOpen) text.classList.add("clamped");
      box.append(text);
      if (long) {
        const more = el(
          "button",
          state.aboutOpen ? "Show less" : "Show more",
          "sp-more",
        );
        more.onclick = () => {
          state.aboutOpen = !state.aboutOpen;
          text.classList.toggle("clamped", !state.aboutOpen);
          more.textContent = state.aboutOpen ? "Show less" : "Show more";
        };
        box.append(more);
      }
    } else
      box.append(
        el(
          "p",
          data.listing?.etf
            ? "A fund description appears once company details load."
            : "The company description loads in the background from Massive, Wikipedia or the annual report.",
          "sp-caption",
        ),
      );
    const facts = (info.facts || []).map((f) => [
      f.label,
      typeof f.value === "number"
        ? fmt.num(f.value, 0)
        : /^\d{4}-\d{2}-\d{2}$/.test(f.value)
          ? fmt.date(f.value)
          : f.value,
      `${f.source}${f.note ? ` · ${f.note}` : ""}`,
    ]);
    if (facts.length) box.append(stats(facts, "sp-stats sp-facts"));
    const links = el("div", undefined, "sp-links");
    for (const a of [
      external("Website", info.website),
      external("Wikipedia", info.wikipedia),
      external(
        `Annual report${info.filing?.date ? ` · ${fmt.date(info.filing.date)}` : ""}`,
        info.filing?.url,
      ),
    ])
      if (a) links.append(a);
    if (links.children.length) box.append(links);
    if (description?.source)
      box.append(
        source(
          `Description: ${description.source}. Facts name their source on hover.`,
        ),
      );
    return box;
  }

  function averageVolume(bars) {
    const recent = bars
      .slice(-30)
      .map((b) => b.volume)
      .filter((v) => v > 0);
    return recent.length >= 10
      ? recent.reduce((a, b) => a + b, 0) / recent.length
      : null;
  }

  function keyStats(profile, data) {
    const d = data.analysis || {},
      p = d.price || {},
      f = d.fundamentals || {},
      q = data.quote && !data.quote.stale ? data.quote : null,
      qd = profile.quote_details || {},
      last = data.bars.at(-1),
      si = profile.short_interest?.latest,
      div = profile.dividends;
    const etf = !!data.listing?.etf;
    const box = section(
      "stats",
      etf ? "ETF price & risk statistics" : "Key statistics",
    );
    const bidAsk = (price, size) =>
      price == null
        ? "—"
        : `${money(price)}${size ? ` × ${fmt.num(size, 0)}` : ""}`;
    const avg = qd.avg_volume_10d ?? averageVolume(data.bars);
    const borrow =
      qd.shortable === false
        ? "Not shortable"
        : qd.hard_to_borrow === true
          ? `Hard to borrow${qd.borrow_rate != null ? ` · ${fmt.num(qd.borrow_rate)}%` : ""}`
          : qd.hard_to_borrow === false
            ? "Easy to borrow"
            : null;
    const common = [
      ["Volume", fmt.compact(q?.volume ?? qd.volume ?? last?.volume)],
      [
        qd.avg_volume_10d != null ? "Average volume (10D)" : "Average volume",
        fmt.compact(avg),
        qd.avg_volume_10d != null ? "Schwab" : "Last 30 stored sessions",
      ],
      ["Open", money(qd.open ?? last?.open)],
      ["Today's high", money(qd.high ?? last?.high)],
      ["Today's low", money(qd.low ?? last?.low)],
      ["52 Week high", money(qd.high_52w ?? p.high_52w)],
      ["52 Week low", money(qd.low_52w ?? p.low_52w)],
      ["Bid", bidAsk(qd.bid ?? q?.bid, qd.bid_size)],
      ["Ask", bidAsk(qd.ask ?? q?.ask, qd.ask_size)],
    ];
    const entries = etf
      ? [
          ...common,
          ["1-month return", fmt.signedPct(p.ret_21d)],
          ["3-month return", fmt.signedPct(p.ret_63d)],
          ["Volatility (20D, annualized)", fmt.pct(p.vol_20d)],
          ["50-day average", money(p.sma50)],
          ["200-day average", money(p.sma200)],
          [
            "Relative volume",
            p.rel_volume == null ? "—" : `${fmt.num(p.rel_volume)}×`,
          ],
        ]
      : [
          ["Market cap", fmt.usdCompact(profile.market_cap)],
          [
            "Price-earnings ratio",
            fmt.num(f.pe ?? qd.pe),
            f.pe != null ? "SEC filings, trailing twelve months" : "Schwab",
          ],
          [
            "Dividend yield",
            div?.yield != null ? fmt.pct(div.yield) : "—",
            div?.yield != null ? "Last 12 months of dividends ÷ price" : "",
          ],
          ...common,
          ["Shares outstanding", fmt.compact(profile.shares_outstanding)],
          ["Beta (1 year)", fmt.num(p.beta_1y)],
          [
            "Short interest",
            si?.share_of_outstanding != null
              ? `${fmt.pct(si.share_of_outstanding)} of shares`
              : si
                ? fmt.compact(si.shares)
                : "—",
            si ? `FINRA settlement ${fmt.date(si.settlement)}` : "",
          ],
          ...(borrow ? [["Borrow", borrow, "Schwab reference data"]] : []),
        ];
    box.append(stats(entries));
    const sections = qd.sections || [];
    box.append(
      source(
        q
          ? `Live values: Schwab${sections.length ? ` (${sections.join(", ")})` : ""}. Daily values: stored closes.`
          : "Stored daily closes; connect Schwab in Settings for live quote statistics.",
      ),
    );
    return box;
  }

  function relatedLists(profile) {
    if (!profile.lists?.length) return null;
    const box = section(
      "lists",
      "Related lists",
      "KESTREL research lists this stock is on today.",
    );
    const row = el("div", undefined, "sp-chips");
    for (const item of profile.lists) {
      const chip = link("", `#lists/${encodeURIComponent(item.id)}`);
      chip.className = "chip";
      chip.append(el("span", item.title), el("small", `#${item.rank}`));
      if (item.reason) chip.title = item.reason;
      row.append(chip);
    }
    box.append(row);
    return box;
  }

  function news(profile, symbol) {
    const box = section("news", "News");
    const items = profile.news || [];
    const state = profile.sources?.news;
    if (!items.length) {
      box.append(
        el(
          "p",
          state?.error ||
            "Headlines load in the background from Massive and refresh every half hour while you view this stock.",
          "sp-caption",
        ),
      );
      return box;
    }
    const list = el("ol", undefined, "sp-news");
    for (const n of items) {
      const item = el("li"),
        meta = el("div", undefined, "sp-news-meta");
      meta.append(
        el("strong", n.source || "News"),
        el("span", fmt.age(n.published)),
      );
      const mood = (n.insights || []).find((i) => i.ticker === symbol);
      if (mood) {
        const chip = el(
          "span",
          mood.sentiment[0].toUpperCase() + mood.sentiment.slice(1),
          `sp-mood ${mood.sentiment}`,
        );
        if (mood.reasoning) chip.title = mood.reasoning;
        meta.append(chip);
      }
      const headline = external(n.title, n.url);
      if (!headline) continue;
      headline.className = "sp-news-title";
      item.append(meta, headline);
      if (n.summary) item.append(el("p", n.summary, "sp-news-summary"));
      list.append(item);
    }
    box.append(
      list,
      source(
        `Massive news${state?.updated_at ? ` · updated ${fmt.ago(state.updated_at)}` : ""}. Sentiment is the publisher feed's per-ticker label, not a KESTREL signal.`,
      ),
    );
    return box;
  }

  function finnhubPrompt(text) {
    const p = el("p", undefined, "sp-caption");
    p.append(
      text + " ",
      link("Add a free Finnhub key in Settings →", "#settings"),
    );
    return p;
  }

  function ratings(profile) {
    const r = profile.ratings,
      box = section("ratings", "Analyst ratings");
    if (!r) {
      box.append(
        profile.finnhub_configured
          ? el(
              "p",
              profile.sources?.ratings?.error ||
                "No analyst recommendations are reported for this stock yet.",
              "sp-caption",
            )
          : finnhubPrompt(
              "Buy, hold and sell counts from Wall Street analysts need an optional free key.",
            ),
      );
      return box;
    }
    const body = el("div", undefined, "sp-ratings"),
      hero = el("div", undefined, "sp-ratings-hero");
    hero.append(
      el("strong", `${Math.round((100 * r.buy) / r.total)}%`),
      el("span", `of ${r.total} analysts say buy`),
    );
    const bars = el("div", undefined, "sp-meters");
    for (const [label, count, cls] of [
      ["Buy", r.buy, "up"],
      ["Hold", r.hold, "neutral"],
      ["Sell", r.sell, "down"],
    ]) {
      const row = el("div", undefined, "sp-meter"),
        track = el("div", undefined, "track"),
        fill = el("span", undefined, cls);
      fill.style.width = `${(100 * count) / r.total}%`;
      track.append(fill);
      row.append(
        el("span", label),
        track,
        el("strong", `${Math.round((100 * count) / r.total)}%`),
      );
      bars.append(row);
    }
    body.append(hero, bars);
    box.append(
      body,
      source(
        `${r.source} · ${fmt.date(r.period)}. Opinions, not KESTREL's committee view.`,
      ),
    );
    return box;
  }

  function earnings(profile) {
    const e = profile.earnings,
      box = section("earnings", "Earnings");
    if (!e) {
      box.append(
        profile.finnhub_configured
          ? el(
              "p",
              "No earnings history is stored for this stock.",
              "sp-caption",
            )
          : finnhubPrompt(
              "Reported earnings per share appear once filings are processed; estimates need an optional free key.",
            ),
      );
      return box;
    }
    const estimated = e.history.some((h) => h.estimate != null);
    if (e.history.length) {
      const rows = e.history.map((h) => {
        const surprise =
          h.actual != null && h.estimate != null ? h.actual - h.estimate : null;
        return {
          label: fmt.period(h.period),
          values: { actual: h.actual, estimate: h.estimate },
          beat: surprise == null ? null : surprise >= 0,
          tip: [
            [`Quarter ended ${fmt.date(h.period)}`],
            ["Actual EPS", fmt.usd(h.actual), "dot actual"],
            ...(h.estimate != null
              ? [
                  ["Estimate", fmt.usd(h.estimate), "dot estimate"],
                  [
                    surprise >= 0 ? "Beat by" : "Missed by",
                    `${fmt.usd(Math.abs(surprise))}${h.surprise_pct != null ? ` (${fmt.num(Math.abs(h.surprise_pct), 1)}%)` : ""}`,
                  ],
                ]
              : []),
            ...(h.derived ? [["Fourth quarter", "year minus 9 months"]] : []),
          ],
        };
      });
      box.append(
        columns(rows, [], (v) => fmt.usd(v), {
          height: 160,
          zero: false,
          dots: [
            { key: "estimate", cls: () => "estimate" },
            {
              key: "actual",
              cls: (row) =>
                row.beat == null
                  ? "actual"
                  : row.beat
                    ? "actual up"
                    : "actual down",
            },
          ],
        }),
        legend(
          estimated
            ? [
                ["Estimated EPS", "estimate", "dot"],
                ["Actual · beat", "up", "dot"],
                ["Actual · missed", "down", "dot"],
              ]
            : [["Reported diluted EPS", "actual", "dot"]],
        ),
      );
    }
    if (e.next) {
      const next = el("div", undefined, "sp-next");
      next.append(
        el("span", "Next report"),
        el(
          "strong",
          `${fmt.date(e.next.date)}${e.next.when ? ` · ${e.next.when}` : ""}`,
        ),
        ...(e.next.eps_estimate != null
          ? [el("span", `EPS estimate ${fmt.usd(e.next.eps_estimate)}`)]
          : []),
      );
      box.append(next);
    }
    box.append(source(e.source || "Finnhub earnings calendar"));
    return box;
  }

  function financials(profile, state) {
    const fin = profile.financials;
    if (!fin) return null;
    const box = section("financials", "Financials"),
      head = box.querySelector(".sp-head"),
      toggle = el("div", undefined, "segmented"),
      chartHost = el("div");
    for (const [id, label] of [
      ["quarterly", "Quarterly"],
      ["annual", "Annual"],
    ]) {
      const b = el("button", label);
      b.setAttribute("aria-pressed", String(state.financials === id));
      b.onclick = () => {
        state.financials = id;
        for (const x of toggle.children)
          x.setAttribute("aria-pressed", String(x === b));
        draw();
      };
      toggle.append(b);
    }
    head.append(toggle);
    const series = [
      { key: "revenue", label: "Revenue", cls: "ramp-1", slot: 0 },
      { key: "gross_profit", label: "Gross profit", cls: "ramp-2", slot: 1 },
      { key: "net_income", label: "Net income", cls: "ramp-3", slot: 2 },
    ];
    function draw() {
      const rows = (fin[state.financials] || []).map((r) => ({
        label:
          state.financials === "annual"
            ? `FY${r.end.slice(0, 4)}`
            : fmt.period(r.end),
        values: r,
        margin: r.net_margin,
        tip: [
          [
            `${state.financials === "annual" ? "Fiscal year" : "Quarter"} ended ${fmt.date(r.end)}`,
          ],
          ["Revenue", fmt.usdCompact(r.revenue), "bar ramp-1"],
          ["Gross profit", fmt.usdCompact(r.gross_profit), "bar ramp-2"],
          ["Net income", fmt.usdCompact(r.net_income), "bar ramp-3"],
          ["Net margin", fmt.pct(r.net_margin, 1)],
          ...(r.eps != null ? [["Diluted EPS", fmt.usd(r.eps)]] : []),
          ...(r.derived?.length
            ? [["Fourth quarter", "year minus 9 months"]]
            : []),
        ],
      }));
      const margins = el("div", undefined, "viz-x sp-margins");
      for (const r of rows)
        margins.append(
          el(
            "span",
            r.margin == null ? "—" : fmt.pct(r.margin, 0),
            r.margin < 0 ? "down" : "",
          ),
        );
      chartHost.replaceChildren(
        columns(rows, series, (v) => fmt.usdCompact(v), { height: 200 }),
        el("div", "Net margin", "sp-margin-label"),
        margins,
        legend(series.map((s) => [s.label, s.cls])),
      );
    }
    draw();
    box.append(chartHost, source(`${fin.source}. ${fin.note}`));
    return box;
  }

  function shortInterest(profile) {
    const s = profile.short_interest;
    if (!s) return null;
    const box = section("short", "Short interest"),
      latest = s.latest;
    if (latest) {
      const changeNode = el(
        "span",
        latest.change == null
          ? "—"
          : `${fmt.signedPct(latest.change, 1)} vs prior`,
        tone(latest.change),
      );
      box.append(
        stats(
          [
            ["Shares sold short", fmt.compact(latest.shares)],
            [
              "Share of shares outstanding",
              latest.share_of_outstanding == null
                ? "—"
                : fmt.pct(latest.share_of_outstanding),
            ],
            ["Days to cover", fmt.num(latest.days_to_cover, 1)],
            ["Change", changeNode],
          ],
          "sp-stats sp-tiles",
        ),
      );
    }
    if (s.series.length > 1)
      box.append(
        columns(
          s.series.map((r) => ({
            label: fmt.date(r.settlement, false),
            values: { shares: r.shares },
            tip: [
              [`Settlement ${fmt.date(r.settlement)}`],
              ["Shares short", fmt.compact(r.shares), "bar series-1"],
              [
                "Of shares outstanding",
                r.share_of_outstanding == null
                  ? "—"
                  : fmt.pct(r.share_of_outstanding),
              ],
              ["Days to cover", fmt.num(r.days_to_cover, 1)],
            ],
          })),
          [{ key: "shares", label: "Shares short", cls: "series-1" }],
          (v) => fmt.compact(v, 1),
          { height: 150 },
        ),
      );
    if (s.daily_volume.length > 1) {
      const shares = s.daily_volume.map((d) => d.short_share),
        avg = shares.reduce((a, b) => a + b, 0) / shares.length,
        row = el("div", undefined, "sp-inline-spark");
      row.append(
        el(
          "span",
          `Short-sale share of daily volume · last ${shares.length} sessions`,
        ),
        spark(shares, "series-1"),
        el("strong", `avg ${fmt.pct(avg, 0)}`),
      );
      box.append(row);
    }
    box.append(source(`${s.source}. ${s.note}`));
    return box;
  }

  function insiders(profile) {
    const ins = profile.insiders;
    if (!ins) return null;
    const box = section(
      "insiders",
      "Insider activity",
      "Open-market buying and selling by officers, directors and 10% owners over the last twelve months.",
    );
    const sold = el("span", fmt.usdCompact(ins.sold), ins.sold ? "down" : "");
    box.append(
      stats(
        [
          [
            "Bought",
            el("span", fmt.usdCompact(ins.bought), ins.bought ? "up" : ""),
          ],
          ["Sold", sold],
          ["Planned (10b5-1) sales", fmt.usdCompact(ins.planned_sold)],
          ["Buyers · sellers", `${ins.buyers} · ${ins.sellers}`],
        ],
        "sp-stats sp-tiles",
      ),
    );
    if (ins.months.some((m) => m.bought || m.sold))
      box.append(
        columns(
          ins.months.map((m) => ({
            label: new Date(m.month + "-15T12:00:00Z").toLocaleDateString(
              "en-US",
              { month: "short", timeZone: "UTC" },
            ),
            values: { bought: m.bought || null, sold: m.sold ? -m.sold : null },
            tip: [
              [
                new Date(m.month + "-15T12:00:00Z").toLocaleDateString(
                  "en-US",
                  {
                    month: "long",
                    year: "numeric",
                    timeZone: "UTC",
                  },
                ),
              ],
              ["Bought", fmt.usdCompact(m.bought), "bar up"],
              ["Sold", fmt.usdCompact(m.sold), "bar down"],
              ...(m.planned_sold
                ? [["of which planned", fmt.usdCompact(m.planned_sold)]]
                : []),
            ],
          })),
          [
            { key: "bought", label: "Bought", cls: "up" },
            { key: "sold", label: "Sold", cls: "down" },
          ],
          (v) => fmt.usdCompact(Math.abs(v)),
          { height: 150 },
        ),
        legend([
          ["Bought", "up"],
          ["Sold", "down"],
        ]),
      );
    if (ins.recent.length) {
      const rows = ins.recent.map((t) => {
        const action = el(
          "span",
          t.action + (t.planned ? " · planned" : ""),
          t.code === "P" ? "up" : t.code === "S" ? "down" : "",
        );
        return [
          fmt.date(t.day),
          el("span", t.name, "sp-person"),
          t.role,
          action,
          fmt.compact(t.shares),
          t.price ? money(t.price) : "—",
          t.value ? fmt.usdCompact(t.value) : "—",
        ];
      });
      const details = el("details", undefined, "sp-trades");
      details.append(
        el("summary", `Recent Form 4 transactions (${rows.length})`),
        table(
          [
            "Date",
            "Insider",
            "Role",
            "Transaction",
            "Shares",
            "Price",
            "Value",
          ],
          rows,
        ),
      );
      box.append(details);
    }
    box.append(source(`${ins.source}. ${ins.note}`));
    return box;
  }

  function dividends(profile) {
    const d = profile.dividends;
    if (!d) return null;
    const box = section("dividends", "Dividends");
    box.append(
      stats(
        [
          ["Dividend yield", fmt.pct(d.yield)],
          ["Paid in the last 12 months", fmt.usd(d.trailing_12m, 2)],
          ["Last ex-dividend date", fmt.date(d.last?.ex_date)],
          ["Last pay date", fmt.date(d.last?.pay_date)],
        ],
        "sp-stats sp-tiles",
      ),
    );
    if (d.next)
      box.append(
        el(
          "p",
          `Next: ${fmt.usd(d.next.cash, 4).replace(/0{1,2}$/, "")} per share, ex-dividend ${fmt.date(d.next.ex_date)}${d.next.pay_date ? `, paid ${fmt.date(d.next.pay_date)}` : ""}.`,
          "sp-next-line",
        ),
      );
    box.append(source(`${d.source}. ${d.note}`));
    return box;
  }

  function peers(profile) {
    if (!profile.peers?.length) return null;
    const box = section(
      "peers",
      "Sector peers",
      "Same sector, closest in size, with KESTREL's latest committee score.",
    );
    const grid = el("div", undefined, "sp-peers");
    for (const p of profile.peers) {
      const card = link("", `#stock/${encodeURIComponent(p.symbol)}`);
      card.className = "sp-peer";
      const top = el("div", undefined, "sp-peer-top");
      top.append(
        el("strong", p.symbol),
        el("span", fmt.num(p.score, 0), "score"),
      );
      const dir = (p.spark?.at(-1) ?? 0) >= (p.spark?.[0] ?? 0) ? "up" : "down";
      card.append(
        top,
        el("span", p.name, "sp-peer-name"),
        spark(p.spark || [], dir),
        el("span", money(p.price), "sp-peer-price"),
        el("span", fmt.signedPct(p.change), tone(p.change)),
      );
      card.title = `${p.name}: KESTREL committee score ${fmt.num(p.score, 0)}`;
      grid.append(card);
    }
    box.append(grid);
    return box;
  }

  function scoreHistory(profile) {
    const points = (profile.score_history || []).filter((p) => p.score != null);
    if (points.length < 2) return null;
    const wrap = el("div", undefined, "sp-score-history"),
      svg = svgEl("svg", {
        viewBox: "0 0 100 40",
        preserveAspectRatio: "none",
        role: "img",
        "aria-label": `Committee score history: ${points[0].score.toFixed(0)} on ${points[0].day}, ${points.at(-1).score.toFixed(0)} on ${points.at(-1).day}`,
      });
    const y = (v) => 40 - (v / 100) * 40;
    svg.append(
      svgEl("rect", {
        x: 0,
        width: 100,
        y: y(58),
        height: y(42) - y(58),
        class: "band",
      }),
      svgEl("polyline", {
        points: points
          .map(
            (p, i) =>
              `${((i / (points.length - 1)) * 100).toFixed(2)},${y(p.score).toFixed(2)}`,
          )
          .join(" "),
      }),
    );
    wrap.append(
      el("div", "Score history", "sp-mini-title"),
      svg,
      el(
        "small",
        `${fmt.date(points[0].day, false)} → ${fmt.date(points.at(-1).day, false)} · neutral band 42–58`,
      ),
    );
    return wrap;
  }

  function sources(profile) {
    const details = el("details", undefined, "sp-sources");
    details.append(el("summary", "Company data sources"));
    const list = el("ul");
    for (const [, s] of Object.entries(profile.sources || {})) {
      if (s.error === "not configured") continue;
      list.append(
        el(
          "li",
          `${s.label}: ${s.updated_at ? `updated ${fmt.ago(s.updated_at)}` : "waiting"}${s.error ? ` · ${s.error}` : ""}`,
        ),
      );
    }
    const sections = profile.quote_details?.sections;
    list.append(
      el(
        "li",
        sections?.length
          ? `Schwab quote sections received: ${sections.join(", ")}`
          : "Schwab quote statistics: not received yet (connect Schwab in Settings)",
      ),
    );
    details.append(list);
    return details;
  }

  return {
    fmt,
    sign,
    about,
    keyStats,
    relatedLists,
    news,
    ratings,
    earnings,
    financials,
    shortInterest,
    insiders,
    dividends,
    peers,
    scoreHistory,
    sources,
    spark,
  };
})();
