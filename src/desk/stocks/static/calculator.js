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
  function svg(tag, attrs) {
    const n = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs))
      n.setAttribute(key, value);
    return n;
  }
  function payoffChart(result) {
    const box = el("div", undefined, "payoff-chart"),
      curve = result.curve;
    const values = curve
      .flatMap((p) => [p.scenario, p.expiration])
      .filter((v) => v != null);
    if (!values.length) {
      box.append(
        el(
          "p",
          "No model curve available for these quotes. Select the common expiration date for exact payoff, or choose contracts with inferable IV.",
          "muted",
        ),
      );
      return box;
    }
    const min = Math.min(0, ...values),
      max = Math.max(0, ...values),
      span = max - min || 1;
    const x = (p) => 50 + (p / curve.at(-1).price) * 900,
      y = (v) => 260 - ((v - min) / span) * 230;
    const plot = svg("svg", {
      viewBox: "0 0 1000 310",
      role: "img",
      tabindex: "0",
      "aria-label":
        "Profit and loss by underlying price. Use arrow keys to inspect prices.",
    });
    plot.append(
      svg("line", {
        x1: 50,
        x2: 950,
        y1: y(0),
        y2: y(0),
        class: "chart-guide",
      }),
    );
    for (const [key, cls] of [
      ["expiration", "payoff-expiry"],
      ["scenario", "payoff-scenario"],
    ]) {
      let path = "";
      curve.forEach((p, i) => {
        if (p[key] != null)
          path += `${i ? "L" : "M"}${x(p.price)},${y(p[key])}`;
      });
      plot.append(svg("path", { d: path, class: cls }));
    }
    const pointer = svg("line", {
      x1: 0,
      x2: 0,
      y1: 15,
      y2: 270,
      class: "chart-cursor",
      visibility: "hidden",
    });
    plot.append(pointer);
    const readout = el(
      "p",
      "Move across the chart to inspect a price scenario.",
      "caption",
    );
    readout.setAttribute("aria-live", "polite");
    let selected = Math.round(curve.length / 2);
    function inspect(i) {
      selected = Math.max(0, Math.min(curve.length - 1, i));
      const p = curve[selected];
      pointer.setAttribute("x1", x(p.price));
      pointer.setAttribute("x2", x(p.price));
      pointer.setAttribute("visibility", "visible");
      readout.textContent = `Underlying ${money(p.price)} · Scenario P/L ${money(p.scenario)}${p.expiration == null ? "" : ` · Expiration P/L ${money(p.expiration)}`}`;
    }
    plot.onpointermove = (e) => {
      const r = plot.getBoundingClientRect(),
        price =
          Math.max(
            0,
            Math.min(1, (((e.clientX - r.left) / r.width) * 1000 - 50) / 900),
          ) * curve.at(-1).price;
      let i = 0;
      curve.forEach((p, j) => {
        if (Math.abs(p.price - price) < Math.abs(curve[i].price - price)) i = j;
      });
      inspect(i);
    };
    plot.onkeydown = (e) => {
      if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
        e.preventDefault();
        inspect(
          e.key === "Home"
            ? 0
            : e.key === "End"
              ? curve.length - 1
              : selected + (e.key === "ArrowLeft" ? -1 : 1),
        );
      }
    };
    const axis = el("div", undefined, "chart-axis");
    axis.append(
      el("span", money(0)),
      el("span", "Underlying price"),
      el("span", money(curve.at(-1).price)),
    );
    box.append(
      plot,
      axis,
      readout,
      el(
        "p",
        "Blue: selected date and IV scenario · Dashed: exact common-expiration payoff",
        "muted caption",
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
    const notice = el(
      "p",
      `${initial.source} snapshot ${initial.asof}. Model scenarios use saved quotes, not guaranteed fills. ${initial.stale_session ? "Archived prices: refresh the chain before relying on current costs." : ""}`,
      "notice",
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
      closed = false;
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
      el("h3", "Explore the outcome"),
      controls,
      assumptionsBox,
    );
    results.append(message, summary, plot);
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
        plot.replaceChildren(payoffChart(result), info);
      } catch (error) {
        if (!closed && request === serial) {
          message.textContent = error.message;
          live.textContent =
            "Scenario unavailable. Check the selected contracts and inputs.";
          summary.replaceChildren();
          plot.replaceChildren();
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
