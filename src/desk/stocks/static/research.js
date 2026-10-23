"use strict";
const KestrelResearch = (() => {
  let chartId = 0;
  const chartChoices = new Map();
  const compact = (value) =>
    value == null
      ? "—"
      : new Intl.NumberFormat("en-US", {
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(value);
  const shortDate = (value) =>
    value
      ? new Date(value + "T12:00:00Z").toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          timeZone: "UTC",
        })
      : "Not available";
  const dateOf = (ordinal) =>
    new Date((ordinal - 719163) * 86400000).toISOString().slice(0, 10);
  const tone = (value) =>
    value == null || value === 0
      ? "neutral"
      : value > 0
        ? "positive"
        : "negative";
  const requestedChains = new Map();
  const change = (value) =>
    value == null ? "—" : `${value > 0 ? "+" : ""}${percent(value)}`;
  function badge(text, className = "") {
    return el("span", text, "research-badge " + className);
  }
  function sectionTitle(title, caption, href) {
    const row = el("div", undefined, "section-heading"),
      titles = el("div");
    titles.append(el("h2", title));
    if (caption) titles.append(el("p", caption, "muted caption"));
    row.append(titles);
    if (href) row.append(link("View all ↗", href));
    return row;
  }
  function empty(title, message, href = "#settings", action = "Connect data") {
    const box = el("div", undefined, "research-empty");
    box.append(
      el("span", "⌁", "empty-mark"),
      el("h3", title),
      el("p", message, "muted"),
    );
    if (href) box.append(link(action, href));
    return box;
  }
  function researchReadiness() {
    const progress = snapshot.price_progress || {},
      running = snapshot.worker_running || snapshot.collection?.running,
      sessions = progress.sessions || 0,
      minimum = progress.minimum_sessions || 60;
    if (!snapshot.source_keys.MASSIVE_API_KEY)
      return {
        title: "Connect stock history",
        message:
          "Add a free Massive Stocks Basic key in Settings to download the history used by these lists.",
        href: "#settings",
        action: "Set up stock history",
      };
    const history =
      sessions < minimum
        ? `${number(sessions)} price sessions downloaded. Scoring needs at least ${minimum} real observations per eligible stock. These are historical downloads, not days you must wait.`
        : `${number(sessions)} price sessions downloaded. The first research lists have not been computed yet; each stock must also meet history and liquidity requirements.`;
    return {
      title:
        sessions < minimum
          ? "Downloading research history"
          : "First research lists pending",
      message: `${history} ${running ? "The collector is running; lists appear after analysis completes. Check System for source errors." : "PAPER and collection are stopped. Use Start PAPER above to continue from saved data."}`,
      href: running ? "#system" : "#settings",
      action: running ? "View collection status" : "Open research controls",
    };
  }
  function svgElement(tag, attributes = {}) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attributes))
      node.setAttribute(key, String(value));
    return node;
  }
  function spark(history, value) {
    const svg = svgElement("svg", {
      viewBox: "0 0 100 30",
      class: `spark ${tone(value)}`,
      "aria-hidden": "true",
    });
    const points = (history || []).filter((p) => Number.isFinite(p.close));
    if (points.length < 2) return svg;
    const lo = Math.min(...points.map((p) => p.close)),
      range = Math.max(...points.map((p) => p.close)) - lo || 1;
    const first = Date.parse(points[0].day),
      span = Date.parse(points.at(-1).day) - first || 1;
    let path = "";
    points.forEach((p, i) => {
      path += `${i && !p.gap_before ? "L" : "M"}${(((Date.parse(p.day) - first) / span) * 100).toFixed(2)},${(28 - ((p.close - lo) / range) * 26).toFixed(2)}`;
    });
    svg.append(svgElement("path", { d: path }));
    return svg;
  }
  function symbolCell(row, card = {}) {
    const cell = el("div", undefined, "symbol-cell"),
      monogram = el("span", row.symbol.slice(0, 2), "symbol-monogram"),
      name = el("div");
    cell.title = row.reason || card.name || row.symbol;
    name.append(
      link(row.symbol, `#stock/${encodeURIComponent(row.symbol)}`),
      el("small", card.name || "US equity"),
    );
    cell.append(monogram, name);
    return cell;
  }
  function stocksTable(rows, cards, limit = 10, activityRank = false) {
    const entries = rows.slice(0, limit).map((r, i) => {
      const card = cards[r.symbol] || {},
        ch = el("div", change(card.change), tone(card.change));
      if (card.previous_day && !card.consecutive_sessions)
        ch.append(
          el("small", `since ${shortDate(card.previous_day)}`, "muted"),
        );
      const signal = el("div", undefined, "row-signal");
      const displayedScore = activityRank ? r.value : card.score;
      if (displayedScore != null)
        signal.append(badge(number(displayedScore), "score-badge"));
      signal.append(
        el(
          "small",
          activityRank
            ? "Activity, not direction"
            : card.verdict || "Research list",
        ),
      );
      return [
        el("span", String(i + 1).padStart(2, "0"), "rank"),
        symbolCell(r, card),
        money(card.price ?? r.price),
        ch,
        spark(card.history, card.change),
        signal,
      ];
    });
    const result = table(
      [
        "#",
        "Company",
        "Stored close",
        "Change",
        "Price history",
        activityRank ? "Activity / 100" : "Committee",
      ],
      entries,
    );
    result.classList.add("market-table");
    return result;
  }
  function listCard(id, title, subtitle, hub, option = false) {
    const list = snapshot.lists.find((l) => l.id === id),
      box = el(
        "section",
        undefined,
        `collection-card ${option ? "option-card" : ""}`,
      );
    box.append(
      el("div", option ? "OPTIONS RESEARCH" : "STOCK RESEARCH", "eyebrow"),
    );
    const titleLink = link(title, `#lists/${id}`);
    titleLink.className = "collection-title";
    box.append(titleLink, el("p", subtitle, "muted"));
    if (list?.rows.length) {
      for (const row of list.rows.slice(0, 3)) {
        const card = hub.stocks[row.symbol] || {},
          item = el("div", undefined, "collection-row");
        item.append(
          link(row.symbol, `#stock/${encodeURIComponent(row.symbol)}`),
        );
        const value = el(
          "span",
          option ? money(row.price) : change(card.change),
          option ? "muted" : tone(card.change),
        );
        if (option)
          value.title =
            "Underlying reference recorded with the options snapshot";
        item.append(value);
        box.append(item);
      }
    } else
      box.append(
        el(
          "p",
          list?.empty_reason ||
            (!list?.asof
              ? "Research not computed yet"
              : option
                ? "Waiting for qualifying chain snapshots"
                : "No qualifying names recorded"),
          "collection-wait",
        ),
      );
    const foot = el("div", undefined, "collection-footer");
    foot.append(
      el("span", list?.asof ? `${list.rows.length} names` : "Not computed"),
      link("Explore ↗", `#lists/${id}`),
    );
    box.append(foot);
    return box;
  }
  function optionsCollections(hub) {
    const section = el("section", undefined, "hub-options");
    section.append(
      sectionTitle(
        "Options intelligence",
        "Collected chain snapshots · coverage varies",
        "#options",
      ),
    );
    const grid = el("div", undefined, "collection-grid options-grid");
    for (const [id, title, text] of [
      [
        "unusual_options",
        "Unusual activity",
        "Contracts trading above their observed normal volume.",
      ],
      [
        "cheap_iv",
        "Relative IV value",
        "Implied volatility compared with realized movement.",
      ],
      [
        "big_moves",
        "Expected moves",
        "Near-term swings priced into at-the-money straddles.",
      ],
    ])
      grid.append(listCard(id, title, text, hub, true));
    section.append(grid);
    return section;
  }
  async function home(ticket, focus) {
    const hub = await api("home");
    if (ticket !== generation) return;
    app.className = "research-home";
    const top = el("div", undefined, "hub-title"),
      title = el("div");
    title.append(
      el("div", "EQUITIES / OPTIONS / RESEARCH", "eyebrow"),
      el("h1", focus === "options" ? "Options overview" : "Market overview"),
      el(
        "p",
        "What’s moving. What’s changing. What the evidence says.",
        "muted",
      ),
    );
    const day = el("div", undefined, "session-label");
    day.append(
      badge(focus === "options" ? "OPTIONS SNAPSHOTS" : "DAILY CLOSE DATA"),
      el(
        "span",
        focus === "options"
          ? `Options session · ${shortDate(snapshot.options_research?.asof)} · Stock research · ${shortDate(snapshot.asof)}`
          : snapshot.asof
            ? `Research session · ${shortDate(snapshot.asof)}`
            : "Awaiting the first research session",
        "muted caption",
      ),
    );
    top.append(title, day);
    app.append(top);
    if (snapshot.research_preview?.awaiting_newer_close) {
      app.append(
        el(
          "p",
          `Showing published research through ${snapshot.research_preview.asof}. ` +
            `Closing prices for ${snapshot.research_preview.expected_asof} are not stored yet. ` +
            "PAPER still waits for its required session.",
          "muted caption",
        ),
      );
    }
    const benchmark = el("section", undefined, "benchmark-strip");
    benchmark.setAttribute("aria-label", "Market benchmarks");
    for (const item of hub.benchmarks) {
      const card = link("", `#stock/${item.symbol}`);
      card.className = "benchmark-card";
      const head = el("div", undefined, "benchmark-label");
      head.append(el("span", item.name), el("small", item.symbol));
      const body = el("div", undefined, "benchmark-price");
      body.append(
        el("strong", money(item.price)),
        el("span", change(item.change), tone(item.change)),
      );
      card.append(head, body, spark(item.history, item.change));
      card.title = item.asof
        ? `${item.symbol} ETF proxy · stored close ${item.asof}`
        : "No stored price history";
      benchmark.append(card);
    }
    app.append(benchmark);
    const suggestions = document.getElementById("stock-symbols");
    suggestions.replaceChildren();
    for (const [symbol, card] of Object.entries(hub.stocks)) {
      const o = el("option", card.name || symbol);
      o.value = symbol;
      suggestions.append(o);
    }
    if (!snapshot.asof || !snapshot.worker_running) {
      const setup = el("div", undefined, "setup-strip");
      const progress = snapshot.price_progress || {},
        keyed = snapshot.source_keys.MASSIVE_API_KEY;
      const message = !keyed
        ? "Stock prices are not connected. Add a free Massive Stocks Basic key in Settings; Schwab can wait."
        : !snapshot.asof
          ? researchReadiness().message
          : `${number(progress.sessions)} price sessions stored · latest close ${progress.latest}. Research updates as new observations arrive.`;
      setup.append(
        el("div", message),
        link("Set up data sources ↗", "#settings"),
        collectionControls(),
      );
      app.append(setup);
    }
    if (focus === "options") app.append(optionsCollections(hub));
    const layout = el("div", undefined, "hub-layout"),
      main = el("div", undefined, "hub-main"),
      aside = el("aside", undefined, "hub-aside");
    const movers = el("section", undefined, "movers-section"),
      tabs = el("div", undefined, "research-tabs"),
      content = el("div");
    const choices =
      focus === "options"
        ? [
            ["options_activity", "Options activity"],
            ["unusual_options", "Volume ratios"],
            ["cheap_iv", "Relative IV"],
            ["big_moves", "Expected moves"],
          ]
        : [
            ["market_activity", "Market & options"],
            ["options_activity", "Options activity"],
            ["trending", "Social attention"],
            ["momentum", "Rising trends"],
            ["gainers", "Top gainers"],
            ["losers", "Top losers"],
          ];
    let selected = choices[0][0];
    const composite = snapshot.lists.find((l) => l.id === "market_activity"),
      observedOptions = snapshot.lists.find((l) => l.id === "options_activity");
    if (
      focus !== "options" &&
      observedOptions?.rows.length &&
      (!composite?.rows.length || observedOptions.asof > composite.asof)
    )
      selected = "options_activity";
    function showMovers() {
      content.replaceChildren();
      const list = snapshot.lists.find((l) => l.id === selected);
      const names = {
        market_activity: "Market & options top 10",
        options_activity: "Options activity",
        trending: "Social attention top 10",
        momentum: "Rising trends",
        gainers: "Top 10 gainers",
        losers: "Top 10 losers",
        unusual_options: "High option volume ratios",
        cheap_iv: "Relative IV value",
        big_moves: "Expected moves",
      };
      content.append(
        sectionTitle(
          names[selected],
          list?.blurb || "Stored daily research",
          `#lists/${selected}`,
        ),
      );
      if (list?.rows.length) {
        if (focus === "options" || selected === "options_activity") {
          const researchRows = list.rows
            .slice(0, 10)
            .map((row, index) => [
              String(index + 1).padStart(2, "0"),
              symbolCell(row, hub.stocks[row.symbol]),
              el("p", row.reason, "options-reason"),
            ]);
          content.append(
            table(["#", "Underlying", "Research signal"], researchRows),
          );
        } else {
          content.append(
            stocksTable(
              list.rows,
              hub.stocks,
              10,
              selected === "market_activity",
            ),
          );
          if (selected === "market_activity") {
            for (const row of list.rows.slice(0, 10))
              content.append(
                el("p", `${row.symbol}: ${row.reason}`, "muted caption"),
              );
          }
        }
      } else if (!list?.asof) {
        const readiness = list?.options_clock
          ? {
              title: "Options collection pending",
              message: list.empty_reason,
              href: "#system",
              action: "View collection status ↗",
            }
          : researchReadiness();
        content.append(
          empty(
            readiness.title,
            readiness.message,
            readiness.href,
            readiness.action,
          ),
        );
      } else
        content.append(
          empty(
            "No qualifying names yet",
            list.empty_reason ||
              (selected === "trending"
                ? "Trending combines observed attention and trading activity. Missing coverage stays missing."
                : [
                      "market_activity",
                      "options_activity",
                      "unusual_options",
                    ].includes(selected)
                  ? `Options coverage: ${list.coverage?.same_session_options ?? "unknown"} same-session summaries; ${list.coverage?.sampled_contracts ?? "unknown"} sampled contracts; ${list.coverage?.quality_pass ?? "unknown"} underlyings pass quote/liquidity gates. ${Object.entries(
                      list.coverage?.excluded || {},
                    )
                      .map(
                        ([reason, count]) =>
                          `${count} ${reason.replaceAll("_", " ")}`,
                      )
                      .join(
                        "; ",
                      )}. Open a stock to prioritize its chain, or see System for per-symbol details.`
                  : "The saved research list has no qualifying stocks for this session."),
            "#lists",
            "Explore other lists",
          ),
        );
      const note = el("div", undefined, "table-note");
      note.append(
        el(
          "span",
          list?.asof
            ? `${list.options_clock ? "Options snapshot" : list.preview ? "Current research preview" : "Recorded list"}: ${list.asof}${list.awaiting_newer_session ? " · awaiting a newer session" : ""} · up to 10 qualified names`
            : "First research list pending",
        ),
        el(
          "span",
          list?.options_clock
            ? "Dated option snapshots; benchmarks use stored stock closes"
            : "Prices are the latest stored close",
        ),
      );
      content.append(note);
      for (const button of tabs.children)
        button.setAttribute(
          "aria-selected",
          String(button.dataset.list === selected),
        );
    }
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "Market movers");
    for (const [id, label] of choices) {
      const button = el("button", label);
      button.dataset.list = id;
      button.setAttribute("role", "tab");
      button.onclick = () => {
        selected = id;
        showMovers();
      };
      tabs.append(button);
    }
    movers.append(tabs, content);
    showMovers();
    main.append(movers);
    const opportunities = el("section", undefined, "opportunity-section");
    opportunities.append(
      sectionTitle(
        "Find your next research idea",
        "Different questions. Different evidence.",
        "#lists",
      ),
    );
    const collections = el("div", undefined, "collection-grid");
    for (const [id, title, text] of [
      ["undervalued", "Undervalued", "Valuation with a quality floor."],
      [
        "quality_fair",
        "Quality, fairly priced",
        "Strong businesses at measured prices.",
      ],
      ["squeeze", "Squeeze watch", "Short positioning meets price pressure."],
      ["insider_buying", "Insiders buying", "Reported open-market purchases."],
      ["new_highs", "At new highs", "Names approaching their 52-week highs."],
      [
        "value_traps",
        "Look beneath the price",
        "Cheap names with financial warning signs.",
      ],
    ])
      collections.append(listCard(id, title, text, hub));
    opportunities.append(collections);
    main.append(opportunities);
    if (focus !== "options") main.append(optionsCollections(hub));
    const pulse = el("section", undefined, "sidebar-section");
    pulse.append(sectionTitle("Research pulse", "Latest scored universe"));
    const breadth = hub.breadth,
      observed = breadth.observed || 0;
    const counts = el("div", undefined, "breadth-counts");
    for (const [key, label, color] of [
      ["advancing", "Advancing", "positive"],
      ["declining", "Declining", "negative"],
    ]) {
      const item = el("div");
      item.append(
        el("strong", observed ? number(breadth[key] || 0) : "—", color),
        el("small", label),
      );
      counts.append(item);
    }
    pulse.append(counts);
    const bar = el("progress");
    bar.max = observed || 1;
    bar.value = breadth.advancing || 0;
    bar.setAttribute("aria-label", "Share of observed stocks advancing");
    pulse.append(
      bar,
      el(
        "p",
        observed
          ? `${number(observed)} with measured daily returns · ${number(breadth.researched)} researched`
          : "Breadth appears after eligible stocks are scored.",
        "muted caption",
      ),
    );
    aside.append(pulse);
    const watch = el("section", undefined, "sidebar-section");
    watch.append(
      sectionTitle("Your watchlist", `${hub.watchlist.length} saved names`),
    );
    if (hub.watchlist.length) {
      for (const symbol of hub.watchlist.slice(0, 8)) {
        const card = hub.stocks[symbol] || {},
          row = el("div", undefined, "watch-row");
        row.append(
          link(symbol, `#stock/${symbol}`),
          spark(card.history, card.change),
          el("span", change(card.change), tone(card.change)),
        );
        watch.append(row);
      }
    } else
      watch.append(
        empty(
          "Keep a closer watch",
          "Save a stock from its research page to follow it here.",
          "#lists",
          "Find a stock ↗",
        ),
      );
    aside.append(watch);
    const catalysts = el("section", undefined, "sidebar-section");
    catalysts.append(
      sectionTitle(
        "On the horizon",
        "Estimated earnings windows",
        "#lists/earnings_week",
      ),
    );
    const events =
      snapshot.lists.find((l) => l.id === "earnings_week")?.rows || [];
    for (const row of events.slice(0, 4)) {
      const item = el("div", undefined, "catalyst-row");
      item.append(
        link(row.symbol, `#stock/${row.symbol}`),
        el("p", row.reason, "muted caption"),
      );
      catalysts.append(item);
    }
    if (!events.length)
      catalysts.append(
        el(
          "p",
          "No estimated reporting windows in the saved list.",
          "muted caption",
        ),
      );
    catalysts.append(badge("ESTIMATES, NOT CONFIRMED DATES", "quiet"));
    aside.append(catalysts);
    const sectors = el("section", undefined, "sidebar-section");
    sectors.append(sectionTitle("Sector pulse", "Daily sector ETF changes"));
    for (const item of hub.sectors) {
      const row = el("div", undefined, "sector-row");
      row.append(
        link(item.name, `#stock/${item.symbol}`),
        el("span", change(item.change), tone(item.change)),
      );
      sectors.append(row);
    }
    aside.append(sectors);
    const health = el("section", undefined, "sidebar-section hub-health");
    health.append(
      sectionTitle("Research, with context"),
      el(
        "p",
        "Eleven local desks. Every score has a reason and a data-coverage measure.",
        "muted caption",
      ),
      link("Inspect the evidence ↗", "#proof"),
      el(
        "p",
        snapshot.worker_running
          ? "● Collection worker running"
          : "○ Collection worker stopped",
        "muted caption",
      ),
      link("Source health ↗", "#system"),
    );
    aside.append(health);
    layout.append(main, aside);
    app.append(layout);
  }

  function chartWidget(
    bars,
    onInspect = () => {},
    basis = "Split-adjusted daily OHLC",
    symbol = "",
  ) {
    const wrapper = el("section", undefined, "chart-widget"),
      tools = el("div", undefined, "chart-tools"),
      ranges = el("div", undefined, "range-buttons"),
      types = el("div", undefined, "chart-types"),
      plot = el("div", undefined, "chart-plot"),
      tooltip = el("div", undefined, "chart-tooltip"),
      readout = el("div", undefined, "chart-readout"),
      axis = el("div", undefined, "chart-axis");
    let { range = "3M", kind = "Line" } = chartChoices.get(symbol) || {};
    tooltip.hidden = true;
    tooltip.id = `candle-tooltip-${++chartId}`;
    tooltip.setAttribute("role", "tooltip");
    tooltip.dataset.testid = "candle-tooltip";
    const periods = {
      "1M": 30,
      "3M": 90,
      "6M": 180,
      "1Y": 365,
      "5Y": 1826,
      Max: Infinity,
    };
    function draw() {
      plot.replaceChildren();
      tooltip.hidden = true;
      onInspect(null);
      if (bars.length < 2) {
        plot.append(
          empty(
            "Price history is building",
            "A chart appears after at least two real daily observations are stored.",
            null,
          ),
        );
        return;
      }
      const end = bars.at(-1).day;
      let rows = bars.filter((b) => b.day >= end - periods[range]);
      // Long candle views use real calendar OHLC aggregation, rather than thousands of overlapping bodies.
      if (kind === "Candles" && rows.length > 520) {
        const monthly = rows.length > 2000,
          grouped = new Map();
        for (const row of rows) {
          const d = new Date(dateOf(row.day) + "T12:00:00Z");
          const key = monthly
            ? dateOf(row.day).slice(0, 7)
            : row.day - ((d.getUTCDay() + 6) % 7);
          const prior = grouped.get(key);
          if (prior) {
            prior.day = row.day;
            prior.high = Math.max(prior.high, row.high);
            prior.low = Math.min(prior.low, row.low);
            prior.close = row.close;
            prior.volume += row.volume || 0;
            prior.gap_before ||= row.gap_before;
          } else
            grouped.set(key, {
              ...row,
              start_day: row.day,
              interval: monthly ? "Monthly" : "Weekly",
              previous_close: bars[bars.indexOf(row) - 1]?.close,
            });
        }
        rows = [...grouped.values()];
      }
      const observationLabel = rows[0]?.interval
        ? `${rows[0].interval} candles · aggregated from daily OHLC`
        : basis;
      const axisDate = (value) =>
        new Date(dateOf(value) + "T12:00:00Z").toLocaleDateString("en-US", {
          month: "short",
          ...(end - rows[0]?.day > 365
            ? { year: "numeric" }
            : { day: "numeric" }),
          timeZone: "UTC",
        });
      if (rows.length < 2) {
        plot.append(el("p", "Not enough observations in this range.", "muted"));
        return;
      }
      const lo = Math.min(
          ...rows.map((r) => (kind === "Candles" ? r.low : r.close)),
        ),
        hi = Math.max(
          ...rows.map((r) => (kind === "Candles" ? r.high : r.close)),
        ),
        padding = (hi - lo) * 0.09 || Math.abs(hi) * 0.01 || 1,
        min = lo - padding,
        max = hi + padding;
      const width = 1000,
        height = 320,
        from = rows[0].day,
        span = end - from || 1,
        x = (r) => ((r.day - from) / span) * width,
        y = (v) => 250 - ((v - min) / (max - min)) * 235;
      const svg = svgElement("svg", {
        viewBox: `0 0 ${width} ${height}`,
        preserveAspectRatio: "none",
        class: "price-chart",
        role: "img",
        "aria-label": `${kind} chart of observed daily stock prices. Use left and right arrows to inspect closes; Escape returns to latest.`,
        tabindex: "0",
        "aria-describedby": tooltip.id,
      });
      for (let i = 0; i < 4; i++) {
        const at = 10 + i * 78;
        svg.append(
          svgElement("line", {
            x1: 0,
            x2: width,
            y1: at,
            y2: at,
            class: "chart-guide",
          }),
        );
      }
      if (kind === "Line") {
        let d = "";
        rows.forEach((r, i) => {
          d += `${i && !r.gap_before ? "L" : "M"}${x(r).toFixed(2)},${y(r.close).toFixed(2)}`;
        });
        svg.append(svgElement("path", { d, class: "price-path" }));
      } else {
        const candleWidth = Math.max(
          1,
          Math.min(9, width / (rows.length * 1.8)),
        );
        for (const row of rows) {
          const color = tone(row.close - row.open);
          svg.append(
            svgElement("line", {
              x1: x(row),
              x2: x(row),
              y1: y(row.high),
              y2: y(row.low),
              class: `candle-wick ${color}`,
            }),
          );
          svg.append(
            svgElement("rect", {
              x: x(row) - candleWidth / 2,
              y: Math.min(y(row.open), y(row.close)),
              width: candleWidth,
              height: Math.max(1, Math.abs(y(row.close) - y(row.open))),
              class: `candle-body ${color}`,
            }),
          );
        }
      }
      const volume = Math.max(...rows.map((r) => r.volume || 0)) || 1;
      for (const row of rows)
        svg.append(
          svgElement("rect", {
            x: x(row) - 1,
            y: height - (50 * (row.volume || 0)) / volume,
            width: 2,
            height: (50 * (row.volume || 0)) / volume,
            class: `volume-bar ${tone(row.close - row.open)}`,
          }),
        );
      const cursor = svgElement("line", {
        x1: 0,
        x2: 0,
        y1: 0,
        y2: height,
        class: "chart-cursor",
        visibility: "hidden",
      });
      svg.append(cursor);
      let selectedIndex = rows.length - 1;
      const inspect = (index, pointer) => {
        selectedIndex = Math.max(0, Math.min(rows.length - 1, index));
        const nearest = rows[selectedIndex];
        cursor.setAttribute("x1", x(nearest));
        cursor.setAttribute("x2", x(nearest));
        cursor.setAttribute("visibility", "visible");
        readout.textContent = `${shortDate(dateOf(nearest.day))} · ${money(nearest.close)} · Volume ${compact(nearest.volume)}`;
        const previousClose = nearest.interval
            ? nearest.previous_close
            : bars[bars.indexOf(nearest) - 1]?.close,
          values = el("dl");
        for (const [label, value] of [
          ["Open", money(nearest.open)],
          ["High", money(nearest.high)],
          ["Low", money(nearest.low)],
          ["Close", money(nearest.close)],
          ["Volume", number(nearest.volume)],
          [
            nearest.gap_before
              ? "Since last observed close"
              : nearest.interval
                ? "Period change"
                : "Day change",
            previousClose
              ? change(nearest.close / previousClose - 1)
              : "Not available",
          ],
        ]) {
          values.append(el("dt", label), el("dd", value));
        }
        tooltip.replaceChildren(
          el(
            "strong",
            new Date(dateOf(nearest.day) + "T12:00:00Z").toLocaleDateString(
              "en-US",
              {
                month: "short",
                day: "numeric",
                year: "numeric",
                timeZone: "UTC",
              },
            ),
          ),
          values,
          ...(nearest.interval
            ? [
                el(
                  "small",
                  `From ${dateOf(nearest.start_day)} · ${nearest.interval} OHLC and total volume`,
                ),
              ]
            : []),
          el("small", `${basis} · USD`),
        );
        tooltip.hidden = false;
        const box = plot.getBoundingClientRect(),
          bounds = svg.getBoundingClientRect(),
          px = pointer
            ? pointer.clientX - box.left
            : (x(nearest) / width) * bounds.width,
          py = pointer
            ? pointer.clientY - box.top
            : (y(nearest.close) / height) * bounds.height;
        const tw = tooltip.offsetWidth,
          th = tooltip.offsetHeight;
        tooltip.style.left = `${Math.max(8, Math.min(box.width - tw - 8, px + tw + 20 > box.width ? px - tw - 14 : px + 16))}px`;
        tooltip.style.top = `${Math.max(8, Math.min(box.height - th - 8, py + th + 16 > box.height ? py - th - 14 : py + 16))}px`;
        onInspect(nearest, rows[0]);
      };
      const reset = () => {
        selectedIndex = rows.length - 1;
        cursor.setAttribute("visibility", "hidden");
        tooltip.hidden = true;
        readout.textContent = `${observationLabel} · volume below`;
        onInspect(null);
      };
      svg.onpointermove = (event) => {
        const bounds = svg.getBoundingClientRect(),
          target = from + ((event.clientX - bounds.left) / bounds.width) * span;
        let index = 0;
        rows.forEach((r, i) => {
          if (Math.abs(r.day - target) < Math.abs(rows[index].day - target))
            index = i;
        });
        inspect(index, event);
      };
      svg.onpointerdown = (event) => {
        svg.setPointerCapture(event.pointerId);
        svg.onpointermove(event);
      };
      svg.onpointerup = (event) => {
        if (svg.hasPointerCapture(event.pointerId))
          svg.releasePointerCapture(event.pointerId);
        if (event.pointerType === "touch") reset();
      };
      svg.onpointercancel = reset;
      svg.onpointerleave = reset;
      svg.onblur = reset;
      svg.onkeydown = (event) => {
        if (event.key === "Escape") reset();
        else if (
          ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
        ) {
          event.preventDefault();
          inspect(
            event.key === "Home"
              ? 0
              : event.key === "End"
                ? rows.length - 1
                : selectedIndex + (event.key === "ArrowLeft" ? -1 : 1),
          );
        }
      };
      plot.append(svg, tooltip);
      readout.textContent = `${observationLabel} · volume below`;
      axis.replaceChildren(
        el("span", axisDate(from)),
        el("span", axisDate(Math.round((from + end) / 2))),
        el("span", axisDate(end)),
      );
      for (const button of ranges.children)
        button.setAttribute(
          "aria-pressed",
          String(button.textContent === range),
        );
      for (const button of types.children)
        button.setAttribute(
          "aria-pressed",
          String(button.textContent === kind),
        );
    }
    for (const label of Object.keys(periods)) {
      const button = el("button", label);
      button.onclick = () => {
        range = label;
        chartChoices.set(symbol, { range, kind });
        draw();
      };
      ranges.append(button);
    }
    for (const label of ["Line", "Candles"]) {
      const button = el("button", label);
      button.onclick = () => {
        kind = label;
        chartChoices.set(symbol, { range, kind });
        draw();
      };
      types.append(button);
    }
    tools.append(ranges, types);
    wrapper.append(plot, axis, tools, readout);
    draw();
    return wrapper;
  }
  function metrics(title, entries) {
    const section = el("section", undefined, "detail-section");
    section.append(sectionTitle(title));
    const grid = el("dl", undefined, "metrics-grid");
    for (const [label, value] of entries) {
      const item = el("div");
      item.append(el("dt", label), el("dd", value));
      grid.append(item);
    }
    section.append(grid);
    return section;
  }
  async function optionChain(symbol, container, ticket, expiration) {
    const query = expiration
      ? `?expiration=${encodeURIComponent(expiration)}`
      : "";
    try {
      const data = await api(
        `stock/${encodeURIComponent(symbol)}/options${query}`,
      );
      if (ticket !== generation) return;
      let queued;
      if (Date.now() - (requestedChains.get(symbol) || 0) > 15 * 60 * 1000) {
        queued = await api(
          `stock/${encodeURIComponent(symbol)}/options/request`,
          {},
        );
        requestedChains.set(symbol, Date.now());
        if (ticket !== generation) return;
      }
      container.replaceChildren(
        sectionTitle(
          "Options chain",
          "Saved bid/ask snapshots · research only",
        ),
      );
      if (!data.contracts.length) {
        container.append(
          empty(
            "Chain collection requested",
            data.message ||
              ((queued?.running ?? snapshot.worker_running)
                ? "This stock is prioritized for the next available collection pass. The page refreshes as stored data arrives. See System for any source error."
                : "This stock is queued. Start PAPER in System to resume collection, or connect your options provider in Settings."),
            "#system",
            "View collection status ↗",
          ),
        );
        return;
      }
      const controls = el("div", undefined, "chain-controls"),
        expiry = el("select"),
        tabs = el("div", undefined, "research-tabs"),
        content = el("div");
      if (data.stale_session || data.expired)
        container.append(
          el(
            "p",
            "Archived snapshot: a newer collection is requested. These stored quotes are not current executable prices.",
            "notice",
          ),
        );
      expiry.setAttribute("aria-label", "Options expiration");
      for (const date of data.expirations) {
        const option = el("option", shortDate(date));
        option.value = date;
        expiry.append(option);
      }
      expiry.value = data.expiration;
      expiry.onchange = () =>
        optionChain(symbol, container, ticket, expiry.value);
      let kind = data.contracts.some((c) => c.kind === "call") ? "call" : "put";
      function draw() {
        const rows = data.contracts.filter((c) => c.kind === kind);
        content.replaceChildren(
          table(
            [
              "Strike",
              "Bid",
              "Ask",
              "Volume",
              "Open interest",
              "Quote time (ET)",
            ],
            rows.map((c) => [
              (() => {
                const button = el("button", money(c.strike), "strike-button");
                button.setAttribute(
                  "aria-label",
                  `Calculate profit and loss for ${symbol} ${c.strike} ${c.kind} expiring ${data.expiration}`,
                );
                button.onclick = () => KestrelCalculator.open(symbol, c, data);
                return button;
              })(),
              money(c.bid),
              money(c.ask),
              compact(c.volume),
              compact(c.open_interest),
              c.quoted_at
                ? new Date(c.quoted_at).toLocaleString("en-US", {
                    timeZone: "America/New_York",
                  })
                : "Not supplied",
            ]),
          ),
        );
        for (const b of tabs.children)
          b.setAttribute("aria-selected", String(b.dataset.kind === kind));
      }
      for (const [id, label] of [
        ["call", "Calls"],
        ["put", "Puts"],
      ]) {
        const button = el("button", label);
        button.dataset.kind = id;
        button.setAttribute("role", "tab");
        button.onclick = () => {
          kind = id;
          draw();
        };
        tabs.append(button);
      }
      tabs.setAttribute("role", "tablist");
      tabs.setAttribute("aria-label", "Option type");
      controls.append(tabs, expiry);
      container.append(
        controls,
        content,
        el(
          "p",
          `${data.source} · snapshot ${data.asof} · showing ${data.displayed_contracts} of ${data.stored_contracts} stored contracts${data.underlying_price ? " nearest the underlying price" : "; underlying price unavailable"}. Quotes may have different timestamps. No order can be placed here.`,
          "muted caption",
        ),
      );
      draw();
    } catch (error) {
      if (ticket === generation)
        container.replaceChildren(el("p", error.message, "error"));
    }
  }
  async function stock(symbol, ticket) {
    let data = await api(`stock/${encodeURIComponent(symbol)}`);
    if (ticket !== generation) return;
    app.replaceChildren();
    app.className = "stock-research";
    const d = data.analysis,
      p = d?.price || {},
      f = d?.fundamentals || {},
      chartHost = el("div"),
      historyStatus = el("p", "", "muted caption"),
      coverageDetails = el("details"),
      coverageInfo = el("p", "", "muted caption");
    coverageDetails.append(
      el("summary", "Chart history and quote coverage"),
      coverageInfo,
    );
    let latest = data.bars.at(-1),
      previous = data.bars.at(-2),
      inspected = false;
    function currentChange() {
      return data.quote && !data.quote.stale && data.quote.prev_close
        ? data.quote.last / data.quote.prev_close - 1
        : latest && previous?.close
          ? latest.close / previous.close - 1
          : null;
    }
    const breadcrumb = el("div", undefined, "breadcrumbs");
    breadcrumb.append(
      link("Markets", "#dashboard"),
      el("span", "/"),
      el("span", d?.sector || data.listing?.exchange || "US equities"),
      el("span", "/"),
      el("span", symbol),
    );
    app.append(breadcrumb);
    const layout = el("div", undefined, "stock-layout"),
      main = el("div", undefined, "stock-main"),
      aside = el("aside", undefined, "stock-aside"),
      top = el("div", undefined, "stock-title");
    top.append(
      el("div", symbol, "eyebrow"),
      el("h1", d?.name || data.listing?.name || symbol),
    );
    const chips = el("div", undefined, "stock-chips");
    chips.append(
      badge(data.listing?.etf ? "ETF" : "US EQUITY"),
      badge(
        data.listing?.etf
          ? "ETF market/options profile"
          : d?.sector || "Research coverage pending",
        "quiet",
      ),
    );
    top.append(chips);
    const pricing = el("div", undefined, "stock-pricing"),
      headlinePrice = el("div", money(latest?.close ?? p.close), "big"),
      headlineChange = el(
        "span",
        change(currentChange()),
        tone(currentChange()),
      ),
      priceCaption = el("p", "", "muted caption");
    headlinePrice.dataset.testid = "headline-price";
    priceCaption.dataset.testid = "price-caption";
    pricing.append(headlinePrice, headlineChange);
    const inspectPrice = (row, baseline) => {
      inspected = !!row;
      const quote = data.quote && !data.quote.stale ? data.quote : null;
      const delta =
        row && baseline?.close
          ? row.close / baseline.close - 1
          : currentChange();
      headlinePrice.textContent = money(
        row?.close ?? quote?.last ?? latest?.close ?? p.close,
      );
      headlineChange.textContent = change(delta);
      headlineChange.className = tone(delta);
      priceCaption.textContent = row
        ? `${new Date(dateOf(row.day) + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })} · Stored close · USD · Change since ${dateOf(baseline.day)}`
        : quote
          ? `Schwab quote · ${new Date(quote.quoted_at).toLocaleString("en-US", { timeZone: "America/New_York" })} ET · USD · Change vs previous close`
          : latest
            ? `Stored close · ${shortDate(dateOf(latest.day))} · USD · Change vs previous observed close`
            : "No stored price history";
    };
    inspectPrice(null);
    top.append(pricing, priceCaption);
    function updateHistoryStatus() {
      const c = data.coverage;
      historyStatus.textContent = `${data.chart_source} · ${c?.sessions || 0} daily observations${c?.first ? ` · ${c.first} through ${c.last}` : ""}${data.stock_data?.history_error ? ` · ${data.stock_data.history_error}` : ""}`;
      coverageInfo.textContent = `${data.stock_data?.max_note || "Max shows all downloaded history. Connect Schwab in Settings for longer chart history."} ${data.chart_basis}. Daily candles may include an unfinished session. Research statistics below use separately dated bulk history. ${data.quote?.stale ? `The last Schwab quote is from ${new Date(data.quote.quoted_at).toLocaleString("en-US", { timeZone: "America/New_York" })} ET; the headline shows the stored close.` : ""} ${data.stock_data?.quote_error || ""}`;
    }
    function drawHistory() {
      chartHost.replaceChildren(
        chartWidget(
          data.bars,
          inspectPrice,
          data.chart_source === "Schwab"
            ? "Schwab daily candles"
            : "Daily · split-adjusted",
          symbol,
        ),
      );
      updateHistoryStatus();
    }
    drawHistory();
    main.append(top, chartHost, historyStatus, coverageDetails);
    async function pollStock() {
      if (ticket !== generation) return;
      try {
        if (document.visibilityState === "visible") {
          await api(`stock/${encodeURIComponent(symbol)}/history/request`, {});
          const next = await api(`stock/${encodeURIComponent(symbol)}`);
          if (ticket !== generation) return;
          const changed =
            next.stock_data?.updated_at !== data.stock_data?.updated_at;
          data = next;
          latest = data.bars.at(-1);
          previous = data.bars.at(-2);
          if (changed) drawHistory();
          else {
            updateHistoryStatus();
            if (!inspected) inspectPrice(null);
          }
        }
      } catch (error) {
        if (ticket === generation)
          historyStatus.textContent =
            error.message +
            " Existing chart data was preserved. See Settings for market-data connections.";
      }
      if (ticket === generation)
        setTimeout(pollStock, data.stock_data?.updated_at ? 30000 : 5000);
    }
    pollStock();
    main.append(
      metrics(
        data.listing?.etf ? "ETF price & risk statistics" : "Key statistics",
        data.listing?.etf
          ? [
              ["1-session return", percent(p.ret_1d)],
              ["5-session return", percent(p.ret_5d)],
              ["21-session return", percent(p.ret_21d)],
              ["63-session return", percent(p.ret_63d)],
              ["20-session annualized volatility", percent(p.vol_20d)],
              [
                "Median 20-session dollar volume",
                p.dollar_volume_20d == null
                  ? "—"
                  : "$" + compact(p.dollar_volume_20d),
              ],
              ["50-session average", money(p.sma50)],
              ["200-session average", money(p.sma200)],
              ["52-week high", money(p.high_52w)],
              ["52-week low", money(p.low_52w)],
              [
                "Relative volume",
                p.rel_volume == null ? "—" : number(p.rel_volume) + "×",
              ],
              ["Stored sessions", compact(d?.coverage?.stored_sessions)],
            ]
          : [
              [
                "Market cap",
                f.market_cap == null ? "—" : "$" + compact(f.market_cap),
              ],
              ["P/E", number(f.pe)],
              ["FCF yield", percent(f.fcf_yield)],
              [
                "20D dollar volume",
                p.dollar_volume_20d == null
                  ? "—"
                  : "$" + compact(p.dollar_volume_20d),
              ],
              ["52-week high", money(p.high_52w)],
              ["52-week low", money(p.low_52w)],
              [
                "Relative volume",
                p.rel_volume == null ? "—" : number(p.rel_volume) + "×",
              ],
              ["1Y beta", number(p.beta_1y)],
            ],
      ),
    );
    if (d) {
      const rationale = el("section", undefined, "detail-section");
      rationale.append(
        sectionTitle(
          data.listing?.etf
            ? "Trend and risk observations"
            : "The investment case",
          data.listing?.etf
            ? "Calculated from stored prices; these are observations, not a forecast."
            : "What supports the view—and what challenges it",
        ),
      );
      const cases = el("div", undefined, "case-grid");
      for (const [title, items, color] of [
        [
          data.listing?.etf ? "Positive trend observations" : "Bull case",
          d.bull,
          "positive",
        ],
        [
          data.listing?.etf ? "Risk and limitations" : "Bear case",
          d.bear,
          "negative",
        ],
      ]) {
        const card = el("div", undefined, "case-card");
        card.append(el("h3", title, color));
        const list = el("ul");
        for (const text of items || []) list.append(el("li", text));
        if (!items?.length)
          list.append(
            el(
              "li",
              data.listing?.etf
                ? "No positive trend observations in the measured windows."
                : "Insufficient evidence for a case.",
            ),
          );
        card.append(list);
        cases.append(card);
      }
      rationale.append(cases);
      main.append(rationale);
    }
    const chains = el("section", undefined, "detail-section option-chain");
    chains.append(
      sectionTitle("Options chain", "Loading stored observations…"),
    );
    main.append(chains);
    const desks = el("section", undefined, "detail-section");
    desks.id = "desks";
    desks.append(
      sectionTitle(
        data.listing?.etf
          ? "ETF market evidence"
          : "Eleven perspectives. One research record.",
        "Scores reflect the stored evidence. Coverage is not a probability of profit.",
      ),
    );
    const etfValue = (id) =>
      id === "momentum"
        ? percent(p.ret_21d)
        : id === "risk"
          ? percent(p.vol_20d)
          : d?.options && Object.keys(d.options).length
            ? "Stored sample"
            : "No sample";
    const grid = el("div", undefined, "grid desk-grid");
    for (const [id, label] of Object.entries(snapshot.desks)) {
      if (data.listing?.etf && !d?.desks?.[id]) continue;
      const desk = d?.desks?.[id],
        card = el("article", undefined, "desk-card");
      card.id = `desk-${id}`;
      card.append(
        el("div", label, "desk-label"),
        el(
          "strong",
          data.listing?.etf ? etfValue(id) : number(desk?.score),
          "desk-score",
        ),
        el("p", desk?.verdict || "Awaiting data", "muted caption"),
      );
      const bar = el("progress");
      bar.max = 1;
      bar.value = desk?.confidence || 0;
      bar.setAttribute("aria-label", `${label} data coverage`);
      card.append(
        bar,
        el("small", `${percent(desk?.confidence)} data coverage`),
      );
      const list = el("ul");
      for (const text of desk?.reasons || []) list.append(el("li", text));
      card.append(list);
      grid.append(card);
    }
    desks.append(grid);
    main.append(desks);
    if (d)
      main.append(
        details("Research inputs and methodology", {
          asof: d.asof,
          weights: d.weights,
          fundamentals: d.fundamentals,
          events: d.events,
          options: d.options,
          macro: d.macro,
        }),
      );
    const committee = el("section", undefined, "committee-card");
    committee.append(
      el(
        "div",
        data.listing?.etf ? "ETF PROFILE" : "COMMITTEE VIEW",
        "eyebrow",
      ),
    );
    const score = el("div", undefined, "committee-number");
    score.append(
      el("strong", data.listing?.etf ? "ETF" : number(d?.committee?.score)),
      el("span", data.listing?.etf ? "" : "/ 100", "muted"),
    );
    committee.append(
      score,
      el(
        "h2",
        data.listing?.etf
          ? "Market & options evidence"
          : d?.committee?.label || "Awaiting evidence",
      ),
    );
    committee.append(
      el(
        "p",
        data.listing?.etf
          ? "Company valuation, earnings and insider models do not apply to funds. This profile shows observed price, risk and options data without a company committee score."
          : "A synthesis of the desks below, weighted by the recorded methodology.",
        "muted caption",
      ),
    );
    const strip = el("div", undefined, "desk-strip");
    for (const [id, label] of Object.entries(snapshot.desks)) {
      if (data.listing?.etf && !d?.desks?.[id]) continue;
      const score = d?.desks?.[id]?.score,
        a = link("", `#desk-${id}`);
      a.className = data.listing?.etf
        ? d?.desks?.[id]?.confidence
          ? "mixed"
          : "unavailable"
        : score == null
          ? "unavailable"
          : score >= 60
            ? "favorable"
            : score <= 40
              ? "unfavorable"
              : "mixed";
      a.title = `${label}: ${data.listing?.etf ? etfValue(id) : number(score)}`;
      a.setAttribute("aria-label", a.title);
      a.onclick = (e) => {
        e.preventDefault();
        document
          .getElementById(`desk-${id}`)
          ?.scrollIntoView({ behavior: "smooth", block: "center" });
      };
      strip.append(a);
    }
    committee.append(
      strip,
      el(
        "p",
        d
          ? data.listing?.etf
            ? `ETF profile · ${d.asof}`
            : `${d.committee?.desks_counted || 0} scored desks · ${d.asof}`
          : "No eligible analysis stored",
        "muted caption",
      ),
    );
    const watch = el(
      "button",
      data.watchlisted ? "✓ In your watchlist" : "+ Add to watchlist",
      "primary watch-button",
    );
    watch.onclick = async () => {
      watch.disabled = true;
      try {
        const result = await api(
          `watch/${encodeURIComponent(symbol)}?action=${data.watchlisted ? "remove" : "add"}`,
          {},
        );
        data.watchlisted = result.watchlisted;
        watch.textContent = data.watchlisted
          ? "✓ In your watchlist"
          : "+ Add to watchlist";
      } catch (error) {
        watch.textContent = error.message;
      } finally {
        watch.disabled = false;
      }
    };
    committee.append(watch);
    aside.append(committee);
    const overview = el("section", undefined, "sidebar-section");
    overview.append(sectionTitle("Desk breakdown"));
    for (const [id, label] of Object.entries(snapshot.desks)) {
      if (data.listing?.etf && !d?.desks?.[id]) continue;
      const row = el("div", undefined, "sector-row");
      row.append(
        el("span", label),
        el(
          "strong",
          data.listing?.etf ? etfValue(id) : number(d?.desks?.[id]?.score),
        ),
      );
      overview.append(row);
    }
    aside.append(overview);
    const changeView = el("section", undefined, "sidebar-section");
    changeView.append(sectionTitle("What would change the view"));
    for (const text of d?.change_view || [])
      changeView.append(el("p", text, "muted caption"));
    if (!d?.change_view?.length)
      changeView.append(
        el(
          "p",
          "Specific invalidation conditions appear when sufficient inputs are available.",
          "muted caption",
        ),
      );
    aside.append(changeView);
    const context = el("section", undefined, "sidebar-section");
    context.append(
      sectionTitle("Keep the context"),
      el(
        "p",
        "This is stock and options research. Quotes, liquidity and your account constraints still determine whether an opportunity is executable.",
        "muted caption",
      ),
      link("Review evidence ↗", "#proof"),
    );
    aside.append(context);
    layout.append(main, aside);
    app.append(layout);
    await optionChain(symbol, chains, ticket);
  }
  return { home, stock };
})();
