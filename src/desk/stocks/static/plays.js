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
    minItm: "",
    view: "cards",
  };
  try {
    state = {
      ...state,
      ...JSON.parse(localStorage.getItem("kestrel-plays") || "{}"),
    };
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
    box.append(
      el("span", label, "play-chance-label"),
      el("strong", pct(shown)),
      bar,
      el(
        "small",
        `Market ${pct(play.action === "SELL" ? 1 - play.itm_market : play.itm_market)} · History ${play.itm_history === null || play.itm_history === undefined ? "—" : pct(play.action === "SELL" ? 1 - play.itm_history : play.itm_history)} · Profit ${pct(play.profit_kestrel)}`,
      ),
    );
    return box;
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
      el(
        "span",
        `${play.action} ${play.type}`,
        `research-badge ${play.action === "SELL" ? "sell" : "buy"}`,
      ),
    );
    if (play.unusual?.tier)
      head.append(el("span", play.unusual.tier, "research-badge quiet"));
    c.append(head);
    c.append(
      el(
        "p",
        `${money(play.strike)} strike · ${play.expiration} (${play.dte}d) · stock ${money(play.underlying_price)}`,
        "play-contract",
      ),
    );
    const legs = legsLine(play);
    if (legs) c.append(legs);
    c.append(chanceMeter(play));
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
        "Break-even",
        `${money(play.breakeven)} (${signed(100 * (play.breakeven_move ?? 0), 1)}%)`,
      ],
      ["Expected value", `${money(play.expected_value)} per contract`],
      [
        "Bid / ask (spread)",
        `${money(play.bid)} / ${money(play.ask)} (${pct(play.spread_pct, 1)})`,
      ],
      [
        "IV (rank)",
        `${pct(play.iv, 1)}${play.iv_rank === null || play.iv_rank === undefined ? "" : ` (${Math.round(play.iv_rank)})`}`,
      ],
      [
        "Δ Γ Θ ν",
        `${greek(play.delta, 2)} · ${greek(play.gamma)} · ${greek(play.theta, 2)} · ${greek(play.vega, 2)}`,
      ],
      [
        "Volume / OI",
        `${number(play.volume)} / ${number(play.open_interest)}${play.vol_oi ? ` (${play.vol_oi.toFixed(1)}×)` : ""}`,
      ],
      [
        "Touch strike before expiry",
        play.in_the_money ? "Already in the money" : pct(play.touch_kestrel),
      ],
      [
        "1σ move by expiry",
        play.expected_move ? `±${pct(play.expected_move, 1)}` : "—",
      ],
    ])
      metrics.append(el("dt", label), el("dd", value));
    c.append(metrics);
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
        "Open the chain & payoff calculator ↗",
        `#stock/${encodeURIComponent(play.symbol)}`,
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
            `#stock/${encodeURIComponent(p.symbol)}`,
          );
          return a;
        })(),
        `${money(p.strike)} · ${p.expiration} (${p.dte}d)`,
        money(p.underlying_price),
        p.action === "SELL" ? `+${money(p.credit)}` : money(p.cost),
        pct(p.itm_kestrel),
        pct(p.profit_kestrel),
        greek(p.delta, 2),
        greek(p.theta, 2),
        pct(p.iv, 0),
        p.vol_oi ? `${p.vol_oi.toFixed(1)}×` : "—",
        money(p.expected_value),
      ]),
    );
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
    maxCost.placeholder = "Max cost per contract ($)";
    minItm.placeholder = "Min chance ITM (%)";
    maxCost.setAttribute("aria-label", "Maximum cost per contract");
    minItm.setAttribute("aria-label", "Minimum chance in the money, percent");
    maxCost.value = state.maxCost;
    minItm.value = state.minItm;
    maxCost.oninput = () => {
      state.maxCost = maxCost.value;
      remember();
      onchange();
    };
    minItm.oninput = () => {
      state.minItm = minItm.value;
      remember();
      onchange();
    };
    bar.append(kind, action, maxCost, minItm, view);
    return bar;
  }

  function keep(play) {
    const kind =
      play.legs?.[0]?.kind || (play.type.includes("CALL") ? "call" : "put");
    if (state.kind !== "all" && kind !== state.kind) return false;
    if (state.action !== "all" && play.action !== state.action) return false;
    if (
      state.maxCost &&
      (play.action === "SELL" ? play.max_loss : play.cost) >
        Number(state.maxCost)
    )
      return false;
    if (state.minItm) {
      const chance =
        play.action === "SELL"
          ? 1 - (play.itm_kestrel ?? 0)
          : (play.itm_kestrel ?? 0);
      if (100 * chance < Number(state.minItm)) return false;
    }
    return true;
  }

  async function show(ticket, route = "plays") {
    const parts = route.split("/");
    if (parts[1] === "history")
      return KestrelPlaysHistory.history(ticket, parts[2]);
    if (parts[1] === "play" && parts.length === 5)
      return KestrelPlaysHistory.detail(ticket, parts[2], parts[3], parts[4]);
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
      ? `Options session ${data.asof} · ${data.coverage.underlyings} underlyings · ${number(data.coverage.contracts_scored)} contracts scored · ${data.source || "stored chains"} · built ${when(data.generated_at)} · chance model: ${data.calibration.applied ? `calibrated on ${data.calibration.resolved} resolved plays` : `not yet calibrated (${data.calibration.resolved} of ${data.calibration.needed} resolved plays)`}${data.model.ic ? ` · committee tilt from IC ${data.model.ic.toFixed(3)} over ${data.model.ic_months} months` : " · no committee tilt yet (no positive measured IC)"}`
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
    const ids = data.categories.map((c) => c.id);
    if (!ids.includes(state.category)) state.category = ids[0];
    function draw() {
      tabs.replaceChildren();
      for (const cat of data.categories) {
        const b = el(
          "button",
          `${cat.emoji} ${cat.title} (${cat.plays.length})`,
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
      const cat = data.categories.find((c) => c.id === state.category);
      body.replaceChildren();
      const head = el("div", undefined, "section-heading");
      const text = el("div");
      text.append(
        el("h2", `${cat.emoji} ${cat.title}`),
        el("p", cat.blurb, "muted"),
      );
      head.append(text);
      body.append(head, el("p", cat.basis, "muted caption"), record(cat));
      if (cat.followups?.length) body.append(followupTable(cat.followups));
      const shown = cat.plays.filter(keep);
      if (!shown.length) {
        body.append(
          el(
            "p",
            cat.plays.length
              ? "No plays in this category match these filters."
              : cat.empty || "Nothing qualifies in this category today.",
            "screen-empty",
          ),
        );
        return;
      }
      if (state.view === "table") body.append(rowsTable(shown));
      else {
        const grid = el("div", undefined, "setup-grid play-grid");
        for (const p of shown) grid.append(card(p));
        body.append(grid);
      }
    }
    app.append(filters(draw), tabs, body);
    draw();
    app.append(
      el(
        "p",
        "Market = what the option's price implies. History = how often this stock moved that far in that many sessions, at today's volatility. KESTREL = a forecast blending implied and realized volatility, tilted by the committee only as far as its measured record allows, then calibrated against how past plays finished. Unusual activity is detected from quote snapshots (volume, open interest, quotes, changes between snapshots), not from exchange trade prints, so it cannot see trade direction for certain.",
        "muted caption",
      ),
    );
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
  return { show, tracking };
})();
