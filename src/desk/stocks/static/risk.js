"use strict";
const KestrelRisk = (() => {
  const scopes = {
    overlay: "Strategy and tracked-play overlay",
    reference_1k: "$1,000 PAPER account",
    reference_5k: "$5,000 PAPER account",
  };
  let marketMove = -5,
    volPoints = 5;
  const signed = (x, digits = 0) =>
    x === null || x === undefined
      ? "unknown"
      : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toLocaleString("en-US", { maximumFractionDigits: digits })}`;
  const dollars = (x) =>
    x === null || x === undefined
      ? "unknown"
      : `${x > 0 ? "+" : x < 0 ? "−" : ""}${money(Math.abs(x))}`;
  function tile(label, value, sub) {
    const box = el("div", undefined, "risk-tile");
    box.append(
      el("div", label, "eyebrow"),
      el("strong", value, value === "unknown" ? "unknown" : ""),
    );
    if (sub) box.append(el("small", sub, "muted"));
    return box;
  }
  async function show(ticket, route) {
    const account = route.split("/")[1];
    const scope = Object.hasOwn(scopes, account) ? account : "overlay";
    const query = new URLSearchParams({
      scope,
      market_move: marketMove,
      vol_points: volPoints,
    });
    const data = await api(`risk?${query}`);
    if (ticket !== generation) return;
    app.className = "risk-page";
    const t = data.totals || {};
    app.append(
      el("div", "PAPER · RESEARCH ONLY", "eyebrow"),
      el("h1", "Portfolio risk"),
      el(
        "p",
        data.account
          ? "Stress the open options in one simulated account."
          : "Inspect the open options across independent strategy books and tracked plays.",
      ),
    );
    const destination = data.account
      ? link("Open PAPER book", `#learning/${data.account}`)
      : link("Open Portfolio lab", "#portfolio");
    const form = el("form", undefined, "panel risk-controls");
    const selector = el("select");
    for (const [key, label] of Object.entries(scopes))
      selector.append(new Option(label, key));
    selector.value = scope;
    selector.onchange = () => {
      location.hash =
        selector.value === "overlay" ? "risk" : `risk/${selector.value}`;
    };
    const field = (label, id, input) => {
      const node = el("div", undefined, "risk-field");
      const caption = el("label", label);
      input.id = id;
      caption.htmlFor = id;
      node.append(caption, input);
      return node;
    };
    function shockInput(value, min, max) {
      const input = el("input");
      Object.assign(input, {
        type: "number",
        value,
        min,
        max,
        step: 1,
        required: true,
      });
      return input;
    }
    const move = shockInput(marketMove, -25, 25),
      vol = shockInput(volPoints, -20, 40);
    const apply = el("button", "Apply stress", "primary");
    form.append(
      field("Book", "risk-book", selector),
      field("SPY move (%)", "risk-spy-move", move),
      field("IV change (points)", "risk-vol-points", vol),
      apply,
    );
    form.onsubmit = async (event) => {
      event.preventDefault();
      marketMove = move.valueAsNumber;
      volPoints = vol.valueAsNumber;
      await render();
      if (ticket + 1 === generation && route === location.hash.slice(1))
        document.getElementById("risk-joint")?.focus({ preventScroll: true });
    };
    const summary = el("section", undefined, "panel risk-joint");
    const joint = data.joint_scenario;
    summary.id = "risk-joint";
    summary.tabIndex = -1;
    summary.append(
      el("h2", "Combined market and volatility shock"),
      el(
        "p",
        `SPY ${signed(joint.market_move * 100)}% and volatility ${signed(joint.vol_points)} points.`,
      ),
    );
    const stats = el("div", undefined, "risk-joint-stats");
    stats.append(
      tile("Modelled change", dollars(joint.change)),
      tile(
        "Options repriced",
        `${joint.covered_positions} of ${data.positions}`,
      ),
    );
    if (data.account) {
      stats.append(
        tile(
          "Ledger equity",
          data.equity == null ? "unknown" : money(data.equity),
        ),
        tile(
          "Impact on equity",
          joint.equity_fraction == null
            ? "unknown"
            : `${signed(joint.equity_fraction * 100, 2)}%`,
        ),
      );
    }
    summary.append(stats);
    if (data.other_positions || data.unknown.shock)
      summary.append(
        el(
          "p",
          `${data.unknown.shock} option positions could not be repriced and ${data.other_positions} non-option positions are outside the stress model.`,
          "muted",
        ),
      );
    app.append(destination, form, summary);
    if (!data.positions) {
      app.append(
        panel(
          "Nothing open",
          data.account
            ? "This account has no open option positions."
            : "No open PAPER options positions or entered tracked plays.",
        ),
      );
      return;
    }
    const tiles = el("section", undefined, "risk-tiles");
    tiles.id = "risk-totals";
    tiles.append(
      tile(
        "Delta",
        `${signed(t.spy_equivalent_shares, 1)} SPY sh`,
        `${dollars(t.spy_dollars_per_1pct)} per 1% SPY move · ${signed(t.delta_shares)} shares, ${dollars(t.delta_dollars)} across names`,
      ),
      tile("Vega", dollars(t.vega_per_point), "per 1 volatility point"),
      tile("Theta", dollars(t.theta_per_day), "per day"),
      tile(
        "Largest name",
        data.largest_name
          ? `${data.largest_name.name} · ${percent(data.largest_name.share)}`
          : "unknown",
        "of gross dollar delta",
      ),
      tile(
        "Largest sector",
        data.largest_sector
          ? `${data.largest_sector.name} · ${percent(data.largest_sector.share)}`
          : "unknown",
        "of gross dollar delta",
      ),
    );
    app.append(tiles);
    const shocks = panel(
      "Scenarios (modelled)",
      `Each position repriced with Black-Scholes; stocks move by their beta to SPY.${data.unknown?.shock ? ` ${data.unknown.shock} position${data.unknown.shock === 1 ? "" : "s"} without a known volatility are left out.` : ""}`,
    );
    shocks.id = "risk-scenarios";
    shocks.append(
      table(
        ["Scenario", "Change in value"],
        data.scenarios.map((s) => [s.name, dollars(s.change)]),
      ),
    );
    const weeks = panel("Expiries by week");
    weeks.append(
      table(
        ["Week of", "Positions", "Contracts"],
        data.expiries.map((w) => [w.week_of, w.positions, w.contracts]),
      ),
    );
    const grid = el("div", undefined, "risk-grid");
    grid.append(shocks, weeks);
    app.append(grid);
    const each = panel(
      "Positions",
      data.unknown?.greeks
        ? `${data.unknown.greeks} positions have incomplete Greeks. The totals include the values that could be computed.`
        : undefined,
    );
    const rows = table(
      [
        "Position",
        "Book",
        "Shock P/L",
        "Stock close",
        "Delta (sh)",
        "Vega",
        "Theta/day",
        "Greeks",
      ],
      [...data.rows]
        .sort((a, b) => (a.combined ?? Infinity) - (b.combined ?? Infinity))
        .map((r) => [
          r.group === "Tracked"
            ? link(r.label, `#plays/mine/${encodeURIComponent(r.id)}`)
            : r.label,
          r.book,
          dollars(r.combined),
          r.price_day ? `${money(r.price)} · ${r.price_day}` : "unknown",
          signed(r.delta_shares, 1),
          dollars(r.vega),
          dollars(r.theta),
          `${r.basis}${r.beta_assumed ? " · beta taken as 1" : ""}`,
        ]),
    );
    rows.classList.add("wrap-cells");
    each.append(rows);
    app.append(each);
    for (const note of data.notes || []) app.append(el("p", note, "muted"));
  }
  return { show };
})();
