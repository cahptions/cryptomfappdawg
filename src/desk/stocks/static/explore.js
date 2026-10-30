"use strict";
const KestrelExplore = (() => {
  const fmtDate = (value) => value || "Not collected yet";
  function listCard(id, title, subtitle, hub, customRows) {
    const list = snapshot.lists.find((item) => item.id === id),
      rows = customRows || list?.rows || [],
      section = el("section", undefined, "screen-card");
    const head = el("div", undefined, "screen-card-heading"),
      titles = el("div");
    titles.append(el("h2", title), el("p", subtitle, "muted caption"));
    head.append(
      titles,
      el("span", String(Math.min(10, rows.length)), "screen-count"),
    );
    section.append(head);
    const listEl = el("ol", undefined, "screen-ranked-list");
    for (const [index, row] of rows.slice(0, 10).entries()) {
      const stock = hub.stocks[row.symbol] || {},
        item = el("li"),
        name = el("div"),
        quote = el("div", undefined, "screen-quote");
      name.append(
        link(row.symbol, `#stock/${encodeURIComponent(row.symbol)}`),
        el("small", companyName(stock.name) || row.reason),
      );
      name.title = row.reason || "";
      quote.append(el("strong", money(row.price ?? stock.price)));
      if (stock.change != null)
        quote.append(
          el(
            "small",
            `${stock.change > 0 ? "+" : ""}${percent(stock.change)}`,
            stock.change >= 0 ? "positive" : "negative",
          ),
        );
      item.append(
        el("span", String(index + 1).padStart(2, "0"), "screen-rank"),
        name,
        quote,
      );
      listEl.append(item);
    }
    if (rows.length) section.append(listEl);
    else {
      section.append(
        el(
          "p",
          list?.empty_reason ||
            "No names meet this screen's rules in the stored sample. Other screens remain available.",
          "screen-empty",
        ),
      );
      section.append(link("View data coverage", "#proof"));
    }
    const footer = el("div", undefined, "screen-card-footer");
    footer.append(
      el("small", `Snapshot ${fmtDate(customRows?.[0]?.asof || list?.asof)}`),
      link("Explore list ↗", `#lists/${id}`),
    );
    if (customRows) footer.lastChild.remove();
    section.append(footer);
    return section;
  }
  function setups(data, options = {}) {
    const section = el("section", undefined, "option-research-section"),
      head = el("div", undefined, "section-heading"),
      text = el("div");
    text.append(
      el("h2", options.title || "Top option setups"),
      el(
        "p",
        "A contract shortlist with visible costs, spreads and risk. Ranked by quote quality and liquidity, not promised profit.",
        "muted",
      ),
    );
    head.append(text, link("All options research ↗", "#options"));
    section.append(head);
    section.append(
      el(
        "p",
        `${data.sampled_underlyings} recently observed underlyings screened · ${data.calls ?? data.rows.filter((r) => r.kind === "call").length} call candidates · ${data.puts ?? data.rows.filter((r) => r.kind === "put").length} put candidates · session ${data.asof}. This is a quoted-liquidity screen, separate from unusual activity or a learned prediction.`,
        "muted caption",
      ),
    );
    const controls = el("div", undefined, "screen-filters"),
      kind = el("select"),
      budget = el("input"),
      content = el("div");
    kind.setAttribute("aria-label", "Option type");
    for (const [id, label] of [
      ["all", "Calls & puts"],
      ["call", "Calls"],
      ["put", "Puts"],
    ]) {
      const o = el("option", label);
      o.value = id;
      kind.append(o);
    }
    kind.value = options.kind || "all";
    budget.type = "number";
    budget.min = "0";
    budget.placeholder = "Max cost per contract ($)";
    budget.setAttribute("aria-label", "Maximum option entry cost");
    controls.append(kind, budget);
    section.append(controls, content);
    function draw() {
      content.replaceChildren();
      const rows = data.rows
        .filter(
          (row) =>
            (kind.value === "all" || row.kind === kind.value) &&
            (!budget.value || row.entry_cost <= Number(budget.value)),
        )
        .slice(0, 10);
      if (!rows.length) {
        content.append(
          el(
            "p",
            data.rows.length
              ? "No contracts match these filters. Raise the cost limit or select both option types."
              : "No contracts currently meet this screen's quote, liquidity and expiry requirements. Open a stock to prioritize its chain; collection continues automatically.",
            "screen-empty",
          ),
        );
        content.append(link("Check options coverage", "#system"));
        return;
      }
      const grid = el("div", undefined, "setup-grid");
      for (const row of rows) {
        const card = el("article", undefined, "setup-card"),
          title = el("div", undefined, "setup-heading");
        title.append(
          link(row.symbol, `#stock/${encodeURIComponent(row.symbol)}`),
          el(
            "span",
            row.kind === "call" ? "CALL" : "PUT",
            "research-badge quiet",
          ),
        );
        const metrics = el("dl", undefined, "setup-metrics");
        for (const [label, value] of [
          ["Strike / expiry", `${money(row.strike)} · ${row.expiration}`],
          ["Cost / premium at risk", money(row.entry_cost)],
          ["Expiry break-even", money(row.breakeven)],
          ["Bid / ask", `${money(row.bid)} / ${money(row.ask)}`],
          ["Quoted spread", percent(row.spread_fraction)],
          [
            "Open interest / volume",
            `${number(row.open_interest)} / ${number(row.volume)}`,
          ],
        ])
          metrics.append(el("dt", label), el("dd", value));
        card.append(
          title,
          metrics,
          el(
            "small",
            `${row.source} · ${row.quoted_at ? new Date(row.quoted_at).toLocaleString("en-US", { timeZone: "America/New_York" }) + " ET" : row.asof}`,
          ),
          link(
            "Inspect chain & payoff ↗",
            `#stock/${encodeURIComponent(row.symbol)}`,
          ),
        );
        grid.append(card);
      }
      content.append(grid, el("p", data.basis, "muted caption"));
    }
    kind.onchange = draw;
    budget.oninput = draw;
    draw();
    return section;
  }
  async function show(ticket) {
    const [hub, data] = await Promise.all([api("home"), api("explore")]);
    if (ticket !== generation) return;
    app.className = "stocks-explorer";
    const hero = el("div", undefined, "explorer-heading"),
      titles = el("div");
    titles.append(
      el("div", "MARKET SCREENER", "eyebrow"),
      el("h1", "Stocks"),
      el(
        "p",
        "Start with the leaders. See the reasons. Research the trade.",
        "muted",
      ),
    );
    const search = el("form", undefined, "explorer-search"),
      input = el("input"),
      button = el("button", "Research stock", "primary");
    input.placeholder = "Search a ticker · AAPL, SPY, QQQ";
    input.setAttribute("aria-label", "Research a stock");
    input.maxLength = 12;
    search.append(input, button);
    search.onsubmit = (event) => {
      event.preventDefault();
      if (input.value.trim())
        location.hash = `stock/${encodeURIComponent(input.value.trim().toUpperCase())}`;
    };
    hero.append(titles, search);
    app.replaceChildren(hero);
    const shortcuts = el("div", undefined, "screen-shortcuts");
    for (const [label, target] of [
      ["Ranked stocks", "ranked-stocks"],
      ["Option setups", "option-setups"],
      ["Watchlist", "saved-stocks"],
    ]) {
      const b = el("button", label);
      b.onclick = () =>
        document
          .getElementById(target)
          ?.scrollIntoView({ behavior: "smooth", block: "start" });
      shortcuts.append(b);
    }
    shortcuts.append(link("Evidence & data health ↗", "#proof"));
    app.append(shortcuts);
    const info = el("div", undefined, "explorer-status");
    info.append(
      el("span", `${number(hub.breadth.researched || 0)} researched stocks`),
      el("span", `Stock snapshot ${fmtDate(snapshot.asof)}`),
      el("span", `Options ${fmtDate(snapshot.options_research?.asof)}`),
    );
    app.append(info);
    const grid = el("div", undefined, "screens-grid");
    grid.id = "ranked-stocks";
    grid.append(
      listCard(
        "committee",
        "Top 10 research ideas",
        "Highest combined research scores with at least six measured desks.",
        hub,
      ),
      listCard(
        "market_activity",
        "Trending: market & options",
        "Measured stock and options activity. No social score enters this ranking.",
        hub,
      ),
      listCard(
        "momentum",
        "Strong price trends",
        "Momentum leaders above their long-term moving average.",
        hub,
      ),
      listCard(
        "volume",
        "Unusual stock volume",
        "Today's activity relative to the stock's own recent trading volume.",
        hub,
        data.price_volume,
      ),
      listCard(
        "gainers",
        "Top gainers",
        "Largest observed daily gains in the eligible stock universe.",
        hub,
      ),
      listCard(
        "losers",
        "Top losers",
        "Largest observed daily declines. A decline alone is not a buying signal.",
        hub,
      ),
    );
    app.append(grid);
    const optionSection = setups(data.option_setups);
    optionSection.id = "option-setups";
    app.append(optionSection);
    const extra = el("div", undefined, "screens-grid");
    extra.append(
      listCard(
        "earnings_week",
        "Upcoming catalysts",
        "Estimated reporting windows, not confirmed earnings dates.",
        hub,
      ),
      listCard(
        "undervalued",
        "Value with a quality floor",
        "Valuation opportunities that also clear the existing quality rules.",
        hub,
      ),
    );
    app.append(extra);
    const watch = el("section", undefined, "screen-card");
    watch.id = "saved-stocks";
    watch.append(el("h2", "Your watchlist"));
    if (hub.watchlist.length)
      for (const symbol of hub.watchlist)
        watch.append(
          link(symbol, `#stock/${symbol}`),
          document.createTextNode("  "),
        );
    else
      watch.append(
        el(
          "p",
          "Open any stock and choose Add to watchlist. Saved names appear here.",
          "muted",
        ),
      );
    app.append(watch);
  }
  return { show, setups };
})();
