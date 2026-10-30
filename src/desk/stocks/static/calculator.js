"use strict";
const KestrelCalculator = (() => {
  const iso = (n) => new Date(n * 86400000).toISOString().slice(0, 10);
  const day = (s) => Math.round(Date.parse(s + "T00:00:00Z") / 86400000);
  function field(label, control) {
    const box = el("label", undefined, "calc-field");
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
  function payoffChart(result, { wide = false, onSelect = () => {} } = {}) {
    const spot = result.underlying_price,
      focus = result.scenario_price;
    const curve = wide
      ? result.curve
      : result.curve.filter(
          (p) =>
            p.price >= Math.min(spot * 0.65, focus * 0.9) &&
            p.price <= Math.max(spot * 1.35, focus * 1.1),
        );
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
          label: "Selected date & IV",
          signed: true,
          value: (p) => p.scenario,
          color: "var(--up)",
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
      changeText: () => "Modeled position value after entry and closing fees",
      tooltipRows: (p) => [
        ["Underlying", money(p.price)],
        ["Scenario P/L", money(p.scenario)],
        ["Expiration P/L", money(p.expiration)],
      ],
      references: [
        { x: spot, label: "Spot" },
        ...result.risk.breakevens.map((x) => ({ x, label: "B/E" })),
        ...[
          ...new Set(result.legs.map((l) => l.strike).filter(Number.isFinite)),
        ].map((x) => ({ x, label: "Strike" })),
      ],
      coverage:
        "Drag or use arrow keys to explore. Click a price to set the scenario. Solid: modeled selected date; dashed: common-expiration payoff. Neither is a forecast or guaranteed fill.",
      onSelect: (p) => onSelect(p.price, result.valuation_date),
      empty:
        "No inferable IV for these quotes. A common-expiry payoff remains available at expiration.",
    });
    const index = curve.findIndex((p) => p.price === focus);
    if (index >= 0) chart.inspect(index);
    return chart;
  }
  function heatmap(result, onSelect) {
    const box = el("div", undefined, "calc-heatmap"),
      grid = el("div", undefined, "calc-heatmap-grid"),
      data = result.heatmap;
    if (!data)
      return el(
        "p",
        "The saved server does not support the scenario grid. Restart after updating.",
        "muted",
      );
    grid.style.setProperty("--heat-columns", data.dates.length);
    grid.append(el("strong", "Stock / date"));
    for (const d of data.dates)
      grid.append(el("strong", KestrelCharts.date(d)));
    const maximum = Math.max(
      1,
      ...data.values.flat().filter(Number.isFinite).map(Math.abs),
    );
    for (let i = data.prices.length - 1; i >= 0; i--) {
      grid.append(el("strong", money(data.prices[i])));
      data.values[i].forEach((v, j) => {
        const b = el(
          "button",
          Number.isFinite(v) ? KestrelCharts.signed(v) : "—",
          `calc-heat-cell ${v >= 0 ? "positive" : "negative"}`,
        );
        b.type = "button";
        b.disabled = v == null;
        b.style.setProperty(
          "--heat-opacity",
          0.08 + 0.35 * Math.sqrt(Math.abs(v || 0) / maximum),
        );
        b.setAttribute(
          "aria-label",
          `${data.dates[j]} · underlying ${money(data.prices[i])} · modeled P/L ${money(v)}`,
        );
        b.onclick = () => onSelect(data.prices[i], data.dates[j]);
        grid.append(b);
      });
    }
    box.append(
      grid,
      el(
        "p",
        "Modeled P/L after fees. Tap a cell to set its price and date; IV stays at the selected assumption. Mixed expirations assume the same price at each earlier expiry.",
        "caption muted",
      ),
    );
    return box;
  }
  async function open(symbol, contract, initial, selectedLegs = null) {
    document.querySelector(".options-calculator")?.remove();
    const dialog = el("dialog", undefined, "options-calculator");
    const header = el("div", undefined, "calc-heading"),
      close = el("button", "Close");
    const title = el("h2", `${symbol} options P/L calculator`);
    title.id = "calc-title";
    header.append(title, close);
    dialog.setAttribute("aria-labelledby", title.id);
    close.onclick = () => dialog.close();
    const noticeText = el(
      "p",
      `${initial.source} snapshot ${initial.asof}. ${initial.idea_id ? "This play uses its archived entry reference; changing the scenario does not rewrite that entry." : "Bid/ask inputs come from the saved chain; Schwab refreshes through the collector."} Model scenarios use saved quotes, not guaranteed fills. ${initial.stale_session ? "Archived prices: refresh the chain before relying on current costs." : ""}`,
      "caption muted",
    );
    const notice = el("details", undefined, "calc-source");
    notice.append(
      el(
        "summary",
        `${initial.source} · quote snapshot ${initial.asof} · modeled scenarios`,
      ),
      noticeText,
    );
    const legHost = el("div", undefined, "calc-legs"),
      add = el("button", "+ Add leg"),
      message = el("p", "", "error");
    message.setAttribute("role", "status");
    const summary = el("div", undefined, "calc-summary"),
      plot = el("div"),
      assumptions = el("div", undefined, "calc-assumptions");
    let serial = 0,
      timer,
      closed = false,
      chartMode = "P/L curve",
      wide = false;
    const cache = new Map();
    const spot = initial.underlying_price || contract.strike;
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
    const price = numeric(Number(spot.toFixed(2)), 0.01, 10000000, 0.01),
      priceSlider = numeric(
        spot,
        Math.max(0.01, Number((spot * 0.1).toFixed(2))),
        Number((spot * 2.5).toFixed(2)),
        0.01,
        "range",
      );
    const date = el("input");
    date.type = "date";
    date.min = initial.asof;
    const dateSlider = numeric(
      day(initial.asof),
      day(initial.asof),
      day(initial.expiration),
      1,
      "range",
    );
    date.value = initial.asof;
    const iv = numeric(0, -75, 150, 1, "range"),
      ivValue = el("output", "0 volatility points");
    const rate = numeric(4, -5, 50, 0.1),
      dividend = numeric(0, 0, 50, 0.1),
      fee = numeric(0.65, 0, 100, 0.01);
    const controls = el("section", undefined, "calc-scenarios");
    controls.append(
      field("Underlying scenario price ($)", price),
      field("Move underlying price", priceSlider),
      field("Scenario date (at close)", date),
      field("Move scenario date", dateSlider),
      field("IV change (percentage points)", iv),
      ivValue,
    );
    assumptions.append(
      field("Risk-free rate (%)", rate),
      field("Dividend yield (%)", dividend),
      field("Fee per contract, each way ($)", fee),
    );
    const assumptionsBox = el("details");
    assumptionsBox.append(
      el("summary", "Pricing assumptions and fees"),
      assumptions,
    );
    const workspace = el("div", undefined, "calc-workspace"),
      editor = el("div", undefined, "calc-editor"),
      results = el("div", undefined, "calc-results"),
      live = el("output", "Calculating scenario…", "calc-live");
    live.setAttribute("aria-live", "polite");
    editor.append(
      el("h3", "Build your position"),
      legHost,
      add,
      assumptionsBox,
    );
    const modelInfo = el("div", undefined, "calc-model-info");
    results.append(
      message,
      plot,
      el("h3", "Explore price, time and volatility"),
      controls,
      summary,
      modelInfo,
    );
    workspace.append(editor, results);
    dialog.append(header, notice, workspace, live);
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
      timer = setTimeout(calculate, 180);
    }
    async function calculate() {
      const request = ++serial;
      const inputs = [price, date, rate, dividend, fee];
      if (inputs.some((n) => !n.checkValidity())) {
        message.textContent = "Check the highlighted scenario inputs.";
        return;
      }
      try {
        const result = await api(
          `stock/${encodeURIComponent(symbol)}/calculator`,
          {
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
          },
        );
        if (closed || request !== serial) return;
        message.textContent = "";
        live.textContent = `Scenario P/L after fees: ${money(result.scenario_pl)} · ${money(+price.value)} underlying · ${date.value}`;
        summary.replaceChildren();
        for (const [label, value] of [
          [
            result.entry_cost < 0 ? "Net entry credit" : "Net entry debit",
            money(Math.abs(result.entry_cost)),
          ],
          ["Scenario P/L after fees", money(result.scenario_pl)],
          [
            "Maximum profit at expiration",
            result.risk.unlimited_profit
              ? "Unlimited"
              : money(result.risk.max_profit),
          ],
          [
            "Maximum loss at expiration",
            result.risk.unlimited_loss
              ? "Unlimited risk"
              : money(result.risk.max_loss),
          ],
          [
            "Break-even prices",
            result.risk.breakevens.map(money).join(", ") ||
              "None / unavailable",
          ],
        ]) {
          const card = el("div");
          card.append(el("span", label), el("strong", value));
          summary.append(card);
        }
        const info = el("div", undefined, "caption muted");
        info.append(el("p", result.risk.message));
        const model = el("details");
        model.append(
          el("summary", "Pricing model and limitations"),
          el("p", result.model),
        );
        info.append(model);
        if (result.greeks)
          info.append(
            el(
              "p",
              `Position Greeks: Delta ${number(result.greeks.delta)} · Gamma ${number(result.greeks.gamma)} · Theta ${money(result.greeks.theta_per_day)}/day · Vega ${money(result.greeks.vega_per_point)}/IV point`,
            ),
          );
        if (result.legs.some((l) => l.iv == null))
          info.append(
            el(
              "p",
              "IV cannot be inferred for at least one leg. Its pre-expiry scenario curve is unavailable; common-expiry payoff remains exact.",
            ),
          );
        const choose = (p, d) => {
          price.value = Number(p.toFixed(2));
          priceSlider.value = price.value;
          date.value = d;
          dateSlider.value = day(d);
          schedule();
        };
        const panel = el("div", undefined, "calc-visual"),
          rangeLabel = el("label", undefined, "kc-toggle"),
          rangeInput = el("input");
        rangeInput.type = "checkbox";
        rangeInput.checked = wide;
        const visual = el("div");
        function renderVisual() {
          visual.replaceChildren(
            chartMode === "P/L curve"
              ? payoffChart(result, { wide, onSelect: choose })
              : heatmap(result, choose),
          );
        }
        rangeInput.onchange = () => {
          wide = rangeInput.checked;
          renderVisual();
        };
        rangeLabel.append(rangeInput, el("span", "Wide price range"));
        const chartTools = el("div", undefined, "kc-toolbar");
        chartTools.append(
          KestrelCharts.ranges(
            ["P/L curve", "Price & time"],
            chartMode,
            (v) => {
              chartMode = v;
              renderVisual();
            },
            { label: "Calculator visualization" },
          ),
          rangeLabel,
        );
        panel.append(chartTools, visual);
        renderVisual();
        plot.replaceChildren(panel);
        modelInfo.replaceChildren(info);
      } catch (error) {
        if (!closed && request === serial) {
          message.textContent = error.message;
          live.textContent =
            "Scenario unavailable. Check the selected contracts and inputs.";
          summary.replaceChildren();
          plot.replaceChildren();
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
      legs.forEach((leg, index) => {
        const row = el("section", undefined, "calc-leg"),
          heading = el("div", undefined, "calc-heading");
        const remove = el("button", "Remove");
        remove.disabled = legs.length === 1;
        remove.onclick = () => {
          legs.splice(index, 1);
          dates();
          drawLegs();
          schedule();
        };
        heading.append(el("h4", `Leg ${index + 1}`), remove);
        row.append(heading);
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
          initial.expirations.map((s) => [s, s]),
          leg.expiration,
        );
        const expirySlider = numeric(
          initial.expirations.indexOf(leg.expiration),
          0,
          initial.expirations.length - 1,
          1,
          "range",
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
        const strikeSlider = numeric(
          available.findIndex((q) => q.occ === leg.quote.occ),
          0,
          available.length - 1,
          1,
          "range",
        );
        const premium = numeric(leg.premium ?? "", 0, 1000000, 0.01);
        premium.placeholder = "Use saved bid/ask";
        const quoteInfo = el("p", "", "muted caption");
        const updateInfo = () => {
          quoteInfo.textContent = `${leg.quote.kind.toUpperCase()} ${money(leg.quote.strike)} · Bid ${money(leg.quote.bid)} / Ask ${money(leg.quote.ask)} · ${leg.expiration} · 100 shares per contract`;
        };
        action.onchange = () => {
          leg.action = action.value;
          updateInfo();
          schedule();
        };
        quantity.oninput = () => {
          if (quantity.checkValidity()) {
            leg.quantity = +quantity.value;
            schedule();
          }
        };
        premium.oninput = () => {
          if (premium.checkValidity()) {
            leg.premium = premium.value === "" ? null : +premium.value;
            schedule();
          }
        };
        function choose(q) {
          leg.quote = q;
          leg.premium = null;
          premium.value = "";
          strike.value = q.occ;
          strikeSlider.value = available.indexOf(q);
          updateInfo();
          schedule();
        }
        strike.onchange = () =>
          choose(available.find((q) => q.occ === strike.value));
        strikeSlider.oninput = () => choose(available[+strikeSlider.value]);
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
                "No stored contracts on that side. The previous leg is preserved.",
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
        expirySlider.onchange = () =>
          changeContract(
            initial.expirations[+expirySlider.value],
            leg.quote.kind,
          );
        type.onchange = () => changeContract(leg.expiration, type.value);
        const fields = el("div", undefined, "calc-leg-fields");
        fields.append(
          field("Action", action),
          field("Contracts", quantity),
          field("Call or put", type),
          field("Expiration", expiry),
          field("Strike", strike),
          field("Assumed entry premium ($/share)", premium),
          field("Move expiration", expirySlider),
          field("Move strike", strikeSlider),
        );
        updateInfo();
        row.append(fields, quoteInfo);
        legHost.append(row);
      });
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
      ivValue.textContent = `${iv.value} volatility points`;
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
  return { open };
})();
