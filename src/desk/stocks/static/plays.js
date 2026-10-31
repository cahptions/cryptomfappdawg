"use strict";
// Plays: today's option ideas by category, each with what it costs, what it risks, its greeks and three views of
// its chance of finishing in the money. Research only — no order can be placed from this desk.
const KestrelPlays = (() => {
  const pct = (x, digits = 0) =>
    x === null || x === undefined ? "—" : `${(100 * x).toFixed(digits)}%`;
  const signed = (x, digits = 2) =>
    x === null || x === undefined
      ? "—"
      : `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(digits)}`;
  const greek = (x, digits = 3) =>
    x === null || x === undefined ? "—" : Number(x).toFixed(digits);
  const when = (iso) =>
    iso
      ? new Date(iso).toLocaleString("en-US", {
          timeZone: "America/New_York",
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        }) + " ET"
      : "—";
  let state = {
    category: null,
    kind: "all",
    action: "all",
    maxCost: "",
    minCost: "",
    priceFilterVersion: 2,
    minItm: "",
    view: "cards",
  };
  try {
    const saved = JSON.parse(localStorage.getItem("kestrel-plays") || "{}");
    state = {
      ...state,
      ...saved,
    };
    // Remove the old automatic ceiling and legacy unlabeled price fields once. Subsequent explicit choices persist.
    if (saved.priceFilterVersion !== 2)
      Object.assign(state, { maxCost: "", minCost: "", priceFilterVersion: 2 });
  } catch {
    /* private window or blocked storage: defaults are fine */
  }
  function remember() {
    try {
      localStorage.setItem("kestrel-plays", JSON.stringify(state));
    } catch {
      /* not essential */
    }
  }

  function chanceMeter(play) {
    const box = el("div", undefined, "play-chance"),
      value = play.itm_kestrel ?? play.itm_market,
      bar = el("div", undefined, "play-chance-bar"),
      fill = el("span");
    fill.style.width = `${Math.max(2, Math.min(100, 100 * (value ?? 0)))}%`;
    bar.append(fill);
    bar.setAttribute("role", "img");
    bar.setAttribute(
      "aria-label",
      `KESTREL chance in the money ${pct(value)}; market ${pct(play.itm_market)}; history ${pct(play.itm_history)}`,
    );
    const label =
      play.chance_label ||
      (play.action === "SELL"
        ? "Chance it expires worthless"
        : "Chance in the money");
    const shown =
      play.action === "SELL" && value !== null && value !== undefined
        ? 1 - value
        : value;
    fill.style.width = `${Math.max(0, Math.min(100, 100 * (shown ?? 0)))}%`;
    bar.setAttribute("aria-label", `${label} at expiry: ${pct(shown)}`);
    box.append(
      el("span", `${label} at expiry`, "play-chance-label"),
      el("strong", pct(shown)),
      bar,
      el(
        "small",
        `Market ${pct(play.action === "SELL" ? 1 - play.itm_market : play.itm_market)} · History ${play.itm_history === null || play.itm_history === undefined ? "—" : pct(play.action === "SELL" ? 1 - play.itm_history : play.itm_history)} · Profit ${pct(play.profit_kestrel)}`,
      ),
    );
    return box;
  }

  // A spread names both strikes ("$49.00 / $53.00 strikes"); one contract or a straddle names one.
  function strikeList(play) {
    const all = [...new Set((play.legs || []).map((l) => l.strike))].sort(
      (a, b) => a - b,
    );
    return all.length > 1 ? all.map(money).join(" / ") : money(play.strike);
  }
  const strikeLabel = (play) =>
    `${strikeList(play)} ${strikeList(play).includes(" / ") ? "strikes" : "strike"}`;

  // One contract: its own bid/ask. Several legs: the net price at the quotes (natural) and at the mids, the range a
  // limit order works in; the widest leg's spread says how much that range costs.
  function netPrice(play) {
    if (play.legs.length === 1)
      return [
        "Bid / ask (spread)",
        `${money(play.bid)} / ${money(play.ask)} (${pct(play.spread_pct, 1)})`,
      ];
    const sign = (l) =>
        (l.side === "buy" ? 1 : -1) * (play.action === "SELL" ? -1 : 1),
      mid = play.legs.reduce((sum, l) => sum + sign(l) * l.mid, 0);
    return [
      `Net ${play.action === "SELL" ? "credit" : "debit"} (natural / mid)`,
      `${money(play.entry)} / ${money(mid)} (widest leg ${pct(play.spread_pct, 1)})`,
    ];
  }

  // Confidence: five plain checks; the meter is how many pass, and each check says why (never a disguised chance).
  function confidenceMeter(play) {
    const c = play.confidence;
    if (!c) return null;
    const box = el("details", undefined, `play-confidence tier-${c.score}`),
      summary = el("summary"),
      bar = el("span", undefined, "play-confidence-bar");
    for (let i = 0; i < c.of; i += 1)
      bar.append(el("i", undefined, i < c.score ? "on" : ""));
    bar.setAttribute("role", "img");
    bar.setAttribute(
      "aria-label",
      `Confidence ${c.score} of ${c.of}: ${c.label}`,
    );
    summary.append(
      el("span", "Confidence", "play-confidence-label"),
      bar,
      el("strong", `${c.label} · ${c.score}/${c.of}`),
    );
    const list = el("ul");
    for (const check of c.checks)
      list.append(
        el(
          "li",
          `${check.ok ? "✓" : "✗"} ${check.title}: ${check.detail}`,
          check.ok ? "ok" : "no",
        ),
      );
    box.append(summary, list);
    return box;
  }

  const day = (iso) =>
    new Date(`${iso}T12:00:00`).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });

  // The plan to leave: take profit and stop as P/L and as stock prices for a move today (or the day time alone gets
  // there if the stock stays put), and a date to be out by.
  function exitBlock(play, plan = play.exit_plan) {
    if (!plan) return null;
    const box = el("div", undefined, "play-exit"),
      rows = el("dl", undefined, "play-exit-rows");
    const line = (level) => {
      if (!level) return null;
      const stock = level.stock || {},
        prices = [
          stock.above != null ? `${play.symbol} ≥ ${money(stock.above)}` : null,
          stock.below != null ? `${play.symbol} ≤ ${money(stock.below)}` : null,
        ].filter(Boolean);
      return [
        `${signed(100 * level.return, 0)}% (${level.pnl >= 0 ? "+" : "−"}${money(Math.abs(level.pnl))})`,
        prices.length ? `${prices.join(" or ")} today` : null,
        level.by_time
          ? `${prices.length ? "or " : ""}by ${day(level.by_time)} if it stays put`
          : null,
      ]
        .filter(Boolean)
        .join(" · ");
    };
    for (const [label, value] of [
      ["Take profit", line(plan.take_profit) || "Hold to expiry"],
      ["Stop", line(plan.stop) || "None: sized so losing it all is fine"],
      ["Leave by", plan.leave_by ? day(plan.leave_by) : "Hold to expiry"],
    ])
      rows.append(el("dt", label), el("dd", value));
    const more = el("details");
    more.append(
      el("summary", "Adding more, and why these levels"),
      el("p", plan.add),
      el("p", plan.why, "muted"),
      el("p", plan.note, "caption muted"),
    );
    box.append(
      el(
        "strong",
        plan.source === "learned" ? "Exit plan · learned" : "Exit plan",
      ),
      rows,
      more,
    );
    return box;
  }

  // With a bankroll saved: how many of this play fit its per-play limit.
  const LEVELS = { careful: 0.05, balanced: 0.1, bold: 0.2 };
  function yourSize(play) {
    const b = typeof snapshot !== "undefined" ? snapshot?.ui?.bankroll : null;
    if (!b || !play.max_loss) return null;
    const cap = b.amount * LEVELS[b.level],
      qty = Math.floor(cap / play.max_loss),
      title = `For your ${money(b.amount)} (${b.level})`;
    return el(
      "p",
      qty >= 1
        ? `${title}: up to ${qty} · worst case ${money(qty * play.max_loss)} (${pct((qty * play.max_loss) / b.amount, 1)})`
        : `${title}: above your ${money(cap)} per-play limit`,
      `play-size ${qty >= 1 ? "" : "over"}`,
    );
  }

  function legsLine(play) {
    if (!play.legs || play.legs.length < 2) return null;
    return el(
      "p",
      play.legs
        .map(
          (l) =>
            `${l.side === "sell" ? "Sell" : "Buy"} ${money(l.strike)} ${l.kind} (${l.expiration})`,
        )
        .join(" · "),
      "play-legs",
    );
  }

  function card(play) {
    const c = el("article", undefined, "setup-card play-card"),
      head = el("div", undefined, "setup-heading play-heading");
    head.append(
      link(play.symbol, `#stock/${encodeURIComponent(play.symbol)}`),
      el("span", money(play.underlying_price), "play-underlying"),
      KestrelPlayWorkflow.trackButton(play),
    );
    if (play.unusual?.tier)
      head.append(el("span", play.unusual.tier, "research-badge quiet"));
    c.append(head);
    c.append(
      el(
        "p",
        `${strikeLabel(play)} · ${play.expiration} (${play.dte}d)`,
        "play-contract",
      ),
    );
    const legs = legsLine(play);
    if (legs) c.append(legs);
    c.append(chanceMeter(play));
    for (const part of [confidenceMeter(play), yourSize(play), exitBlock(play)])
      if (part) c.append(part);
    c.append(KestrelPlayWorkflow.targets(play));
    const metrics = el("dl", undefined, "setup-metrics play-metrics");
    const cost =
      play.action === "SELL"
        ? [
            "Credit / max loss",
            `${money(play.credit)} / ${money(play.max_loss)}`,
          ]
        : ["Cost / max loss", `${money(play.cost)} / ${money(play.max_loss)}`];
    for (const [label, value] of [
      cost,
      ["Max gain", play.max_gain === null ? "Unlimited" : money(play.max_gain)],
      [
        play.breakevens ? "Break-evens" : "Break-even",
        play.breakevens
          ? `${play.breakevens.map(money).join(" / ")} (±${(100 * (play.breakeven_move ?? 0)).toFixed(1)}%)`
          : `${money(play.breakeven)} (${signed(100 * (play.breakeven_move ?? 0), 1)}%)`,
      ],
      ["Expected value", `${money(play.expected_value)} per contract`],
      [...netPrice(play)],
      [
        "IV (rank)",
        `${pct(play.iv, 1)}${play.iv_rank === null || play.iv_rank === undefined ? "" : ` (${Math.round(play.iv_rank)})`}`,
      ],
      ["Δ Delta", greek(play.delta, 2)],
      ["Γ Gamma", greek(play.gamma)],
      ["Θ Theta / day", greek(play.theta, 2)],
      ["ν Vega / IV point", greek(play.vega, 2)],
      [
        "Volume / OI",
        `${number(play.volume)} / ${number(play.open_interest)}${play.vol_oi ? ` (${play.vol_oi.toFixed(1)}×)` : ""}`,
      ],
      [
        play.breakevens
          ? "Touch a break-even before expiry"
          : "Touch strike before expiry",
        play.in_the_money ? "Already in the money" : pct(play.touch_kestrel),
      ],
      [
        "1σ move by expiry",
        play.expected_move ? `±${pct(play.expected_move, 1)}` : "—",
      ],
    ])
      metrics.append(el("dt", label), el("dd", value));
    c.append(metrics);
    c.append(KestrelPlayWorkflow.greeks(play));
    if (play.unusual) {
      const u = el("div", undefined, "play-unusual");
      u.append(
        el("strong", `Unusual score ${Math.round(play.unusual.score)}`),
        el("p", play.unusual.factors.join(" · "), "muted"),
      );
      if (play.unusual.confirmation)
        u.append(el("p", play.unusual.confirmation, "play-confirm"));
      c.append(u);
    }
    const why = el("ul", undefined, "play-reasons");
    for (const r of play.reasons || []) why.append(el("li", r));
    for (const f of play.flags || []) why.append(el("li", f, "play-flag"));
    c.append(why);
    c.append(
      el(
        "small",
        `${play.source} · quoted ${when(play.quoted_at)} · liquidity ${play.liquidity}`,
      ),
      link(
        "Open play, P/L & exact-leg calculator ↗",
        play.idea_id
          ? `#plays/idea/${play.idea_id}`
          : `#stock/${encodeURIComponent(play.symbol)}`,
      ),
    );
    return c;
  }

  function rowsTable(plays) {
    return table(
      [
        "",
        "Play",
        "Strike · expiry",
        "Stock",
        "Cost/credit",
        "Chance ITM",
        "Profit",
        "Confidence",
        "Δ",
        "Θ/day",
        "IV",
        "Vol/OI",
        "EV",
      ],
      plays.map((p) => [
        String(p.rank),
        (() => {
          const a = link(
            `${p.symbol} ${p.action} ${p.type}`,
            p.idea_id
              ? `#plays/idea/${p.idea_id}`
              : `#stock/${encodeURIComponent(p.symbol)}`,
          );
          return a;
        })(),
        `${strikeList(p)} · ${p.expiration} (${p.dte}d)`,
        money(p.underlying_price),
        p.action === "SELL" ? `+${money(p.credit)}` : money(p.cost),
        pct(p.itm_kestrel),
        pct(p.profit_kestrel),
        p.confidence ? `${p.confidence.score}/${p.confidence.of}` : "—",
        greek(p.delta, 2),
        greek(p.theta, 2),
        pct(p.iv, 0),
        p.vol_oi ? `${p.vol_oi.toFixed(1)}×` : "—",
        money(p.expected_value),
      ]),
    );
  }

  const LEVEL_LABELS = [
    ["careful", "Careful · 5% a play, 20% in all"],
    ["balanced", "Balanced · 10% a play, 35% in all"],
    ["bold", "Bold · 20% a play, 60% in all"],
  ];
  function bankrollView(body) {
    const saved = (typeof snapshot !== "undefined" &&
      snapshot?.ui?.bankroll) || { amount: 1000, level: "balanced" };
    const head = el("div", undefined, "section-heading"),
      text = el("div");
    text.append(
      el("h2", "💼 My bankroll"),
      el(
        "p",
        "Enter what you've set aside for options. KESTREL picks today's plays from every category and sizes them so no single play can lose more than a set share of it and the total at risk stays capped. Research picks for PAPER, not orders.",
        "muted",
      ),
    );
    head.append(text);
    const form = el("form", undefined, "bankroll-form"),
      amount = el("input"),
      level = el("select"),
      go = el("button", "Pick my plays", "primary"),
      out = el("div", undefined, "bankroll-out");
    amount.type = "number";
    amount.min = "1";
    amount.step = "any";
    amount.inputMode = "decimal";
    amount.value = String(saved.amount);
    amount.setAttribute("aria-label", "Bankroll in dollars");
    for (const [id, label] of LEVEL_LABELS) {
      const o = el("option", label);
      o.value = id;
      level.append(o);
    }
    level.value = saved.level;
    level.setAttribute("aria-label", "Risk level");
    form.append(el("span", "$", "bankroll-prefix"), amount, level, go);
    body.append(head, form, out);
    async function run() {
      const value = Number(amount.value);
      if (!(value >= 1)) {
        out.replaceChildren(
          el("p", "Enter an amount of at least $1.", "screen-empty"),
        );
        return;
      }
      go.disabled = true;
      out.replaceChildren(el("p", "Choosing plays…", "muted"));
      const choice = { amount: value, level: level.value };
      api("ui", { bankroll: choice })
        .then((ui) => {
          if (typeof snapshot !== "undefined" && snapshot) snapshot.ui = ui;
        })
        .catch(() => {});
      try {
        draw(
          await api(
            `plays/bankroll?amount=${encodeURIComponent(value)}&level=${level.value}`,
          ),
        );
      } catch (error) {
        out.replaceChildren(el("p", error.message, "screen-empty"));
      } finally {
        go.disabled = false;
      }
    }
    function draw(data) {
      const s = data.summary,
        stats = el("dl", undefined, "bankroll-summary");
      for (const [label, value] of [
        ["Plays", String(s.plays)],
        [
          "At risk",
          `${money(s.risk)} of ${money(data.amount)} (${pct(s.share, 0)})`,
        ],
        ["Cash tied up", money(s.cash)],
        ["If every take-profit hits", `+${money(s.at_take_profit)}`],
        ["If every stop hits", money(s.at_stop)],
        [
          "KESTREL expects",
          `${s.expected_value >= 0 ? "+" : "−"}${money(Math.abs(s.expected_value))}`,
        ],
      ]) {
        const pair = el("div");
        pair.append(el("dt", label), el("dd", value));
        stats.append(pair);
      }
      const parts = [stats];
      if (data.message) parts.push(el("p", data.message, "bankroll-note"));
      const grid = el("div", undefined, "setup-grid play-grid");
      for (const pick of data.picks) {
        const wrap = el("div", undefined, "bankroll-pick"),
          verb = pick.play.action === "SELL" ? "Sell" : "Buy";
        wrap.append(
          el(
            "p",
            `${verb} ${pick.quantity} × · risk ${money(pick.risk)} (${pct(pick.share, 1)}) · cash ${money(pick.cash)} · ${pick.play.category_title}`,
            "bankroll-pick-head",
          ),
          card(pick.play),
        );
        grid.append(wrap);
      }
      if (data.picks.length) parts.push(grid);
      parts.push(
        el(
          "p",
          `Limits: no play's worst case above ${money(data.per_play_cap)}; all plays together at most ${money(data.risk_cap)}; one play per stock, two per category, and no direction (bullish, bearish, neutral, big-move) above 60% of the total. Plays need 3 of 5 confidence checks (2 when few qualify), most confident first. Sold plays tie up their worst case as collateral; a cash-secured put ties up the cash to buy the shares.`,
          "caption muted",
        ),
      );
      out.replaceChildren(...parts);
    }
    form.onsubmit = (event) => {
      event.preventDefault();
      run();
    };
    run();
  }

  function record(cat) {
    const box = el("div", undefined, "play-record");
    const r = cat.record;
    box.append(
      el(
        "p",
        r && r.resolved
          ? `Track record: ${r.resolved} past plays resolved · ${pct(r.itm_rate)} finished in the money (KESTREL predicted ${pct(r.predicted_itm)}) · median return at expiry ${pct(r.median_return)}`
          : "Track record: no plays in this category have expired yet. Each day's plays are saved once and graded at expiry.",
        "muted caption",
      ),
    );
    if (cat.paper)
      box.append(
        el(
          "p",
          `PAPER strategy "${cat.paper.title}": ${cat.paper.trades} closed trades · win rate ${pct(cat.paper.win_rate)} · average return ${pct(cat.paper.avg_return)} · ${cat.paper.grade}`,
          "muted caption",
        ),
      );
    return box;
  }

  function followupTable(rows) {
    const box = el("details", undefined, "play-followups");
    box.open = true;
    box.append(
      el(
        "summary",
        `Did the last flags open positions? ${rows[0].session} flags, checked against today's open interest`,
      ),
      table(
        [
          "Contract",
          "Flagged volume",
          "Open interest then → now",
          "Opened",
          "Price since",
          "Verdict",
        ],
        rows.map((r) => [
          (() =>
            link(
              `${r.symbol} ${money(r.strike)} ${r.kind} ${r.expiration}`,
              `#stock/${encodeURIComponent(r.symbol)}`,
            ))(),
          number(r.volume),
          `${number(r.open_interest)} → ${r.open_interest_now === null ? "—" : number(r.open_interest_now)}`,
          r.opened === null ? "—" : pct(r.opened),
          r.price_change === null
            ? "—"
            : `${r.price_change >= 0 ? "+" : ""}${pct(r.price_change, 1)}`,
          r.verdict,
        ]),
      ),
    );
    return box;
  }

  function filters(onchange) {
    const bar = el("div", undefined, "screen-filters play-filters");
    const kind = el("select"),
      action = el("select"),
      maxCost = el("input"),
      minItm = el("input"),
      view = el("select");
    for (const [select, label, choices, key] of [
      [
        kind,
        "Option type",
        [
          ["all", "Calls & puts"],
          ["call", "Calls"],
          ["put", "Puts"],
        ],
        "kind",
      ],
      [
        action,
        "Buy or sell",
        [
          ["all", "Buy & sell"],
          ["BUY", "Buying"],
          ["SELL", "Selling"],
        ],
        "action",
      ],
      [
        view,
        "Layout",
        [
          ["cards", "Cards"],
          ["table", "Table"],
        ],
        "view",
      ],
    ]) {
      select.setAttribute("aria-label", label);
      for (const [id, text] of choices) {
        const o = el("option", text);
        o.value = id;
        select.append(o);
      }
      select.value = state[key];
      select.onchange = () => {
        state[key] = select.value;
        remember();
        onchange();
      };
    }
    maxCost.type = minItm.type = "number";
    maxCost.min = minItm.min = "0";
    minItm.max = "100";
    maxCost.step = "0.01";
    maxCost.placeholder = "No limit";
    minItm.placeholder = "Any chance";
    maxCost.setAttribute("aria-label", "Maximum cost per contract");
    minItm.setAttribute("aria-label", "Minimum chance at expiry, percent");
    maxCost.oninput = () => {
      state.maxCost = maxCost.value;
      state.minCost = "";
      remember();
      onchange();
    };
    minItm.oninput = () => {
      state.minItm = minItm.value;
      remember();
      onchange();
    };
    for (const [input, title] of [
      [kind, "Option type"],
      [action, "Buy or sell"],
      [maxCost, "Max cost / risk ($)"],
      [minItm, "Min chance at expiry (%)"],
      [view, "Layout"],
    ]) {
      const label = el("label", undefined, "play-filter-field");
      label.append(el("span", title), input);
      bar.append(label);
    }
    const clear = el("button", "Clear filters");
    function reset() {
      Object.assign(state, {
        kind: "all",
        action: "all",
        maxCost: "",
        minCost: "",
        minItm: "",
      });
      remember();
      onchange();
    }
    clear.onclick = reset;
    bar.append(clear);
    const budgets = el("div", undefined, "play-budget-presets"),
      presets = [];
    budgets.setAttribute("role", "group");
    budgets.setAttribute("aria-label", "Trade budget");
    budgets.append(el("span", "Optional price filters:"));
    for (const [title, low, high] of [
      ["Up to $100", "", "100"],
      ["$100–$250", "100", "250"],
      ["$250–$500", "250", "500"],
      ["Up to $500", "", "500"],
      ["All prices", "", ""],
    ]) {
      const button = el("button", title);
      button.onclick = () => {
        state.minCost = low;
        state.maxCost = high;
        remember();
        onchange();
      };
      presets.push([button, low, high]);
      budgets.append(button);
    }
    function sync() {
      kind.value = state.kind;
      action.value = state.action;
      maxCost.value = state.maxCost;
      minItm.value = state.minItm;
      view.value = state.view;
      clear.disabled = !filterRules().length;
      for (const [button, low, high] of presets)
        button.setAttribute(
          "aria-pressed",
          String(state.minCost === low && state.maxCost === high),
        );
    }
    sync();
    return { element: bar, budgets, sync, reset };
  }

  const playRisk = (play) =>
    play.action === "SELL" ? play.max_loss : play.cost;
  function filterRules() {
    const rules = [];
    if (state.kind !== "all")
      rules.push({
        label: state.kind === "call" ? "Calls only" : "Puts only",
        accepts: (p) =>
          (p.legs?.[0]?.kind || (p.type.includes("CALL") ? "call" : "put")) ===
          state.kind,
      });
    if (state.action !== "all")
      rules.push({
        label: state.action === "BUY" ? "Buying only" : "Selling only",
        accepts: (p) => p.action === state.action,
      });
    if (state.maxCost !== "")
      rules.push({
        label: `Maximum cost / risk ${money(Number(state.maxCost))}`,
        accepts: (p) =>
          Number.isFinite(playRisk(p)) && playRisk(p) <= Number(state.maxCost),
      });
    if (state.minCost !== "")
      rules.push({
        label: `Cost / risk above ${money(Number(state.minCost))}`,
        accepts: (p) =>
          Number.isFinite(playRisk(p)) && playRisk(p) > Number(state.minCost),
      });
    if (state.minItm !== "")
      rules.push({
        label: `Minimum chance at expiry ${state.minItm}%`,
        accepts: (p) => {
          const itm = p.itm_kestrel ?? p.itm_market;
          return (
            Number.isFinite(itm) &&
            100 * (p.action === "SELL" ? 1 - itm : itm) >= Number(state.minItm)
          );
        },
      });
    return rules;
  }

  function selection(cat, rules) {
    const budget = state.maxCost !== "" || state.minCost !== "",
      pool =
        budget && cat.budget_plays
          ? [
              ...cat.budget_plays,
              ...cat.plays.filter((p) => playRisk(p) > 1000),
            ]
          : cat.plays,
      unique = new Map();
    for (const play of pool) {
      const key =
        play.idea_id || JSON.stringify(play.legs?.map((l) => [l.occ, l.side]));
      if (!unique.has(key)) unique.set(key, play);
    }
    const candidates = [...unique.values()].sort(
      (a, b) =>
        (b.selection_score ?? -b.rank) - (a.selection_score ?? -a.rank) ||
        a.rank - b.rank,
    );
    const matches = candidates.filter((p) => rules.every((r) => r.accepts(p))),
      counts = new Map(),
      shown = [];
    for (const play of matches) {
      const used = counts.get(play.symbol) || 0;
      if (used >= (cat.id === "unusual" ? 2 : 1)) continue;
      counts.set(play.symbol, used + 1);
      shown.push({ ...play, rank: shown.length + 1 });
      if (shown.length >= 12) break;
    }
    return { candidates, matches, shown, budget };
  }

  function priceRanges(cat, rules, select) {
    const section = el("section", undefined, "play-price-ranges"),
      grid = el("div", undefined, "play-price-grid");
    section.append(
      el("h3", "Candidates across price ranges"),
      el(
        "p",
        "Lower-priced alternatives appear alongside the overall ranking. All prices remain eligible; price alone does not improve a play's score.",
        "muted caption",
      ),
    );
    for (const [title, low, high] of [
      ["Up to $100", 0, 100],
      ["$100–$250", 100, 250],
      ["$250–$500", 250, 500],
      ["$500–$1,000", 500, 1000],
    ]) {
      const group = el("article", undefined, "play-price-band"),
        pool = (cat.budget_plays || [])
          .filter(
            (p) =>
              playRisk(p) > low &&
              playRisk(p) <= high &&
              rules.every((r) => r.accepts(p)),
          )
          .sort(
            (a, b) =>
              (b.selection_score ?? -b.rank) - (a.selection_score ?? -a.rank),
          );
      group.append(el("h4", title));
      for (const p of pool.slice(0, 2)) {
        const item = el("div", undefined, "play-price-candidate"),
          detail = p.idea_id
            ? `#plays/idea/${p.idea_id}`
            : `#stock/${encodeURIComponent(p.symbol)}`;
        item.append(
          link(`${p.symbol} · ${p.action} ${p.type}`, detail),
          el(
            "strong",
            `${money(playRisk(p))} ${p.action === "SELL" ? "max risk" : "cost"}`,
          ),
          el("small", `${strikeLabel(p)} · ${p.expiration}`),
          el(
            "small",
            `Profit at expiry ${pct(p.profit_kestrel)} · liquidity ${p.liquidity}`,
          ),
        );
        group.append(item);
      }
      if (!pool.length)
        group.append(
          el(
            "p",
            "No qualifying candidates in this range for the current filters.",
            "caption muted",
          ),
        );
      const view = el("button", `View ${title}`);
      view.disabled = !pool.length;
      view.onclick = () => select(low, high);
      group.append(view);
      grid.append(group);
    }
    section.append(
      grid,
      el(
        "p",
        "These alternatives are tracked automatically in History. Their results are separate from the original category's performance record.",
        "caption muted",
      ),
    );
    return section;
  }

  async function show(ticket, route = "plays") {
    const parts = route.split("/");
    if (parts[1] === "idea")
      return KestrelPlayWorkflow.detail(ticket, parts[2]);
    if (parts[1] === "mine")
      return parts[2]
        ? KestrelPlayWorkflow.detail(ticket, parts[2], true)
        : KestrelPlayWorkflow.mine(ticket);
    if (parts[1] === "history")
      return KestrelPlaysHistory.history(ticket, parts[2]);
    if (parts[1] === "play" && parts.length === 5)
      return KestrelPlaysHistory.detail(ticket, parts[2], parts[3], parts[4]);
    if (parts[1] === "performance")
      return KestrelPlaysHistory.performance(ticket);
    const data = await api("plays");
    if (ticket !== generation) return;
    app.className = "plays-page";
    const hero = el("div", undefined, "explorer-heading"),
      titles = el("div");
    titles.append(
      el("div", "OPTIONS OF THE DAY", "eyebrow"),
      el("h1", "Plays"),
      el(
        "p",
        "Ranked option ideas from the desks, the options data and what past plays and PAPER trades actually did. Every chance is an estimate with its track record beside it. Research, not financial advice.",
        "muted",
      ),
    );
    hero.append(titles);
    app.append(hero, KestrelPlaysHistory.views("board"));
    const strip = el("p", undefined, "muted caption play-strip");
    strip.textContent = data.asof
      ? `Loaded options session ${data.asof} · ${data.coverage.underlyings} underlyings · ${number(data.coverage.contracts_scored)} contracts scored · ${data.source || "stored chains"} · built ${when(data.generated_at)} · chance model: ${data.calibration.applied ? `calibrated on ${data.calibration.resolved} resolved plays` : `not yet calibrated (${data.calibration.resolved} of ${data.calibration.needed} resolved plays)`}${data.model.ic ? ` · committee tilt from IC ${data.model.ic.toFixed(3)} over ${data.model.ic_months} months` : " · no committee tilt yet (no positive measured IC)"}`
      : data.message;
    app.append(strip);
    if (!data.categories?.length) {
      const empty = panel("No plays yet", data.message);
      empty.append(
        link("Check options coverage ↗", "#system"),
        link("Connect an options source ↗", "#settings"),
      );
      app.append(empty);
      return;
    }
    const tabs = el("div", undefined, "play-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "Play categories");
    const body = el("section", undefined, "play-body");
    const controls = filters(draw),
      summary = el("p", undefined, "play-filter-summary");
    summary.setAttribute("role", "status");
    summary.setAttribute("aria-atomic", "true");
    const ids = ["bankroll", ...data.categories.map((c) => c.id)];
    if (!ids.includes(state.category)) state.category = data.categories[0].id;
    function draw() {
      controls.sync();
      const rules = filterRules();
      tabs.replaceChildren();
      const mine = el("button", "💼 My bankroll", "play-tab play-tab-bankroll");
      mine.setAttribute("role", "tab");
      mine.setAttribute("aria-selected", String(state.category === "bankroll"));
      mine.onclick = () => {
        state.category = "bankroll";
        remember();
        draw();
      };
      tabs.append(mine);
      const bank = state.category === "bankroll";
      for (const part of [controls.element, controls.budgets, summary])
        part.style.display = bank ? "none" : "";
      if (bank) {
        body.replaceChildren();
        for (const cat of data.categories) {
          const b = el("button", `${cat.emoji} ${cat.title}`, "play-tab");
          b.setAttribute("role", "tab");
          b.setAttribute("aria-selected", "false");
          b.onclick = () => {
            state.category = cat.id;
            remember();
            draw();
          };
          tabs.append(b);
        }
        bankrollView(body);
        return;
      }
      for (const cat of data.categories) {
        const picked = selection(cat, rules);
        const b = el(
          "button",
          `${cat.emoji} ${cat.title} (${rules.length ? `${picked.shown.length} of ` : ""}${picked.candidates.length})`,
          "play-tab",
        );
        b.setAttribute("role", "tab");
        b.setAttribute("aria-selected", String(cat.id === state.category));
        b.onclick = () => {
          state.category = cat.id;
          remember();
          draw();
        };
        tabs.append(b);
      }
      const cat = data.categories.find((c) => c.id === state.category),
        { candidates, matches, shown, budget } = selection(cat, rules);
      body.replaceChildren();
      const head = el("div", undefined, "section-heading");
      const text = el("div"),
        evidence = el("div", undefined, "play-overall-evidence");
      text.append(
        el("h2", `${cat.emoji} ${cat.title}`),
        el("p", cat.blurb, "muted"),
      );
      if (!budget && cat.label && cat.label.state !== "unproven")
        evidence.append(el("p", cat.label.text, `pf-label ${cat.label.state}`));
      if (!budget && cat.exit)
        evidence.append(
          el(
            "p",
            `Suggested exit (chosen walk-forward over ${cat.exit.test_sessions} unseen sessions, +${pct(cat.exit.mean_improvement, 1)} a play better than holding): ${cat.exit.best_in_sample.rule}.`,
            "caption",
          ),
        );
      if (cat.live_rules)
        text.append(
          el(
            "p",
            `Running a promoted challenger rule (${cat.live_rules}); see Performance for the evidence.`,
            "caption muted",
          ),
        );
      head.append(text);
      body.append(head, el("p", cat.basis, "muted caption"));
      if (budget) {
        const note = el(
          "p",
          data.budget_search
            ? "Budget search considers affordable strikes and expirations before ranking. Category quality gates still apply; budget trades are followed automatically in History. The original category's performance record is separate."
            : "This saved board predates budget search. The next collection pass rebuilds it from saved chains; current results only filter the original shortlist.",
          "caption muted",
        );
        body.append(note);
      }
      if (cat.followups?.length) body.append(followupTable(cat.followups));
      summary.textContent = `Showing ${shown.length} ranked plays in ${cat.title}: ${matches.length} of ${candidates.length} candidates match. ${rules.length ? `${rules.map((r) => r.label).join(" · ")}. Filters are remembered in this browser.` : "No filters applied."} At most 12 shown, ${cat.id === "unusual" ? "two" : "one"} per stock.`;
      if (!budget && cat.budget_plays) {
        body.append(
          priceRanges(cat, rules, (low, high) => {
            state.minCost = low ? String(low) : "";
            state.maxCost = String(high);
            remember();
            draw();
          }),
        );
        body.append(el("h3", "Overall ranking · all prices"));
      }
      if (!budget) body.append(evidence, record(cat));
      if (!shown.length) {
        if (
          !budget &&
          (cat.budget_plays || []).some((p) => rules.every((r) => r.accepts(p)))
        ) {
          body.append(
            el(
              "p",
              "No picks in the overall ranking match these filters. Qualifying alternatives remain in the price ranges above.",
              "screen-empty",
            ),
          );
          return;
        }
        const empty = el("div", undefined, "screen-empty play-filter-empty");
        if (candidates.length) {
          empty.append(
            el("h3", "No plays in this category match these filters."),
            el(
              "p",
              `Loading is complete. ${candidates.length} candidates are hidden by your filters.`,
            ),
          );
          const reasons = el("ul");
          for (const rule of rules) {
            const excluded = candidates.filter((p) => !rule.accepts(p)).length;
            if (excluded)
              reasons.append(
                el(
                  "li",
                  `${rule.label} excludes ${excluded} of ${candidates.length} candidates.`,
                ),
              );
          }
          empty.append(reasons);
          if (rules.length > 1)
            empty.append(
              el(
                "p",
                "A play can be excluded by more than one filter.",
                "caption",
              ),
            );
          if (state.maxCost !== "") {
            const priced = candidates.map(playRisk).filter(Number.isFinite);
            if (priced.length && Math.min(...priced) > Number(state.maxCost))
              empty.append(
                el(
                  "p",
                  `The lowest cost / risk in this category is ${money(Math.min(...priced))} for one contract per leg, including entry fees.`,
                ),
              );
          }
          const reset = el("button", "Clear filters and show the full ranking");
          reset.onclick = controls.reset;
          empty.append(reset);
        } else {
          empty.append(
            el(
              "p",
              budget && cat.plays.length
                ? "No qualifying alternatives were found in the searched price bands for this category. Try another category or view all prices."
                : cat.empty || "Nothing qualifies in this category today.",
            ),
          );
          if (budget && cat.plays.length) {
            const all = el("button", "View all prices");
            all.onclick = controls.reset;
            empty.append(all);
          }
        }
        body.append(empty);
        return;
      }
      if (state.view === "table") body.append(rowsTable(shown));
      else {
        const grid = el("div", undefined, "setup-grid play-grid");
        for (const p of shown) grid.append(card(p));
        body.append(grid);
      }
    }
    // Categories first (what kind of play), then a compact filter bar; the method notes close the page.
    app.append(tabs, controls.element, controls.budgets, summary, body);
    draw();
    const method = el("details", undefined, "play-method");
    method.append(
      el("summary", "How plays are chosen and filtered"),
      el(
        "p",
        `Searching ${number(data.coverage.underlyings)} collected underlyings. Coverage grows as collection rotates; watchlist stocks are prioritized.`,
        "caption muted",
      ),
      el(
        "p",
        "Cost / risk is for one contract per leg, including entry fees: purchase cost for buys; maximum loss for sells. Chance follows the card's expiry event: ITM, a break-even move, or a short option expiring worthless.",
        "caption muted play-filter-help",
      ),
      el(
        "p",
        "Market = what the option's price implies. History = how often this stock moved that far in that many sessions, at today's volatility. KESTREL = a forecast blending implied and realized volatility, tilted by the committee only as far as its measured record allows, then calibrated against how past plays finished. Unusual activity is detected from quote snapshots (volume, open interest, quotes, changes between snapshots), not from exchange trade prints, so it cannot see trade direction for certain.",
        "muted caption",
      ),
    );
    app.append(method);
  }
  // System → Plays tracking: what is followed live, how fresh, what waits, and what it costs.
  function tracking(t) {
    const wrap = el("div", undefined, "plays-tracking");
    if (!t || t.waiting) {
      wrap.append(
        panel("Plays tracking", t?.waiting || "Tracking has not run yet."),
      );
      return wrap;
    }
    const p = t.pass || {},
      f = p.freshness || { buckets: {}, late: [] },
      endpoint =
        {
          working:
            "Schwab option quotes verified: 100 contracts a request, with Schwab's own IV and greeks.",
          no_options:
            "Schwab's quotes endpoint answered without option quotes; tracking uses per-expiry chains instead.",
          refused:
            "Schwab refused option symbols on its quotes endpoint; tracking uses per-expiry chains instead.",
        }[t.endpoint?.state] ||
        "Schwab option quotes not verified yet: run uv run desk stocks doctor --online.",
      mb =
        t.storage?.bytes === null || t.storage?.bytes === undefined
          ? "size not measurable on this SQLite"
          : `${(t.storage.bytes / 1e6).toFixed(1)} MB`,
      rows = t.storage?.rows || {};
    const lines = [
      `${t.market_open ? "Market open" : "Market closed"} · last live pass ${when(p.at)} · ${number(p.tracked ?? 0)} contracts tracked · ${number(p.quoted ?? 0)} quoted that pass, ${number(p.from_collector ?? 0)} more from the research collector.`,
      `${p.requests ?? 0} requests that pass, ${p.requests_last_hour ?? 0} in the last hour (at most ${t.budget.requests_per_pass} a pass; Schwab allows ${t.budget.schwab_limit_per_minute} a minute, shared with research).`,
      endpoint,
      `Today: ${number(t.today?.quotes ?? 0)} quotes of ${number(t.today?.contracts ?? 0)} contracts. Last close folded: ${t.last_close_folded || "none yet"}. Stored: ${number(rows.stk_track_days ?? 0)} daily closes, ${number(rows.stk_track_intraday ?? 0)} intraday days, ${number(rows.stk_play_marks ?? 0)} play marks · ${mb}.`,
    ];
    if (t.repair?.pending)
      lines.push(
        `Repairing ${number(t.repair.pending)} plays: filling a gap from Schwab's stock history (${t.repair.note || "within the request budget"}).`,
      );
    if (p.errors?.length) lines.push(`Last errors: ${p.errors.join(" · ")}`);
    wrap.append(panel("Plays tracking", lines.join(" ")));
    const order = ["≤ 5 min", "≤ 15 min", "≤ 60 min", "older", "never today"];
    if (Object.keys(f.buckets || {}).length)
      wrap.append(
        table(
          ["Quote age", "Contracts"],
          order
            .filter((k) => f.buckets[k])
            .map((k) => [k, number(f.buckets[k])]),
        ),
      );
    if (f.late?.length)
      wrap.append(
        table(
          ["Waiting contract", "Stock", "Schedule", "Last quote"],
          f.late.map((x) => [
            x.occ,
            x.symbol,
            t.priorities?.[x.priority] || x.priority,
            x.minutes === null ? "none today" : `${x.minutes} min ago`,
          ]),
        ),
      );
    return wrap;
  }
  return { show, tracking, card, exitBlock, confidenceMeter };
})();
