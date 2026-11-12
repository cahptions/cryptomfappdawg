"use strict";
// Whole-portfolio risk (#risk): every open PAPER options position and My tracked plays added up
// (desk.stocks.risk). Unknown greeks are shown as unknown, never as zero.
const KestrelRisk = (() => {
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
  async function show(ticket) {
    const data = await api("risk");
    if (ticket !== generation) return;
    app.className = "risk-page";
    const t = data.totals || {};
    app.append(
      el("div", "PAPER · RESEARCH ONLY", "eyebrow"),
      el("h1", "Portfolio risk"),
      el(
        "p",
        `${data.paper_positions} open PAPER options position${data.paper_positions === 1 ? "" : "s"} and ${data.tracked_plays} of My tracked plays, added up.${data.unknown?.greeks ? ` ${data.unknown.greeks} with unknown greeks are left out of the totals.` : ""}`,
        "muted",
      ),
    );
    if (!data.positions) {
      app.append(
        panel(
          "Nothing open",
          "No open PAPER options positions or entered tracked plays.",
        ),
        link("Pick a play to track →", "#plays"),
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
    const each = panel("Positions");
    const rows = table(
      ["Position", "Book", "Delta (sh)", "Vega", "Theta/day", "Greeks"],
      data.rows.map((r) => [
        r.group === "Tracked"
          ? link(r.label, `#plays/mine/${encodeURIComponent(r.id)}`)
          : r.label,
        r.book,
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
