"use strict";
// My Schwab account: the user's REAL positions, read only, with KESTREL's own view of each, and closed real trades
// graded against random twins. This app places no orders; nothing here is an instruction to trade.
const KestrelAccount = (() => {
  const pct = (x, digits = 0) =>
    x === null || x === undefined
      ? "—"
      : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(100 * x).toFixed(digits)}%`;
  const chance = (x) =>
    x === null || x === undefined ? "—" : `${Math.round(100 * x)}%`;
  const stamp = (value) =>
    value
      ? new Date(value).toLocaleString("en-US", {
          timeZone: "America/New_York",
        }) + " ET"
      : "never";
  const STATES = {
    ok: "Read successfully",
    not_enabled: "Account access not enabled",
    signed_out: "Not signed in",
    error: "Schwab did not answer",
  };
  function banner(data) {
    const note = el("div", undefined, "account-real");
    note.setAttribute("role", "note");
    note.append(el("strong", "Real account · read only"), el("p", data.label));
    return note;
  }
  function stateLine(data) {
    const box = el("div", undefined, "account-state");
    const state = data.state;
    if (state) {
      const line = el(
        "p",
        `${STATES[state.outcome] || state.outcome} · ${stamp(state.at)}`,
        state.outcome === "ok" ? "muted" : "error",
      );
      line.dataset.outcome = state.outcome;
      box.append(line);
      if (state.detail) box.append(el("p", state.detail, "account-detail"));
    } else
      box.append(
        el(
          "p",
          "Not read yet. The first read happens at the next scheduled time, or choose Refresh now.",
          "muted",
        ),
      );
    if (data.taken_at)
      box.append(
        el("p", `Positions as of ${stamp(data.taken_at)} (Schwab).`, "muted"),
      );
    box.append(el("p", data.schedule || "", "muted"));
    const refresh = el("button", "Refresh now");
    refresh.type = "button";
    refresh.id = "account-refresh";
    const message = el("span", undefined, "muted");
    message.setAttribute("role", "status");
    refresh.onclick = async () => {
      refresh.disabled = true;
      message.textContent = " Reading your account…";
      try {
        const value = await api("account/refresh", {});
        render(value);
      } catch (error) {
        message.textContent = ` ${error.message}`;
        refresh.disabled = false;
      }
    };
    box.append(refresh, message);
    return box;
  }
  // Phone width shows each row as a card; each cell carries its column name (account.css).
  function cards(t) {
    t.classList.add("wrap-cells", "account-cards");
    const names = [...t.querySelectorAll("thead th")].map((h) => h.textContent);
    for (const row of t.querySelectorAll("tbody tr"))
      [...row.children].forEach((cell, i) => (cell.dataset.label = names[i]));
    return t;
  }
  function positionCell(p) {
    const box = el("div", undefined, "account-position");
    const name =
      p.asset_type === "OPTION"
        ? `${p.stock} ${p.expiration} $${number(p.strike)} ${(p.put_call || "").toLowerCase()}`
        : p.symbol;
    box.append(link(name, `#stock/${encodeURIComponent(p.stock)}`));
    box.append(
      el("small", `${p.asset_type.toLowerCase().replaceAll("_", " ")}`),
    );
    return box;
  }
  function viewCell(p) {
    const box = el("div", undefined, "account-view");
    box.append(el("p", p.committee.text, "account-committee"));
    const o = p.option;
    if (!o) return box;
    if (o.iv_rank !== null && o.iv_rank !== undefined)
      box.append(el("p", `IV rank ${Math.round(o.iv_rank)} of 100`, "muted"));
    if (o.chance_profit !== undefined)
      box.append(
        el(
          "p",
          `Chance of a profit at expiry from your average price: ${chance(o.chance_profit)}; from today's price: ${chance(o.chance_from_here)} (${o.chance_basis}). Now ${pct(o.current_return)} on the money at risk.`,
        ),
      );
    const plan = o.plan;
    if (plan) {
      const tp = plan.take_profit,
        sl = plan.stop,
        parts = [];
      if (tp)
        parts.push(
          `take profit at ${pct(tp.return)}${tp.stock?.above ? ` (stock about $${number(tp.stock.above)} today)` : tp.stock?.below ? ` (stock about $${number(tp.stock.below)} today)` : ""}`,
        );
      if (sl)
        parts.push(
          `stop at ${pct(sl.return)}${sl.stock?.below ? ` (stock about $${number(sl.stock.below)})` : sl.stock?.above ? ` (stock about $${number(sl.stock.above)})` : ""}`,
        );
      if (plan.leave_by) parts.push(`leave by ${plan.leave_by}`);
      const line = el(
        "p",
        `Exit plan: ${parts.join(", ") || "hold to expiry"}.`,
      );
      line.className = "account-plan";
      box.append(line);
      if (plan.odds)
        box.append(
          el(
            "p",
            `Odds the take-profit comes first: ${chance(plan.odds.take_profit)}; the stop: ${chance(plan.odds.stop)}.`,
            "muted",
          ),
        );
      box.append(el("p", o.plan_state, "account-plan-state"));
      box.append(el("small", plan.why, "muted"));
    }
    if (o.note) box.append(el("p", o.note, "muted"));
    return box;
  }
  function positions(data) {
    const section = panel("Positions");
    if (!data.positions.length) {
      section.append(
        el(
          "p",
          data.taken_at
            ? "No positions in the accounts Schwab returned."
            : "No positions read yet.",
          "muted",
        ),
      );
      return section;
    }
    if (data.accounts.length)
      section.append(el("p", `Accounts: ${data.accounts.join(", ")}`, "muted"));
    const t = table(
      [
        "Position",
        "Account",
        "Quantity",
        "Average price",
        "Market value",
        "Today",
        "KESTREL's view",
      ],
      data.positions.map((p) => [
        positionCell(p),
        p.account,
        number(p.quantity),
        money(p.average_price),
        money(p.market_value),
        p.day_pnl === null ? "—" : money(p.day_pnl),
        viewCell(p),
      ]),
    );
    t.classList.add("account-table");
    section.append(cards(t));
    section.append(
      el(
        "p",
        "Option figures are per contract, from KESTREL's own models, the same as a play's. Research, not advice.",
        "muted",
      ),
    );
    return section;
  }
  function graded(data) {
    const section = panel("Your decisions graded");
    section.classList.add("account-graded");
    const g = data.graded;
    if (!g) return section;
    const summary = el("p", g.summary.text);
    summary.className =
      g.summary.strength === "early" ? "account-early" : "account-indicative";
    section.append(summary);
    if (g.summary.average !== undefined)
      section.append(
        el(
          "p",
          `Average ${pct(g.summary.average, 1)} a trade against ${pct(g.summary.twin_average, 1)} for the twins; ${Math.round(100 * g.summary.beat_share)}% beat their twin.`,
          "muted",
        ),
      );
    if (g.summary.expired_text)
      section.append(el("p", g.summary.expired_text, "muted"));
    section.append(
      el(
        "p",
        "Each closed trade is compared with a twin over the same days: a stock with another stock picked at random, an option with SPY in the option's direction.",
        "muted",
      ),
    );
    if (g.trades.length) {
      const t = table(
        ["Closed", "Position", "Days", "Your result", "Twin", "Twin result"],
        g.trades.map((r) => [
          r.closed,
          `${r.side} ${r.symbol}`,
          String(r.days),
          `${pct(r.return, 1)} (${money(r.pnl)})`,
          r.twin ? `${r.twin.symbol} · ${r.twin.basis}` : "Not available yet",
          r.twin ? pct(r.twin.return, 1) : "—",
        ]),
      );
      t.classList.add("account-trades");
      section.append(cards(t));
    }
    return section;
  }
  let container = null;
  function render(data) {
    if (!container) return;
    container.replaceChildren(banner(data));
    if (!data.enabled) {
      const off = panel(
        "Reading your account is off",
        "Nothing is requested from your Schwab account until you turn on Read my Schwab account (read-only) in Settings. It uses your existing Schwab sign-in and needs the Accounts and Trading Production product on your Schwab developer app.",
      );
      off.append(link("Open Settings", "#settings"));
      container.append(off);
      if (!data.taken_at) return;
    } else container.append(stateLine(data));
    container.append(positions(data), graded(data));
  }
  async function show(ticket) {
    heading(
      "My Schwab account",
      "Your real positions, read only, with KESTREL's view of each. This app cannot place orders.",
    );
    container = el("div", undefined, "account-page");
    app.append(container);
    const data = await api("account");
    if (ticket !== generation) return;
    render(data);
  }
  function settingsPanel() {
    const box = panel("Read my Schwab account (read-only)");
    box.id = "account-settings";
    const label = el("label", undefined, "account-switch");
    const input = el("input");
    input.type = "checkbox";
    input.id = "read-account";
    input.setAttribute("role", "switch");
    label.append(input, el("span", "Read my Schwab account (read-only)"));
    const message = el("p", undefined, "muted");
    message.setAttribute("role", "status");
    box.append(
      el(
        "p",
        "Off by default. When on, KESTREL reads your positions a few times each market day and your executed trades after the close, with the Schwab sign-in above. It never places, changes or cancels an order, and account numbers are never stored or shown (only their last four digits). Needs the Accounts and Trading Production product on your Schwab developer app; after adding it, sign in again.",
        "muted",
      ),
      label,
      message,
      link("Open My Schwab account", "#account"),
    );
    api("account")
      .then((data) => {
        input.checked = data.enabled;
      })
      .catch((error) => {
        message.textContent = error.message;
      });
    input.onchange = async () => {
      input.disabled = true;
      try {
        const data = await api("account/settings", {
          read_account: input.checked,
        });
        input.checked = data.enabled;
        message.textContent = data.enabled
          ? "On. The first read happens at the next scheduled time, or use Refresh now on My Schwab account."
          : "Off. Nothing more is requested; saved snapshots are kept.";
      } catch (error) {
        input.checked = !input.checked;
        message.textContent = error.message;
      } finally {
        input.disabled = false;
      }
    };
    return box;
  }
  return { show, settingsPanel };
})();
