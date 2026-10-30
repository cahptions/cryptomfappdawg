"use strict";
// Options profit calculator: a full-screen workspace in the style of the popular calculators (OptionStrat,
// optionsprofitcalculator.com). Strategy templates, a strike rail, a stats strip with the options market's own
// chance of profit, a P/L chart or a price × date table, and price, date, IV and range controls. Every value is
// modelled from the saved quotes after fees; it is not a forecast and nothing here places an order.
const KestrelCalculator = (() => {
  const MINUS = "−";
  const iso = (n) => new Date(n * 86400000).toISOString().slice(0, 10);
  const day = (s) => Math.round(Date.parse(s + "T00:00:00Z") / 86400000);
  const shortDate = (s, weekday = false) =>
    new Date(s + "T12:00:00Z").toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      ...(weekday ? { weekday: "short" } : {}),
      timeZone: "UTC",
    });
  const signedMoney = (v) =>
    Number.isFinite(v)
      ? (v > 0 ? "+" : v < 0 ? MINUS : "") + money(Math.abs(v))
      : "—";
  const compactMoney = (v) => {
    if (!Number.isFinite(v)) return "—";
    const sign = v > 0 ? "+" : v < 0 ? MINUS : "",
      a = Math.abs(v);
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(1)}M`;
    if (a >= 1e4) return `${sign}$${Math.round(a / 1e3)}K`;
    if (a >= 1e3) return `${sign}$${(a / 1e3).toFixed(1)}K`;
    return `${sign}$${Math.round(a)}`;
  };
  const signedPct = (v, digits = 1) =>
    Number.isFinite(v)
      ? (v > 0 ? "+" : v < 0 ? MINUS : "") +
        Math.abs(v * 100).toFixed(digits) +
        "%"
      : "—";

  function field(label, control, cls = "calc-field") {
    const box = el("label", undefined, cls);
    box.append(el("span", label), control);
    control.setAttribute("aria-label", label);
    return box;
  }
  function select(values, current) {
    const node = el("select");
    for (const [value, label] of values) {
      const option = el("option", label);
      option.value = value;
      node.append(option);
    }
    node.value = current;
    return node;
  }
  function numeric(value, min, max, step = "any", type = "number") {
    const n = el("input");
    Object.assign(n, {
      type,
      value: String(value),
      min: String(min),
      max: String(max),
      step: String(step),
    });
    return n;
  }

  // ── strategies ───────────────────────────────────────────────────────────────────────────────
  // Offsets are shares of the underlying price; the nearest saved strikes are used, never invented ones.
  const TEMPLATES = [
    ["Bullish", "Long call", [["buy", "call", 0]]],
    [
      "Bullish",
      "Bull call spread",
      [
        ["buy", "call", 0],
        ["sell", "call", 0.05],
      ],
    ],
    [
      "Bullish",
      "Bull put spread",
      [
        ["buy", "put", -0.07],
        ["sell", "put", -0.02],
      ],
    ],
    ["Bearish", "Long put", [["buy", "put", 0]]],
    [
      "Bearish",
      "Bear put spread",
      [
        ["buy", "put", 0],
        ["sell", "put", -0.05],
      ],
    ],
    [
      "Bearish",
      "Bear call spread",
      [
        ["sell", "call", 0.02],
        ["buy", "call", 0.07],
      ],
    ],
    [
      "Neutral",
      "Iron condor",
      [
        ["buy", "put", -0.1],
        ["sell", "put", -0.05],
        ["sell", "call", 0.05],
        ["buy", "call", 0.1],
      ],
    ],
    [
      "Neutral",
      "Iron butterfly",
      [
        ["buy", "put", -0.05],
        ["sell", "put", 0],
        ["sell", "call", 0],
        ["buy", "call", 0.05],
      ],
    ],
    [
      "Neutral",
      "Long call butterfly",
      [
        ["buy", "call", -0.05],
        ["sell", "call", 0, 2],
        ["buy", "call", 0.05],
      ],
    ],
    [
      "Neutral",
      "Calendar spread",
      [
        ["sell", "call", 0],
        ["buy", "call", 0, 1, "next"],
      ],
    ],
    [
      "Volatility",
      "Long straddle",
      [
        ["buy", "call", 0],
        ["buy", "put", 0],
      ],
    ],
    [
      "Volatility",
      "Long strangle",
      [
        ["buy", "put", -0.05],
        ["buy", "call", 0.05],
      ],
    ],
  ];

  // The legs' structure in plain words (the popular calculators name the strategy you build).
  function strategyName(legs) {
    const ls = legs.map((l) => ({
      buy: l.action === "buy",
      kind: l.quote.kind,
      strike: l.quote.strike,
      qty: l.quantity,
      exp: l.expiration,
    }));
    const one = ls.length === 1,
      sameExp = new Set(ls.map((l) => l.exp)).size === 1;
    if (one) return `${ls[0].buy ? "Long" : "Short"} ${ls[0].kind}`;
    const sorted = [...ls].sort((a, b) => a.strike - b.strike);
    const equalQty = new Set(ls.map((l) => l.qty)).size === 1;
    if (ls.length === 2 && equalQty) {
      const [a, b] = sorted;
      if (a.kind === b.kind && !sameExp && a.buy !== b.buy) {
        const near = ls.find((l) => l.exp === [a.exp, b.exp].sort()[0]);
        if (!near.buy)
          return a.strike === b.strike ? "Calendar spread" : "Diagonal spread";
      }
      if (
        sameExp &&
        a.kind === b.kind &&
        a.buy !== b.buy &&
        a.strike < b.strike
      ) {
        if (a.kind === "call")
          return a.buy ? "Bull call spread" : "Bear call spread";
        return a.buy ? "Bull put spread" : "Bear put spread";
      }
      if (sameExp && a.kind !== b.kind && a.buy === b.buy) {
        const kind = a.strike === b.strike ? "straddle" : "strangle";
        return `${a.buy ? "Long" : "Short"} ${kind}`;
      }
    }
    if (
      ls.length === 3 &&
      sameExp &&
      new Set(ls.map((l) => l.kind)).size === 1
    ) {
      const [a, b, c] = sorted;
      if (
        a.buy &&
        c.buy &&
        !b.buy &&
        a.qty === c.qty &&
        b.qty === 2 * a.qty &&
        Math.abs(b.strike - a.strike - (c.strike - b.strike)) < 1e-6
      )
        return `Long ${a.kind} butterfly`;
    }
    if (ls.length === 4 && sameExp && equalQty) {
      const puts = sorted.filter((l) => l.kind === "put"),
        calls = sorted.filter((l) => l.kind === "call");
      if (
        puts.length === 2 &&
        calls.length === 2 &&
        puts[0].buy &&
        !puts[1].buy &&
        !calls[0].buy &&
        calls[1].buy &&
        puts[1].strike <= calls[0].strike
      )
        return puts[1].strike === calls[0].strike
          ? "Iron butterfly"
          : "Iron condor";
    }
    return `Custom · ${ls.length} legs`;
  }

  // ── visuals ──────────────────────────────────────────────────────────────────────────────────
  function payoffChart(result, { onSelect, basis }) {
    const spot = result.underlying_price,
      band = result.price_range || 0.2,
      lo = spot * (1 - band),
      hi = spot * (1 + band);
    const curve = result.curve.filter((p) => p.price >= lo && p.price <= hi);
    const move = result.chance?.expected_move;
    const chart = KestrelCharts.create({
      id: "calculator",
      className: "payoff-chart",
      xKind: "number",
      baseline: 0,
      label: `Projected P/L · ${result.valuation_date}`,
      value: (p) => p.value,
      valueColor: (p) => (p.value >= 0 ? "var(--up)" : "var(--down)"),
      points: curve.map((p) => ({
        ...p,
        x: p.price,
        value: p.scenario ?? p.expiration,
      })),
      series: [
        {
          id: "scenario",
          label: `On ${shortDate(result.valuation_date)}`,
          signed: true,
          value: (p) => p.scenario,
          color: "var(--accent)",
          className: "payoff-scenario",
        },
        {
          id: "expiration",
          label: "At expiration",
          value: (p) => p.expiration,
          color: "var(--text-2)",
          dashed: true,
          className: "payoff-expiry",
        },
      ],
      format: KestrelCharts.signed,
      xFormat: (p) => money(p.price),
      timeFormat: (p) => `Underlying ${money(p.price)}`,
      changeText: () => "Modelled position value after entry and closing fees",
      tooltipRows: (p) => [
        ["Underlying", `${money(p.price)} (${signedPct(p.price / spot - 1)})`],
        [
          "Scenario P/L",
          `${money(p.scenario)}${basis && Number.isFinite(p.scenario) ? ` · ${signedPct(p.scenario / basis)}` : ""}`,
        ],
        ["Expiration P/L", money(p.expiration)],
      ],
      references: [
        { x: spot, label: "Now" },
        ...result.risk.breakevens
          .filter((x) => x >= lo && x <= hi)
          .map((x) => ({ x, label: "B/E" })),
        ...(move
          ? [
              { x: spot - move, label: "−1σ" },
              { x: spot + move, label: "+1σ" },
            ].filter((r) => r.x >= lo && r.x <= hi)
          : []),
      ],
      coverage:
        "Drag or use arrow keys to explore. Click a price to set the scenario. Accent: the selected date and IV; dashed: the payoff at expiration. ±1σ: the options-implied one-standard-deviation move by expiration.",
      onSelect: (p) => onSelect(p.price, result.valuation_date),
      empty:
        "No inferable IV for these quotes. The payoff at expiration is still exact.",
    });
    const index = curve.findIndex((p) => p.price === result.scenario_price);
    // On a phone the tooltip would cover the curve; it appears on touch instead.
    if (index >= 0 && matchMedia("(min-width: 700px)").matches)
      chart.inspect(index);
    return chart;
  }

  function table(result, { onSelect, units, basis }) {
    const data = result.heatmap,
      spot = result.underlying_price;
    const wrap = el("div", undefined, "calc-table-wrap");
    wrap.setAttribute("role", "region");
    wrap.setAttribute(
      "aria-label",
      "Modelled P/L by underlying price and date",
    );
    wrap.tabIndex = 0;
    const grid = el("div", undefined, "calc-heatmap-grid");
    grid.style.setProperty("--heat-columns", data.dates.length);
    grid.append(el("span", "Price", "calc-heat-corner"));
    for (const d of data.dates) {
      const head = el("span", undefined, "calc-heat-date");
      head.append(
        el("b", d === result.last_expiration ? "Exp" : shortDate(d)),
        el("small", shortDate(d, true).split(",")[0]),
      );
      if (d === result.valuation_date) head.classList.add("selected");
      grid.append(head);
    }
    const values = data.values.flat().filter(Number.isFinite);
    const maximum = Math.max(1, ...values.map(Math.abs));
    let nearest = 0;
    data.prices.forEach((p, i) => {
      if (Math.abs(p - spot) < Math.abs(data.prices[nearest] - spot))
        nearest = i;
    });
    for (let i = data.prices.length - 1; i >= 0; i--) {
      const price = data.prices[i],
        head = el("span", undefined, "calc-heat-price");
      head.append(
        el("b", money(price)),
        el("small", signedPct(price / spot - 1)),
      );
      if (i === nearest) head.classList.add("now");
      if (price === result.scenario_price) head.classList.add("selected");
      grid.append(head);
      data.values[i].forEach((v, j) => {
        const text = !Number.isFinite(v)
          ? "—"
          : units === "%" && basis
            ? signedPct(v / basis, 0)
            : compactMoney(v);
        const b = el(
          "button",
          text,
          `calc-heat-cell ${v > 0 ? "positive" : v < 0 ? "negative" : "flat"}`,
        );
        b.type = "button";
        b.disabled = v == null;
        b.style.setProperty(
          "--heat",
          `${Math.round(8 + 62 * Math.sqrt(Math.abs(v || 0) / maximum))}%`,
        );
        if (
          price === result.scenario_price &&
          data.dates[j] === result.valuation_date
        )
          b.classList.add("selected");
        b.setAttribute(
          "aria-label",
          `${data.dates[j]} · underlying ${money(price)} · modelled P/L ${money(v)}`,
        );
        b.onclick = () => onSelect(price, data.dates[j]);
        grid.append(b);
      });
    }
    wrap.append(grid);
    return wrap;
  }

  // Legs as pills on a price line, with today's price marked: the shape of the position at a glance.
  function rail(legs, spot) {
    const box = el("div", undefined, "calc-rail");
    const strikes = legs.map((l) => l.quote.strike);
    const lo = Math.min(spot, ...strikes),
      hi = Math.max(spot, ...strikes),
      pad = Math.max((hi - lo) * 0.18, spot * 0.03);
    const from = lo - pad,
      to = hi + pad,
      at = (x) => `${((x - from) / (to - from)) * 100}%`;
    const expirations = [...new Set(legs.map((l) => l.expiration))].sort();
    for (const expiration of expirations) {
      const row = el("div", undefined, "calc-rail-row");
      if (expirations.length > 1)
        row.append(el("span", shortDate(expiration), "calc-rail-exp"));
      const track = el("div", undefined, "calc-rail-track");
      const now = el("i", undefined, "calc-rail-now");
      now.style.left = at(spot);
      now.title = `Underlying ${money(spot)}`;
      track.append(now);
      legs.forEach((leg, index) => {
        if (leg.expiration !== expiration) return;
        const pill = el(
          "button",
          `${leg.quantity > 1 ? leg.quantity + "× " : ""}${leg.action === "buy" ? "+" : MINUS}${leg.quote.kind === "call" ? "C" : "P"} ${number(leg.quote.strike)}`,
          `calc-pill ${leg.action}`,
        );
        pill.type = "button";
        pill.style.left = at(leg.quote.strike);
        pill.setAttribute(
          "aria-label",
          `Leg ${index + 1}: ${leg.action} ${leg.quantity} ${leg.quote.kind} ${money(leg.quote.strike)} ${leg.expiration}`,
        );
        pill.onclick = () => {
          const card = document.querySelectorAll(".calc-leg")[index];
          card?.scrollIntoView({ block: "nearest", behavior: "smooth" });
          card?.classList.add("flash");
          setTimeout(() => card?.classList.remove("flash"), 900);
        };
        track.append(pill);
      });
      row.append(track);
      box.append(row);
    }
    const scale = el("div", undefined, "calc-rail-scale");
    scale.append(el("span", money(from)), el("span", money(to)));
    box.append(scale);
    return box;
  }

  function stat(label, value, note, cls = "") {
    const box = el("div", undefined, `calc-stat ${cls}`);
    box.append(el("span", label), el("strong", value));
    if (note) box.append(el("small", note));
    return box;
  }

  // ── the workspace ────────────────────────────────────────────────────────────────────────────
  async function open(symbol, contract, initial, selectedLegs = null) {
    document.querySelector(".options-calculator")?.remove();
    const dialog = el("dialog", undefined, "options-calculator");
    const spot = initial.underlying_price || contract.strike;
    const top = el("header", undefined, "calc-top"),
      titles = el("div", undefined, "calc-titles"),
      title = el("h2", `${symbol} profit calculator`),
      subtitle = el("p", "", "calc-subtitle"),
      close = el("button", "Close", "calc-close");
    title.id = "calc-title";
    dialog.setAttribute("aria-labelledby", title.id);
    close.type = "button";
    close.onclick = () => dialog.close();
    const templates = el("details", undefined, "calc-templates"),
      templateList = el("div", undefined, "calc-template-list");
    templates.append(el("summary", "Strategies"), templateList);
    titles.append(title, subtitle);
    const actions = el("div", undefined, "calc-top-actions");
    actions.append(templates, close);
    top.append(titles, actions);

    const stats = el("section", undefined, "calc-stats");
    stats.setAttribute("aria-label", "Position summary");
    const message = el("p", "", "error calc-message");
    message.setAttribute("role", "status");
    const visualHost = el("div", undefined, "calc-visual"),
      toolbar = el("div", undefined, "calc-toolbar"),
      live = el("output", "Calculating scenario…", "calc-live");
    live.setAttribute("aria-live", "polite");
    const legHost = el("div", undefined, "calc-legs"),
      railHost = el("div"),
      add = el("button", "+ Add leg", "calc-add"),
      strategy = el("p", "", "calc-strategy"),
      greeksHost = el("section", undefined, "calc-greeks"),
      modelInfo = el("div", undefined, "calc-model-info");
    add.type = "button";

    let serial = 0,
      timer,
      closed = false,
      view = "P/L curve",
      units = "$",
      result = null;
    const cache = new Map();
    const legs = selectedLegs
      ? selectedLegs.map((l) => ({
          quote: l,
          expiration: l.expiration,
          action: l.side,
          quantity: initial.contract_quantity || 1,
          premium: null,
        }))
      : [
          {
            quote: contract,
            expiration: initial.expiration,
            action: "buy",
            quantity: 1,
            premium: null,
          },
        ];

    // Scenario controls (labels kept stable: tests and screen readers use them).
    const price = numeric(Number(spot.toFixed(2)), 0.01, 10000000, 0.01),
      priceSlider = numeric(
        spot,
        Math.max(0.01, Number((spot * 0.1).toFixed(2))),
        Number((spot * 2.5).toFixed(2)),
        0.01,
        "range",
      );
    priceSlider.setAttribute("aria-label", "Move underlying price");
    const date = el("input");
    date.type = "date";
    date.min = initial.asof;
    date.value = initial.asof;
    const dateSlider = numeric(
      day(initial.asof),
      day(initial.asof),
      day(initial.expiration),
      1,
      "range",
    );
    dateSlider.setAttribute("aria-label", "Move scenario date");
    const iv = numeric(0, -75, 150, 1, "range"),
      ivValue = el("output", "+0 pts");
    const range = numeric(20, 2, 60, 1, "range"),
      rangeValue = el("output", "±20%");
    const rate = numeric(4, -5, 50, 0.1),
      dividend = numeric(0, 0, 50, 0.1),
      fee = numeric(0.65, 0, 100, 0.01);
    const priceNote = el("small", "", "calc-control-note"),
      dateNote = el("small", "", "calc-control-note");

    // Each scenario control: a label, an exact value (typed or shown), a note and a slider.
    function control(label, value, note, slider) {
      const box = el("div", undefined, "calc-control"),
        head = el("div", undefined, "calc-control-head");
      head.append(el("span", label, "calc-control-label"), value, note);
      box.append(head, slider);
      return box;
    }
    price.setAttribute("aria-label", "Underlying scenario price ($)");
    date.setAttribute("aria-label", "Scenario date (at close)");
    iv.setAttribute("aria-label", "IV change (percentage points)");
    const controls = el("section", undefined, "calc-controls");
    controls.setAttribute("aria-label", "Scenario");
    controls.append(
      control("Price", price, priceNote, priceSlider),
      control("Date", date, dateNote, dateSlider),
      control("Implied volatility", ivValue, el("small"), iv),
    );

    const assumptions = el("details", undefined, "calc-assumptions");
    const assumptionFields = el("div", undefined, "calc-assumption-fields");
    assumptionFields.append(
      field("Risk-free rate (%)", rate),
      field("Dividend yield (%)", dividend),
      field("Fee per contract, each way ($)", fee),
    );
    assumptions.append(
      el("summary", "Pricing assumptions and fees"),
      assumptionFields,
    );
    const source = el("details", undefined, "calc-source");
    source.append(
      el(
        "summary",
        `${initial.source || "Saved"} quotes · snapshot ${initial.asof}`,
      ),
      el(
        "p",
        `${initial.idea_id ? "This play uses its archived entry reference; changing the scenario does not rewrite that entry. " : "Bid and ask come from the saved chain; Schwab refreshes it through the collector. "}Scenarios use saved quotes, not guaranteed fills.${initial.stale_session ? " Archived prices: refresh the chain before relying on current costs." : ""}`,
      ),
    );

    const main = el("section", undefined, "calc-main"),
      side = el("aside", undefined, "calc-side");
    main.setAttribute("aria-label", "Profit and loss");
    main.append(toolbar, visualHost, live, controls, message);
    const positionHead = el("div", undefined, "calc-side-head");
    positionHead.append(el("h3", "Position"), strategy);
    side.append(
      positionHead,
      railHost,
      legHost,
      add,
      greeksHost,
      assumptions,
      source,
      modelInfo,
    );
    const body = el("div", undefined, "calc-body");
    body.append(main, side);
    dialog.append(top, stats, body);
    document.body.append(dialog);
    dialog.showModal();
    dialog.onclose = () => {
      closed = true;
      clearTimeout(timer);
      dialog.remove();
    };

    async function load(expiration) {
      if (!cache.has(expiration)) {
        const data = await api(
          `stock/${encodeURIComponent(symbol)}/options?expiration=${encodeURIComponent(expiration)}&limit=1000${initial.idea_id ? `&idea_id=${initial.idea_id}` : ""}`,
        );
        if (!data.contracts.length)
          throw Error(
            "No stored contracts for that expiration. Keep the previous leg or refresh the chain.",
          );
        cache.set(expiration, data);
      }
      return cache.get(expiration);
    }
    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(calculate, 160);
    }
    // P/L as a percentage of what is at risk: the maximum loss, or the debit paid.
    function basisOf(r) {
      if (!r) return null;
      if (Number.isFinite(r.risk.max_loss) && r.risk.max_loss > 0)
        return r.risk.max_loss;
      return r.entry_cost > 0 ? r.entry_cost : null;
    }
    function choose(p, d) {
      price.value = Number(p.toFixed(2));
      priceSlider.value = price.value;
      date.value = d;
      dateSlider.value = day(d);
      schedule();
    }
    function drawToolbar() {
      const basis = basisOf(result);
      const unitBar = KestrelCharts.ranges(
        basis ? ["$", "%"] : ["$"],
        basis ? units : "$",
        (v) => {
          units = v;
          drawVisual();
        },
        { label: "Show P/L as", className: "calc-units" },
      );
      const rangeBox = el("label", undefined, "calc-range");
      rangeBox.append(el("span", "Range"), range, rangeValue);
      range.setAttribute("aria-label", "Price range around today's price (%)");
      toolbar.replaceChildren(
        KestrelCharts.ranges(
          ["P/L curve", "Price & time"],
          view,
          (v) => {
            view = v;
            drawVisual();
            drawToolbar();
          },
          { label: "Calculator view" },
        ),
        ...(view === "Price & time" ? [unitBar] : []),
        rangeBox,
      );
    }
    function drawVisual() {
      if (!result) return;
      const basis = basisOf(result);
      visualHost.replaceChildren(
        view === "P/L curve"
          ? payoffChart(result, { onSelect: choose, basis })
          : table(result, { onSelect: choose, units, basis }),
      );
    }
    function drawStats() {
      const r = result,
        basis = basisOf(r),
        risk = r.risk;
      const be = risk.breakevens.length
        ? risk.breakevens
            .slice(0, 2)
            .map(
              (x) => `${money(x)} (${signedPct(x / r.underlying_price - 1)})`,
            )
            .join(" · ")
        : "—";
      const ratio =
        Number.isFinite(risk.max_profit) &&
        Number.isFinite(risk.max_loss) &&
        risk.max_loss > 0
          ? `${(risk.max_profit / risk.max_loss).toFixed(2)}×`
          : "—";
      const chance = r.chance;
      stats.replaceChildren(
        stat(
          r.entry_cost < 0 ? "Net entry credit" : "Net entry debit",
          money(Math.abs(r.entry_cost)),
          "After fees, at the saved bid/ask",
        ),
        stat(
          "Max profit",
          risk.unlimited_profit ? "Unlimited" : money(risk.max_profit),
          risk.exact ? "At expiration" : "Needs one expiration",
          "up",
        ),
        stat(
          "Max loss",
          risk.unlimited_loss ? "Unlimited" : money(risk.max_loss),
          risk.exact ? "At expiration" : "Needs one expiration",
          "down",
        ),
        stat(
          risk.breakevens.length > 1 ? "Break-evens" : "Break-even",
          be,
          "Price at expiration",
        ),
        stat(
          "Chance of profit",
          chance ? `${Math.round(chance.chance * 100)}%` : "—",
          chance
            ? `Options-implied · ±${money(chance.expected_move)} (±${((chance.expected_move / r.underlying_price) * 100).toFixed(1)}%) 1σ move`
            : "Needs one expiration and IV",
          "chance",
        ),
        stat(
          "Profit ÷ risk",
          ratio,
          basis ? `Risking ${money(basis)}` : "Undefined risk",
        ),
      );
    }
    function drawGreeks() {
      const g = result.greeks;
      greeksHost.replaceChildren(el("h3", "Greeks"));
      if (!g) {
        greeksHost.append(
          el(
            "p",
            "Unavailable on an expiration date or without IV for every leg.",
            "muted caption",
          ),
        );
        return;
      }
      const grid = el("dl", undefined, "calc-greek-grid");
      for (const [name, value, note] of [
        ["Delta", number(g.delta), `≈ ${Math.round(g.delta)} shares`],
        ["Gamma", number(g.gamma), "Delta change per $1"],
        ["Theta", signedMoney(g.theta_per_day), "Per day"],
        ["Vega", signedMoney(g.vega_per_point), "Per IV point"],
      ]) {
        const item = el("div");
        item.append(el("dt", name), el("dd", value), el("small", note));
        grid.append(item);
      }
      greeksHost.append(grid);
    }
    function drawNotes() {
      const daysLeft = day(result.first_expiration) - day(date.value);
      priceNote.textContent = signedPct(
        +price.value / result.underlying_price - 1,
      );
      dateNote.textContent =
        daysLeft > 0
          ? `${daysLeft} day${daysLeft === 1 ? "" : "s"} to ${result.first_expiration === result.last_expiration ? "expiration" : "first expiration"}`
          : "At expiration";
      const info = el("div", undefined, "caption muted");
      info.append(el("p", result.risk.message));
      if (result.legs.some((l) => l.iv == null))
        info.append(
          el(
            "p",
            "IV cannot be inferred for at least one leg. Its pre-expiry curve is unavailable; the payoff at expiration stays exact.",
          ),
        );
      const model = el("details");
      model.append(
        el("summary", "Pricing model and limitations"),
        el("p", result.model),
        el(
          "p",
          "Chance of profit is the options market's risk-neutral view from the legs' implied volatility, the way broker calculators show it. It is not KESTREL's calibrated chance and not a forecast.",
        ),
      );
      info.append(model);
      modelInfo.replaceChildren(info);
    }
    async function calculate() {
      const request = ++serial;
      if ([price, date, rate, dividend, fee].some((n) => !n.checkValidity())) {
        message.textContent = "Check the highlighted scenario inputs.";
        return;
      }
      try {
        const r = await api(`stock/${encodeURIComponent(symbol)}/calculator`, {
          ...(initial.idea_id ? { idea_id: initial.idea_id } : {}),
          legs: legs.map((l) => ({
            occ: l.quote.occ,
            action: l.action,
            quantity: l.quantity,
            entry_price: l.premium,
          })),
          valuation_date: date.value,
          underlying_price: +price.value,
          iv_change: +iv.value / 100,
          rate: +rate.value / 100,
          dividend_yield: +dividend.value / 100,
          fee_per_contract: +fee.value,
          price_range: +range.value / 100,
        });
        if (closed || request !== serial) return;
        result = r;
        message.textContent = "";
        const basis = basisOf(r);
        live.replaceChildren(
          el("span", "Scenario P/L after fees"),
          el(
            "strong",
            `${signedMoney(r.scenario_pl)}${basis && Number.isFinite(r.scenario_pl) ? ` (${signedPct(r.scenario_pl / basis)})` : ""}`,
            r.scenario_pl > 0 ? "up" : r.scenario_pl < 0 ? "down" : "",
          ),
          el(
            "span",
            `if ${symbol} is ${money(+price.value)} on ${shortDate(date.value, true)}`,
          ),
        );
        subtitle.textContent = `${money(r.underlying_price)} · ${strategyName(legs)} · quotes ${r.captured}${r.archived ? " (archived)" : ""}`;
        drawStats();
        drawToolbar();
        drawVisual();
        drawGreeks();
        drawNotes();
        for (const [i, info] of r.legs.entries()) {
          const meta = legHost.querySelectorAll(".calc-leg-quote")[i];
          if (meta)
            meta.textContent = `Bid ${money(legs[i].quote.bid)} · Ask ${money(legs[i].quote.ask)} · IV ${info.iv == null ? "—" : (info.iv * 100).toFixed(1) + "%"}${info.custom_price ? " · custom premium" : ""}`;
        }
      } catch (error) {
        if (!closed && request === serial) {
          message.textContent = error.message;
          live.textContent =
            "Scenario unavailable. Check the selected contracts and inputs.";
          stats.replaceChildren();
          visualHost.replaceChildren();
          modelInfo.replaceChildren();
        }
      }
    }
    function dates() {
      const last = legs
        .map((l) => l.expiration)
        .sort()
        .at(-1);
      date.max = last;
      dateSlider.max = day(last);
      if (date.value > last) date.value = last;
      dateSlider.value = day(date.value);
    }
    function drawLegs() {
      legHost.replaceChildren();
      add.disabled = legs.length >= 8;
      strategy.textContent = strategyName(legs);
      railHost.replaceChildren(rail(legs, spot));
      legs.forEach((leg, index) => {
        const card = el("section", undefined, `calc-leg ${leg.action}`);
        card.setAttribute("aria-label", `Leg ${index + 1}`);
        const remove = el("button", "×", "calc-remove");
        remove.type = "button";
        remove.setAttribute("aria-label", `Remove leg ${index + 1}`);
        remove.disabled = legs.length === 1;
        remove.onclick = () => {
          legs.splice(index, 1);
          dates();
          drawLegs();
          schedule();
        };
        const action = select(
            [
              ["buy", "Buy"],
              ["sell", "Sell"],
            ],
            leg.action,
          ),
          quantity = numeric(leg.quantity, 1, Number.MAX_SAFE_INTEGER, 1);
        const type = select(
          [
            ["call", "Call"],
            ["put", "Put"],
          ],
          leg.quote.kind,
        );
        const expiry = select(
          initial.expirations.map((s) => [
            s,
            `${shortDate(s)} ’${s.slice(2, 4)}`,
          ]),
          leg.expiration,
        );
        const available = (
          cache.get(leg.expiration)?.contracts || initial.contracts
        )
          .filter((q) => q.kind === leg.quote.kind)
          .sort((a, b) => a.strike - b.strike);
        const strike = select(
          available.map((q) => [q.occ, money(q.strike)]),
          leg.quote.occ,
        );
        const down = el("button", "‹", "calc-step"),
          up = el("button", "›", "calc-step");
        down.type = up.type = "button";
        down.setAttribute("aria-label", `Lower strike, leg ${index + 1}`);
        up.setAttribute("aria-label", `Higher strike, leg ${index + 1}`);
        const position = available.findIndex((q) => q.occ === leg.quote.occ);
        down.disabled = position <= 0;
        up.disabled = position < 0 || position >= available.length - 1;
        const premium = numeric(leg.premium ?? "", 0, 1000000, 0.01);
        premium.placeholder = "Saved bid/ask";
        function pick(q) {
          leg.quote = q;
          leg.premium = null;
          drawLegs();
          schedule();
        }
        action.onchange = () => {
          leg.action = action.value;
          drawLegs();
          schedule();
        };
        quantity.oninput = () => {
          if (quantity.checkValidity()) {
            leg.quantity = +quantity.value;
            strategy.textContent = strategyName(legs);
            railHost.replaceChildren(rail(legs, spot));
            schedule();
          }
        };
        premium.oninput = () => {
          if (premium.checkValidity()) {
            leg.premium = premium.value === "" ? null : +premium.value;
            schedule();
          }
        };
        strike.onchange = () =>
          pick(available.find((q) => q.occ === strike.value));
        down.onclick = () => pick(available[position - 1]);
        up.onclick = () => pick(available[position + 1]);
        let update = 0;
        async function changeContract(expiration, kind) {
          const id = ++update;
          message.textContent = "Loading saved strikes…";
          try {
            const data = await load(expiration);
            if (closed || id !== update) return;
            const choices = data.contracts
              .filter((q) => q.kind === kind)
              .sort(
                (a, b) =>
                  Math.abs(a.strike - leg.quote.strike) -
                  Math.abs(b.strike - leg.quote.strike),
              );
            if (!choices.length)
              throw Error(
                "No stored contracts on that side. The previous leg is kept.",
              );
            leg.quote = choices[0];
            leg.expiration = expiration;
            leg.premium = null;
            message.textContent = "";
            dates();
            drawLegs();
            schedule();
          } catch (error) {
            message.textContent = error.message;
            drawLegs();
          }
        }
        expiry.onchange = () => changeContract(expiry.value, leg.quote.kind);
        type.onchange = () => changeContract(leg.expiration, type.value);
        const strikeBox = el("div", undefined, "calc-strike");
        strikeBox.append(down, strike, up);
        strike.setAttribute("aria-label", "Strike");
        const head = el("div", undefined, "calc-leg-head");
        head.append(
          field("Action", action, "calc-field calc-action"),
          field("Contracts", quantity, "calc-field calc-qty"),
          field("Call or put", type, "calc-field"),
          remove,
        );
        const strikeField = el("div", undefined, "calc-field");
        strikeField.append(el("span", "Strike"), strikeBox);
        const rows = el("div", undefined, "calc-leg-body");
        rows.append(field("Expiration", expiry), strikeField);
        const foot = el("div", undefined, "calc-leg-foot");
        const meta = el("p", "", "calc-leg-quote");
        meta.textContent = `Bid ${money(leg.quote.bid)} · Ask ${money(leg.quote.ask)}`;
        foot.append(field("Entry premium ($/share)", premium), meta);
        premium.setAttribute("aria-label", "Assumed entry premium ($/share)");
        card.append(head, rows, foot);
        legHost.append(card);
      });
    }

    // A template replaces the legs with the nearest saved strikes on the first leg's expiration.
    async function applyTemplate(name, spec) {
      templates.open = false;
      const expiration = legs[0].expiration;
      try {
        const data = await load(expiration);
        let next = null;
        if (spec.some((s) => s[4] === "next")) {
          const later = initial.expirations.filter((e) => e > expiration);
          if (!later.length)
            throw Error("A calendar spread needs a later saved expiration.");
          next = later[0];
          await load(next);
        }
        if (closed) return;
        const used = new Set();
        const built = spec.map(([action, kind, offset, quantity = 1, when]) => {
          const exp = when === "next" ? next : expiration;
          const choices = cache
            .get(exp)
            .contracts.filter(
              (q) => q.kind === kind && (q.ask > 0 || q.bid > 0),
            )
            .sort(
              (a, b) =>
                Math.abs(a.strike - spot * (1 + offset)) -
                Math.abs(b.strike - spot * (1 + offset)),
            );
          const q = choices.find((c) => !used.has(c.occ)) || choices[0];
          if (!q) throw Error(`No saved ${kind}s on ${exp} for ${name}.`);
          used.add(q.occ);
          return { quote: q, expiration: exp, action, quantity, premium: null };
        });
        legs.splice(0, legs.length, ...built);
        message.textContent = "";
        dates();
        drawLegs();
        schedule();
      } catch (error) {
        message.textContent = error.message;
      }
    }
    let group = null,
      groupBox = null;
    for (const [category, name, spec] of TEMPLATES) {
      if (category !== group) {
        group = category;
        groupBox = el("div", undefined, "calc-template-group");
        groupBox.append(el("span", category));
        templateList.append(groupBox);
      }
      const b = el("button", name);
      b.type = "button";
      b.onclick = () => applyTemplate(name, spec);
      groupBox.append(b);
    }

    add.onclick = () => {
      if (legs.length < 8) {
        legs.push({ ...legs.at(-1) });
        dates();
        drawLegs();
        schedule();
      }
    };
    price.oninput = () => {
      priceSlider.value = price.value;
      schedule();
    };
    priceSlider.oninput = () => {
      price.value = priceSlider.value;
      schedule();
    };
    date.onchange = () => {
      dateSlider.value = day(date.value);
      schedule();
    };
    dateSlider.oninput = () => {
      date.value = iso(+dateSlider.value);
      schedule();
    };
    iv.oninput = () => {
      ivValue.textContent = `${+iv.value >= 0 ? "+" : MINUS}${Math.abs(+iv.value)} pts`;
      schedule();
    };
    range.oninput = () => {
      rangeValue.textContent = `±${range.value}%`;
      schedule();
    };
    for (const n of [rate, dividend, fee]) n.oninput = schedule;
    try {
      const expirations = [...new Set(legs.map((l) => l.expiration))];
      for (const expiration of expirations) await load(expiration);
      if (!closed) {
        dates();
        drawLegs();
        calculate();
      }
    } catch (error) {
      message.textContent = error.message;
    }
  }
  return { open, strategyName };
})();
