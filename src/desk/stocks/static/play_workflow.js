"use strict";
// Personal selections are quote-based PAPER references, separate from research cohorts and Learning.
const KestrelPlayWorkflow = (() => {
  let watcher = null,
    viewSerial = 0;
  const when = (s) =>
    s
      ? new Date(s).toLocaleString("en-US", {
          timeZone: "America/New_York",
          month: "short",
          day: "numeric",
          hour: "numeric",
          minute: "2-digit",
        }) + " ET"
      : "—";
  const percent = (x) => (x == null ? "—" : `${Math.round(100 * x)}%`);
  function trackButton(play) {
    const b = el(
      "button",
      `${play.action} ${play.type}`,
      `research-badge ${play.action === "SELL" ? "sell" : "buy"} play-track-button`,
    );
    b.type = "button";
    b.setAttribute(
      "aria-label",
      `${play.action} ${play.type}: confirm PAPER tracking for ${play.symbol} ${play.strike} ${play.expiration}`,
    );
    b.title = "Track this exact play in My tracked plays";
    b.disabled = !play.idea_id;
    b.onclick = () => confirmPlay(play, b);
    return b;
  }
  async function confirmPlay(play, anchor) {
    document.querySelector(".play-confirmation")?.remove();
    const dialog = el("dialog", undefined, "play-confirmation"),
      title = el("h2", `Track ${play.symbol} ${play.type}?`),
      description = el(
        "p",
        `${money(play.strike)} strike · ${play.expiration}`,
      ),
      status = el("p", "Checking entry timing…", "muted"),
      quantity = el("input"),
      label = el("label", undefined, "calc-field"),
      save = el("button", "Confirm PAPER tracking", "primary"),
      cancel = el("button", "Cancel"),
      actions = el("div", undefined, "play-actions");
    title.id = "play-confirm-title";
    dialog.setAttribute("aria-labelledby", title.id);
    Object.assign(quantity, {
      type: "number",
      min: "1",
      step: "1",
      value: "1",
    });
    quantity.setAttribute("aria-label", "Contracts per leg");
    label.append(
      el("span", play.legs.length > 1 ? "Contracts per leg" : "Contracts"),
      quantity,
    );
    actions.append(cancel, save);
    save.disabled = true;
    status.setAttribute("role", "status");
    dialog.append(
      title,
      description,
      label,
      status,
      el(
        "p",
        "This records a personal PAPER reference. You place any real trade separately with your broker. Opening prices can differ from this idea's quotes.",
        "caption muted",
      ),
      actions,
    );
    document.body.append(dialog);
    dialog.showModal();
    const position = () => {
      const r = anchor.getBoundingClientRect(),
        d = dialog.getBoundingClientRect();
      dialog.style.left = `${Math.max(12, Math.min(innerWidth - d.width - 12, r.left))}px`;
      dialog.style.top = `${Math.max(12, Math.min(innerHeight - d.height - 12, r.bottom + 8))}px`;
    };
    position();
    dialog.onclose = () => {
      removeEventListener("resize", position);
      dialog.remove();
      anchor.focus();
    };
    addEventListener("resize", position);
    cancel.onclick = () => dialog.close();
    const requestId = crypto.randomUUID();
    try {
      const data = await api(`plays/idea/${play.idea_id}`);
      if (!dialog.isConnected) return;
      status.textContent = data.plan.can_enter
        ? `Eligible ${when(data.plan.eligible_at)}. Uses the first fresh market-hours quote received on a tracking pass. ${data.plan.timing_policy}`
        : "This idea has expired; choose a current play.";
      save.disabled = !data.plan.can_enter;
      position();
    } catch (e) {
      status.textContent = e.message;
    }
    save.onclick = async () => {
      const n = +quantity.value;
      quantity.setCustomValidity(
        Number.isSafeInteger(n) && n > 0
          ? ""
          : "Enter a positive whole number of contracts.",
      );
      if (!quantity.reportValidity()) return;
      save.disabled = true;
      try {
        const selected = await api("plays/select", {
          idea_id: play.idea_id,
          quantity: n,
          request_id: requestId,
        });
        dialog.close();
        location.hash = `#plays/mine/${selected.id}`;
      } catch (e) {
        status.textContent = e.message;
        save.disabled = false;
      }
    };
  }
  function targets(play) {
    const box = el("div", undefined, "play-targets"),
      data = play.probability_targets;
    const meter = (name, value) => {
      const row = el("div", undefined, "play-target"),
        bar = el("meter");
      bar.min = 0;
      bar.max = 1;
      bar.value = value ?? 0;
      bar.setAttribute("aria-label", `${name}: ${percent(value)}`);
      row.append(el("span", name), el("strong", percent(value)), bar);
      box.append(row);
    };
    meter("Profit at expiry", data?.returns?.["0"] ?? play.profit_kestrel);
    if (data) {
      meter("+50% on risk at expiry", data.returns["50"]);
      const details = el("details"),
        more = el("div");
      details.append(el("summary", "Profit targets & what the odds mean"));
      for (const target of ["25", "100"]) {
        more.append(
          el(
            "p",
            `+${target}% on maximum risk at expiry: ${percent(data.returns[target])}`,
          ),
        );
      }
      if (data.max_gain_probability != null)
        more.append(
          el(
            "p",
            `Maximum gain at expiry: ${percent(data.max_gain_probability)}`,
          ),
        );
      more.append(el("p", data.max_gain_note), el("p", data.note));
      details.append(more);
      box.append(details);
    } else
      box.append(
        el(
          "small",
          "ITM and profit differ. More targets appear after the next analysis pass.",
        ),
      );
    return box;
  }
  function greeks(play) {
    const box = el("details", undefined, "play-greeks-guide");
    box.append(el("summary", "What do Δ, Γ, Θ and ν mean?"));
    const items = [
      [
        "Δ Delta",
        "Change in option value for a $1 stock move",
        play.delta == null
          ? null
          : `${money(play.delta * 100)} per standard contract for a $1 rise.`,
      ],
      [
        "Γ Gamma",
        "Change in Delta for a $1 stock move",
        play.gamma == null
          ? null
          : `Delta changes by about ${Number(play.gamma).toFixed(3)} for a $1 rise.`,
      ],
      [
        "Θ Theta",
        "Value change as one calendar day passes",
        play.theta == null
          ? null
          : `About ${money(play.theta * 100)} per day per standard contract.`,
      ],
      [
        "ν Vega",
        "Value change for one percentage point of implied volatility",
        play.vega == null
          ? null
          : `About ${money(play.vega * 100)} per contract when IV rises one point (e.g. 40% → 41%).`,
      ],
    ];
    for (const [name, text, example] of items) {
      const p = el("p");
      p.append(
        el("strong", `${name}: `),
        document.createTextNode(`${text}. ${example || ""}`),
      );
      box.append(p);
    }
    box.append(
      el(
        "p",
        "These are local estimates with other inputs held constant. Spreads show net Greeks for one contract per leg; your quantity scales the dollar changes. Standard contracts represent 100 shares.",
        "caption muted",
      ),
    );
    return box;
  }
  function heading(title, subtitle, active = "mine") {
    app.className = "plays-page play-workspace";
    app.replaceChildren(
      el("h1", title),
      el("p", subtitle, "muted"),
      KestrelPlaysHistory.views(active),
    );
  }
  async function mine(ticket, offset = 0) {
    clearInterval(watcher);
    const serial = ++viewSerial;
    const data = await api(`plays/mine?offset=${offset}&limit=20`);
    if (ticket !== generation || serial !== viewSerial) return;
    heading(
      "My tracked plays",
      "Your confirmed selections, entry references and exits. Kept separately from automatic research and the Learning account.",
    );
    if (!data.rows.length)
      app.append(
        panel(
          "Choose a play to track",
          "Open Today's plays, click a BUY or SELL badge and confirm. Premarket selections queue for the opening session.",
        ),
      );
    const grid = el("div", undefined, "play-grid research-grid");
    for (const row of data.rows) {
      const p = row.idea.play,
        card = el("article", undefined, "setup-card personal-play-card");
      card.append(
        el("span", row.state.toUpperCase(), "research-badge"),
        link(
          `${p.symbol} · ${money(p.underlying_price)}`,
          `#stock/${p.symbol}`,
        ),
        el("h2", `${p.action} ${p.type} · ${money(p.strike)}`),
        el(
          "p",
          `${p.expiration} · ${row.quantity} contract${row.quantity === 1 ? "" : "s"}${p.legs.length > 1 ? " per leg" : ""}`,
        ),
        el("p", row.message, "muted"),
        el(
          "p",
          row.entry
            ? `Reference entry ${money(row.entry.price * 100)} per structure · ${when(row.entry.at)}`
            : `Eligible ${when(row.eligible_at)}`,
          "caption",
        ),
        link("Open tracking & P/L →", `#plays/mine/${row.id}`),
      );
      grid.append(card);
    }
    app.append(grid);
    const pages = el("div", undefined, "play-actions");
    for (const [text, next, disabled] of [
      ["Previous", offset - 20, offset === 0],
      ["Next", offset + 20, offset + 20 >= data.total],
    ]) {
      const b = el("button", text);
      b.disabled = disabled;
      b.onclick = () => mine(ticket, next);
      pages.append(b);
    }
    pages.append(el("span", `${data.total} selections`));
    app.append(pages);
  }
  const exitTitle = {
    take_profit: "TAKE PROFIT",
    stop: "STOP: EXIT",
    time_exit: "TIME TO LEAVE",
    add: "ODDS IMPROVED: ADD IS ALLOWED",
    hold: "HOLD",
  };
  async function detail(ticket, id, personal = false) {
    clearInterval(watcher);
    const serial = ++viewSerial;
    const path = `plays/${personal ? "mine" : "idea"}/${encodeURIComponent(id)}`,
      data = await api(path);
    if (ticket !== generation || serial !== viewSerial) return;
    const play = data.idea.play,
      tracked = data.tracking;
    heading(
      `${play.symbol} · ${money(play.underlying_price)} · ${play.type}`,
      `${money(play.strike)} strike · ${play.expiration} · Stock price at identification · ${when(data.idea.identified_at)}`,
      personal ? "mine" : "board",
    );
    const actions = el("div", undefined, "play-actions"),
      refresh = el("button", "Refresh P/L"),
      calc = el("button", "Open exact-leg payoff calculator", "primary"),
      note = el("p", "", "caption muted");
    if (play.recovered_sighting && !play.underlying_price) {
      calc.disabled = true;
      note.textContent =
        "The original stock price was not saved, so this snapshot cannot support IV scenarios. P/L still uses saved option quotes. Open the company profile for its current chain and calculator.";
    }
    refresh.onclick = () => detail(ticket, id, personal);
    calc.onclick = async () => {
      calc.disabled = true;
      try {
        const chain = await api(
          `stock/${encodeURIComponent(play.symbol)}/options?expiration=${play.expiration}&limit=1000&idea_id=${data.idea.id}`,
        );
        chain.contract_quantity = tracked?.quantity || 1;
        await KestrelCalculator.open(
          play.symbol,
          play.legs[0],
          chain,
          play.legs,
        );
      } catch (e) {
        note.textContent = e.message;
      } finally {
        calc.disabled = false;
      }
    };
    actions.append(
      calc,
      refresh,
      link("Company profile →", `#stock/${play.symbol}`),
    );
    app.append(actions, note);
    if (tracked) {
      const status = panel(
        tracked.state.toUpperCase(),
        `${tracked.message} ${tracked.entry ? `Entry ${when(tracked.entry.at)} · ${money(tracked.entry.price * 100)} per structure.` : `Eligible ${when(tracked.eligible_at)}.`} Quantity: ${tracked.quantity}${play.legs.length > 1 ? " per leg" : ""}.`,
      );
      if (["queued", "open"].includes(tracked.state)) {
        const kind = tracked.state === "queued" ? "cancel" : "close",
          b = el(
            "button",
            kind === "cancel"
              ? "Cancel queued selection"
              : "Close PAPER tracking",
          );
        b.onclick = async () => {
          b.disabled = true;
          try {
            await api(`${path}/action`, { action: kind });
            await detail(ticket, id, true);
          } catch (e) {
            note.textContent = e.message;
            b.disabled = false;
          }
        };
        status.append(b);
      }
      app.append(status);
      const exit = data.chart?.exit_status,
        plan = data.chart?.exit_plan || play.exit_plan;
      if (exit || plan) {
        const box = panel(
          exit ? exitTitle[exit.state] : "Exit plan",
          exit
            ? `${exit.text}. Now ${exit.return >= 0 ? "+" : "−"}${Math.abs(100 * exit.return).toFixed(0)}% on the money at risk · ${exit.days_left} days to expiry${exit.chance_from_here == null ? "" : ` · chance of a profit from here ${Math.round(100 * exit.chance_from_here)}%${exit.chance_at_entry == null ? "" : ` (${Math.round(100 * exit.chance_at_entry)}% at entry)`}`}.`
            : "The plan this play was identified with.",
        );
        box.classList.add("play-exit-status", `state-${exit?.state || "plan"}`);
        const block = KestrelPlays.exitBlock(play, plan);
        if (block) box.append(block);
        app.append(box);
      }
    }
    const chart = data.chart ? KestrelPlaysHistory.chart(data.chart) : null;
    if (chart) app.append(chart);
    else
      app.append(
        panel(
          "Entry pending",
          "Your P/L path begins when a fresh market-hours entry reference is captured. No price or history is invented while waiting.",
        ),
      );
    const context = el("details", undefined, "play-context");
    context.append(
      el("summary", "Original play, probabilities & Greeks"),
      play.recovered_sighting
        ? panel(
            "Recovered original sighting",
            "The original contracts, entry and first-seen time were saved. The original rank and model probabilities were not retained, so they are unavailable here.",
          )
        : KestrelPlays.card(play),
    );
    context.open = !personal;
    app.append(context);
    const legs = el("section");
    legs.append(
      el("h2", "Exact contracts"),
      table(
        ["Action", "Contract", "Strike", "Expiry", "Idea bid / ask"],
        play.legs.map((l) => [
          l.side.toUpperCase(),
          l.occ,
          money(l.strike),
          l.expiration,
          `${money(l.bid)} / ${money(l.ask)}`,
        ]),
      ),
      el(
        "p",
        "The calculator opens these exact contracts and sides using the dated idea snapshot. Its scenario curve models possible outcomes; the history chart above uses stored observations.",
        "caption muted",
      ),
    );
    app.append(legs);
    let polling = false;
    if (!tracked || ["queued", "open", "closing"].includes(tracked.state)) {
      const timer = setInterval(async () => {
        if (ticket !== generation || serial !== viewSerial) {
          clearInterval(timer);
          return;
        }
        if (
          document.hidden ||
          document.querySelector("dialog[open]") ||
          polling
        )
          return;
        polling = true;
        try {
          const updated = await api(path);
          if (ticket !== generation || serial !== viewSerial) return;
          if (tracked && updated.tracking.state !== tracked.state)
            return detail(ticket, id, true);
          if (chart && updated.chart) chart.updateData(updated.chart);
          note.textContent = `Saved observations checked ${when(new Date().toISOString())}. Source collection runs separately.`;
        } catch (e) {
          note.textContent = `${e.message} Existing chart observations are preserved.`;
        } finally {
          polling = false;
        }
      }, 15000);
      watcher = timer;
    }
  }
  return { trackButton, targets, greeks, detail, mine };
})();
