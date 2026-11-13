"use strict";
// The Learner page (#learner): the Learner PAPER account against its random twin, SPY and the rule-based option
// strategies; each trade with the decision behind it; the do / don't findings; results by market regime; and the
// model versions with their held-out and forward results. Everything comes from GET /api/stocks/learner/account,
// which the worker saved; a page load computes nothing.
const KestrelLearner = (() => {
  const pct = (x, digits = 0) =>
    x === null || x === undefined
      ? "—"
      : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(100 * x).toFixed(digits)}%`;
  const range = (b) =>
    b
      ? `${pct(b.mean, 1)} (90% range ${pct(b.low, 1)} to ${pct(b.high, 1)}, ${b.days} days)`
      : "—";
  const SERIES = [
    ["learner", "Learner", "var(--series-1)"],
    ["learner_random", "Random twin", "var(--series-2)"],
    ["spy", "SPY (total return)", "var(--series-3)"],
    ["rules", "Rule-based options (average)", "var(--series-4)"],
  ];
  function tile(label, value, sub) {
    const box = el("div", undefined, "risk-tile");
    box.append(el("div", label, "eyebrow"), el("strong", value));
    if (sub) box.append(el("small", sub, "muted"));
    return box;
  }
  // Account value of each series on one axis; a legend names every line (no colour-only meaning).
  function chart(curves) {
    const box = el("section", undefined, "learner-chart");
    box.setAttribute("aria-label", "Account value against the baselines");
    box.append(el("h2", "Account value against the baselines"));
    const drawn = SERIES.filter(([id]) => (curves[id] || []).length > 1);
    if (!drawn.length) {
      box.append(
        el(
          "p",
          "No equity recorded yet: the curves start with the account's first PAPER session.",
          "muted",
        ),
      );
      return box;
    }
    const days = [
        ...new Set(drawn.flatMap(([id]) => curves[id].map((p) => p.day))),
      ].sort(),
      values = drawn.flatMap(([id]) => curves[id].map((p) => p.equity)),
      lo = Math.min(...values),
      hi = Math.max(...values),
      w = 640,
      h = 220,
      pad = 8,
      x = (d) =>
        pad + ((w - 2 * pad) * days.indexOf(d)) / Math.max(1, days.length - 1),
      y = (v) => h - pad - ((h - 2 * pad) * (v - lo)) / Math.max(1e-9, hi - lo);
    const ns = "http://www.w3.org/2000/svg",
      svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("class", "learner-chart-svg");
    svg.setAttribute("role", "img");
    svg.setAttribute(
      "aria-label",
      drawn
        .map(([id, label]) => `${label} ${money(curves[id].at(-1).equity)}`)
        .join("; "),
    );
    for (const [id, , color] of drawn) {
      const path = document.createElementNS(ns, "path");
      path.setAttribute(
        "d",
        curves[id]
          .map(
            (p, i) =>
              `${i ? "L" : "M"}${x(p.day).toFixed(1)},${y(p.equity).toFixed(1)}`,
          )
          .join(""),
      );
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", color);
      path.setAttribute("stroke-width", id === "learner" ? "2.5" : "1.5");
      svg.append(path);
    }
    const legend = el("ul", undefined, "learner-legend");
    for (const [id, label, color] of drawn) {
      const item = el("li");
      const swatch = el("i");
      swatch.style.background = color;
      item.append(
        swatch,
        el("span", `${label}: ${money(curves[id].at(-1).equity)}`),
      );
      legend.append(item);
    }
    box.append(
      svg,
      legend,
      el("p", `${days[0]} to ${days.at(-1)}`, "caption muted"),
    );
    return box;
  }
  function accounts(data) {
    const box = el("section", undefined, "learner-accounts");
    box.setAttribute("aria-label", "The Learner's account and its baselines");
    const me = data.accounts.learner || {},
      twin = data.accounts.learner_random || {},
      vs = data.vs_rules || {};
    const tiles = el("div", undefined, "risk-tiles");
    tiles.append(
      tile(
        "Learner",
        money(me.equity ?? data.policy.capital),
        `${pct(me.return, 1)} since it started · ${me.open_positions ?? 0} open · ${me.trades ?? 0} closed`,
      ),
      tile(
        "Random twin",
        money(twin.equity ?? data.policy.capital),
        `${pct(twin.return, 1)} · same risk, sessions and limits`,
      ),
      tile("SPY over the same days", pct(me.spy_return, 1), "total return"),
      tile(
        "Grade",
        me.grade?.grade ?? "early",
        me.grade?.text ??
          "Graded like every PAPER account once it has enough sessions and trades",
      ),
    );
    box.append(el("h2", "The account"), tiles);
    const rows = [
      [
        "Learner, realised by entry day",
        range(vs.learner),
        `${vs.learner_trades ?? 0} trades`,
      ],
      ["Random twin", range(vs.random), `${vs.random_trades ?? 0} trades`],
      ["Rule-based option strategies", range(vs.rules), ""],
      ["Learner minus Random (paired days)", range(vs.vs_random), ""],
      ["Learner minus the rules", range(vs.vs_rules), ""],
    ];
    const t = table(
      ["Realised return on the money at risk", "Daily mean", "Trades"],
      rows,
    );
    t.classList.add("wrap-cells");
    box.append(
      t,
      el(
        "p",
        `${vs.note || ""} Exploration so far: ${pct(me.explore_share)} of new risk (${me.explore_decisions ?? 0} of ${me.decisions ?? 0} trades); ${Math.round(100 * data.policy.explore_share)}% of each session's budget is kept for it.`,
        "caption muted",
      ),
    );
    return box;
  }
  function decisionText(d) {
    if (!d) return "—";
    const s = d.scores || {};
    const bits = [d.reason];
    if (d.mode !== "random")
      bits.push(
        `Model ${d.model_id}: expected ${pct(s.expected)} (10–90%: ${pct(s.q10)} to ${pct(s.q90)}), uncertainty ±${pct(s.se)} from ${Number(s.evidence || 0).toLocaleString("en-US")} similar labelled trades.`,
      );
    if (d.alternatives?.length)
      bits.push(
        `Also weighed: ${d.alternatives
          .slice(0, 3)
          .map(
            (a) =>
              `${a.symbol} ${a.key.split("|")[0].replaceAll("_", " ")} (${pct(a.expected)})`,
          )
          .join(", ")}.`,
      );
    return bits.join(" ");
  }
  function trades(data) {
    const box = el("section", undefined, "learner-trades");
    box.setAttribute("aria-label", "Trades and the decisions behind them");
    box.append(el("h2", "Trades and why"));
    if (!data.trades.length) {
      box.append(el("p", "No trades yet.", "muted"));
      return box;
    }
    const list = el("ul", undefined, "learner-trade-list");
    for (const t of data.trades) {
      const d = t.decision || {},
        item = el("li", undefined, "learner-trade");
      const head = el("div", undefined, "learner-trade-head");
      head.append(
        el("strong", `${t.symbol} · ${t.title}`),
        el(
          "span",
          `${t.account === "learner" ? "Learner" : "Random twin"} · ${d.mode || ""}`,
          `learner-mode learner-mode-${d.mode || "none"}`,
        ),
      );
      const result =
        t.state === "OPEN"
          ? `Open since ${t.entry_day} · worth ${money(t.value)} · at risk ${money(t.max_loss)} · exit rule: ${d.exit_title || "—"}`
          : `Closed ${t.exit_day}: ${t.exit_reason} · P/L ${money(t.pnl)} (${pct(t.return)} of ${money(t.max_loss)} at risk)`;
      item.append(
        head,
        el(
          "p",
          `${d.dials || ""} · decided ${d.session || "—"} in a ${d.regime || "unknown"} market`,
          "caption muted",
        ),
        el("p", result),
        el("p", decisionText(d), "learner-why"),
      );
      list.append(item);
    }
    box.append(list);
    return box;
  }
  function findings(data) {
    const box = el("section", undefined, "learner-findings");
    box.setAttribute("aria-label", "What it learned to do and not to do");
    const f = data.findings || { items: [] },
      r = f.rules || {};
    box.append(
      el("h2", "What it learned to do / not to do"),
      el(
        "p",
        `Written by the system from held-out data only: a rule is kept when its daily results' 90% range excludes zero over at least ${r.min_days ?? 15} days after a Benjamini-Hochberg correction (${Math.round(100 * (r.fdr ?? 0.1))}%) across every rule tried, and confirmed only on at least ${r.confirm_days ?? 10} later, untouched days. A confirmed "don't" is a hard filter for the Learner's account.`,
        "caption muted",
      ),
    );
    if (!f.items.length) {
      box.append(el("p", "Nothing has cleared the tests yet.", "muted"));
      return box;
    }
    const list = el("ul", undefined, "learner-finding-list");
    for (const item of f.items) {
      const li = el(
        "li",
        undefined,
        `learner-finding learner-finding-${item.kind}`,
      );
      li.append(
        el(
          "span",
          `${item.status}${item.filter ? " · used as a filter" : ""}`,
          `learner-status learner-status-${item.status}`,
        ),
        el("p", item.words),
      );
      if (item.regimes?.length)
        li.append(
          el(
            "p",
            "By regime: " +
              item.regimes
                .map((g) => `${g.regime} ${pct(g.mean)} (${g.days} days)`)
                .join("; "),
            "caption muted",
          ),
        );
      list.append(li);
    }
    box.append(list);
    return box;
  }
  function regimes(data) {
    const box = el("section", undefined, "learner-regimes");
    box.setAttribute("aria-label", "Results by market regime");
    const rule = data.regimes?.rule || {};
    box.append(
      el("h2", "By market regime"),
      el(
        "p",
        `Trend: SPY above or below its 200-day average. Volatility: calm under VIX ${rule.vix_calm ?? 20} with a normal term structure, high at ${rule.vix_high ?? 30} or above (or ${rule.vix_calm ?? 20}+ with an inverted one), elevated between. Credit widening: the high-yield spread up ${rule.hy_widening ?? 0.3} points in 21 days. All read as of the previous close.`,
        "caption muted",
      ),
    );
    const held = data.regimes?.held_out || [];
    if (held.length) {
      const t = table(
        [
          "Regime (model, held out)",
          "Days",
          "Rank correlation",
          "Top tenth vs random",
        ],
        held.map((g) => [
          g.regime,
          String(g.days),
          g.rank_correlation === null ? "—" : g.rank_correlation.toFixed(2),
          range(g.top_minus_random),
        ]),
      );
      t.classList.add("wrap-cells");
      box.append(t);
    }
    const acct = data.by_regime || [];
    if (acct.length) {
      const t = table(
        [
          "Regime (account, closed trades)",
          "Account",
          "Trades",
          "Mean return",
          "Wins",
          "P/L",
        ],
        acct.map((g) => [
          g.regime,
          g.account === "learner" ? "Learner" : "Random twin",
          String(g.trades),
          pct(g.mean_return, 1),
          pct(g.win_rate),
          money(g.pnl),
        ]),
      );
      t.classList.add("wrap-cells");
      box.append(t);
    }
    if (!held.length && !acct.length)
      box.append(el("p", "No results by regime yet.", "muted"));
    return box;
  }
  function models(data) {
    const box = el("section", undefined, "learner-models");
    box.setAttribute("aria-label", "Model versions");
    box.append(el("h2", "Model versions"));
    const promo = data.promotion || [];
    if (promo.length) {
      const rows = promo.map((v) => {
        const f = v.forward || {};
        return [
          `${v.model_code} / policy ${v.policy_version}${v.current ? " (current)" : ""}`,
          v.stage,
          f.verdict
            ? `${f.why}: vs Random ${range(f.vs_random)}; vs the rules ${range(f.vs_rules)}`
            : f.why ||
              `Needs ${data.policy.forward_days} forward entry days once it trades`,
        ];
      });
      const t = table(
        ["Learner version (model code / policy)", "Stage", "Forward test"],
        rows,
      );
      t.classList.add("wrap-cells");
      box.append(t);
    }
    const list = data.models || [];
    if (list.length) {
      const t = table(
        ["Model", "Trained", "Held out", "Gate", "Forward trades"],
        list.map((m) => [
          m.id,
          (m.trained_at || "").slice(0, 10),
          `rank ${m.rank_correlation === null ? "—" : m.rank_correlation.toFixed(2)}; top tenth vs random ${range(m.top_minus_random)}`,
          m.gate.ok ? "passed" : m.gate.why,
          m.forward_trades
            ? `${m.forward_trades} closed, ${pct(m.forward_mean, 1)} average`
            : "none",
        ]),
      );
      t.classList.add("wrap-cells");
      box.append(t);
    } else box.append(el("p", "No model has been trained yet.", "muted"));
    return box;
  }
  function days(data) {
    const box = el("details", undefined, "learner-days");
    box.append(el("summary", "Recent sessions: traded, waited and why"));
    const rows = (data.days || []).map((d) => [
      d.session,
      d.account === "learner" ? "Learner" : "Random twin",
      d.status,
      d.why,
    ]);
    if (rows.length) {
      const t = table(["Session", "Account", "Outcome", "Why"], rows);
      t.classList.add("wrap-cells");
      box.append(t);
    } else box.append(el("p", "No session decided yet.", "muted"));
    return box;
  }
  async function show(ticket) {
    const data = await api("learner/account");
    if (ticket !== generation) return;
    app.className = "learner-page";
    const p = data.policy;
    app.append(
      el("div", "PAPER · RESEARCH ONLY", "eyebrow"),
      el("h1", "Learner"),
      el("p", data.label, "muted"),
    );
    const status = el("section", undefined, "learner-status-box");
    status.setAttribute("role", "status");
    status.append(
      el("strong", data.gate?.ok ? "Trading on PAPER" : "Waiting"),
      el("p", data.gate?.why || ""),
      el(
        "p",
        `The rule: exploit trades whose lower confidence bound is above zero; spend ${Math.round(100 * p.explore_share)}% of each session's ${Math.round(100 * p.session_risk)}% new-risk budget exploring uncertain but promising ones by Thompson sampling. At most ${Math.round(100 * p.trade_risk)}% at risk a trade, ${Math.round(100 * p.total_risk)}% in all, ${Math.round(100 * p.stock_risk)}% a stock, ${Math.round(100 * p.sector_risk)}% a sector, ${p.max_open} positions; defined-risk structures only, sold ones fully collateralised. Started with ${money(p.capital)}.`,
        "caption muted",
      ),
    );
    const nav = el("p", undefined, "caption");
    nav.append(
      link("PAPER accounts →", "#paper"),
      " ",
      link("Learning report →", "#learning"),
    );
    app.append(status);
    const curves = {
      learner: data.accounts.learner?.curve || [],
      learner_random: data.accounts.learner_random?.curve || [],
      spy: data.baselines?.spy || [],
      rules: data.baselines?.rules || [],
    };
    const grid = el("div", undefined, "learner-grid");
    grid.append(accounts(data), chart(curves));
    app.append(
      grid,
      trades(data),
      findings(data),
      regimes(data),
      models(data),
      days(data),
      nav,
    );
    if (data.generated_at)
      app.append(
        el(
          "p",
          `Updated ${new Date(data.generated_at).toLocaleString("en-US", { timeZone: "America/New_York" })} ET`,
          "caption muted",
        ),
      );
  }
  return { show };
})();
