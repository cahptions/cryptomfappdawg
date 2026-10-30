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
  const intradayTime = (at, full = false) =>
    new Date(at).toLocaleString("en-US", {
      ...(full ? { month: "short", day: "numeric", year: "numeric" } : {}),
      hour: "numeric",
      minute: "2-digit",
      timeZone: "America/New_York",
    }) + " ET";
  const tone = (value) =>
    value == null || value === 0
      ? "neutral"
      : value > 0
        ? "positive"
        : "negative";
  const requestedChains = new Map(),
    chainPolls = new Map();
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
    const chart = KestrelCharts.spark(
      (history || []).map((p) => ({
        value: p.close,
        gap_before: p.gap_before,
      })),
      { color: value >= 0 ? "var(--up)" : "var(--down)" },
    );
    chart.classList.add("spark");
    return chart;
  }
  function symbolCell(row, card = {}) {
    const cell = el("div", undefined, "symbol-cell"),
      monogram = el("span", row.symbol.slice(0, 2), "symbol-monogram"),
      name = el("div");
    cell.title = row.reason || card.name || row.symbol;
    name.append(
      link(row.symbol, `#stock/${encodeURIComponent(row.symbol)}`),
      el("small", companyName(card.name) || "US equity"),
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
      // Activity lists: the first measured fact, with the full reason on hover.
      const fact = activityRank
        ? (r.reason || "").split(/;|\. /)[0] || "Activity, not direction"
        : card.verdict || "Research list";
      const note = el("small", fact);
      if (activityRank && r.reason) note.title = r.reason;
      signal.append(note);
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
  // Home for one person: their plays' P/L, the market and today's top ideas on one screen, then the research.
  function cockpitHome(hub, ticket) {
    const hour = new Date().getHours(),
      greeting =
        hour < 5
          ? "Late night"
          : hour < 12
            ? "Good morning"
            : hour < 18
              ? "Good afternoon"
              : "Good evening";
    const head = el("div", undefined, "cockpit-head");
    head.append(
      el("h1", greeting),
      el(
        "span",
        snapshot.asof
          ? `Research session ${shortDate(snapshot.asof)}`
          : "Awaiting the first research session",
        "muted",
      ),
    );
    const grid = el(
      "section",
      undefined,
      `cockpit${hub.cockpit?.plays?.rows?.length ? "" : " no-plays"}`,
    );
    grid.setAttribute("aria-label", "Your desk at a glance");
    grid.append(
      cockpitPlays(hub.cockpit?.plays),
      cockpitMarket(hub),
      cockpitIdeas(hub.cockpit),
    );
    const latest = hub.events?.recent?.[0];
    if (latest) {
      const line = link("", "#events");
      line.className = `cockpit-event tone-${latest.tone}`;
      line.append(
        el("span", undefined, "event-mark"),
        el("strong", latest.title),
        el("small", shortDate(latest.day), "muted"),
        el("span", "All events →", "cockpit-more"),
      );
      grid.append(line);
    }
    app.append(head, grid);
    if (snapshot.research_preview?.awaiting_newer_close)
      app.append(
        el(
          "p",
          `Showing published research through ${snapshot.research_preview.asof}. Closing prices for ${snapshot.research_preview.expected_asof} are not stored yet.`,
          "notice",
        ),
      );
    return homeBody(hub, ticket, "stocks", null);
  }
  // The oldest quote behind the P/L, so a number from yesterday never reads as live.
  function quotedAt(rows) {
    const times = rows
      .map((r) => r.quoted_at)
      .filter(Boolean)
      .sort();
    if (!times.length) return "";
    return ` as of ${new Date(times[0]).toLocaleString("en-US", {
      timeZone: "America/New_York",
      weekday: "short",
      hour: "numeric",
      minute: "2-digit",
    })} ET`;
  }
  function cockpitPlays(mine) {
    const box = el("article", undefined, "cockpit-card cockpit-plays"),
      rows = mine?.rows || [];
    const pnl = mine?.pnl;
    box.append(el("div", "YOUR PLAYS", "eyebrow"));
    const figure = el(
      "strong",
      pnl == null
        ? rows.length
          ? "—"
          : "$0.00"
        : `${pnl > 0 ? "+" : pnl < 0 ? "−" : ""}${money(Math.abs(pnl))}`,
      `cockpit-figure ${pnl > 0 ? "up" : pnl < 0 ? "down" : ""}`,
    );
    box.append(
      figure,
      el(
        "p",
        rows.length
          ? `${mine.open} open · P/L at saved quotes${quotedAt(rows)}${mine.priced < rows.length ? ` · ${rows.length - mine.priced} waiting for a quote` : ""}`
          : "No plays tracked yet.",
        "cockpit-sub",
      ),
    );
    if (!rows.length) {
      box.append(link("Pick a play to track →", "#plays"));
      return box;
    }
    const list = el("div", undefined, "cockpit-list");
    for (const r of rows) {
      const row = link("", `#plays/mine/${encodeURIComponent(r.id)}`);
      row.className = "cockpit-row";
      const name = el("span", undefined, "cockpit-name");
      name.append(
        el("b", r.label),
        el("small", `${r.action} · ${r.quantity}× · ${r.state}`),
      );
      const value = el("span", undefined, "cockpit-value");
      value.append(
        el(
          "b",
          r.pnl == null
            ? r.state === "queued"
              ? "Queued"
              : "—"
            : `${r.pnl > 0 ? "+" : r.pnl < 0 ? "−" : ""}${money(Math.abs(r.pnl))}`,
          r.pnl > 0 ? "up" : r.pnl < 0 ? "down" : "",
        ),
        el(
          "small",
          [
            r.pct != null ? percent(r.pct) : null,
            r.chance != null ? `${Math.round(r.chance * 100)}% chance` : null,
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      );
      row.append(name, value);
      list.append(row);
    }
    box.append(list, link("All my plays →", "#plays/mine"));
    return box;
  }
  function cockpitMarket(hub) {
    const box = el("article", undefined, "cockpit-card cockpit-market");
    const title = el("div", undefined, "cockpit-card-head");
    title.append(el("h2", "Market overview"), link("Events →", "#events"));
    box.append(title, benchmarkStrip(hub));
    const ai = (hub.events?.standing || []).find((s) => s.subject === "ai");
    if (ai)
      box.append(
        el(
          "p",
          `AI stocks ${ai.from_high > -0.0005 ? "at their high" : `${(ai.from_high * 100).toFixed(1).replace("-", "−")}% below their 52-week high`}`,
          "cockpit-sub",
        ),
      );
    return box;
  }
  function cockpitIdeas(cockpit) {
    const box = el("article", undefined, "cockpit-card cockpit-ideas"),
      ideas = cockpit?.ideas || [];
    const title = el("div", undefined, "cockpit-card-head");
    title.append(el("h2", "Top ideas"), link("All plays →", "#plays"));
    box.append(title);
    if (!ideas.length) {
      box.append(
        el(
          "p",
          "Plays appear after the worker's next options pass.",
          "cockpit-sub",
        ),
      );
      return box;
    }
    for (const p of ideas) {
      const row = link("", `#plays/idea/${encodeURIComponent(p.idea_id)}`);
      row.className = "cockpit-row";
      const name = el("span", undefined, "cockpit-name");
      name.append(
        el(
          "b",
          `${p.symbol} ${money(p.strike)} ${(p.type || "").toLowerCase()}`,
        ),
        el("small", `${p.action} · ${p.expiration}`),
      );
      const chance = p.itm_kestrel ?? p.itm_market;
      const value = el("span", undefined, "cockpit-value");
      value.append(
        el(
          "b",
          chance == null ? "—" : `${Math.round(chance * 100)}%`,
          "accent",
        ),
        el("small", money(p.action === "SELL" ? p.max_loss : p.cost)),
      );
      row.append(name, value);
      box.append(row);
    }
    if (ideas[0].category)
      box.append(el("p", ideas[0].category, "cockpit-sub"));
    return box;
  }
  async function home(ticket, focus) {
    const [hub, contractScreen] = await Promise.all([
      api("home"),
      focus === "options" ? api("explore") : Promise.resolve(null),
    ]);
    if (ticket !== generation) return;
    app.className = "research-home";
    if (focus !== "options") return cockpitHome(hub, ticket);
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
    app.append(benchmarkStrip(hub));
    return homeBody(hub, ticket, focus, contractScreen);
  }
  // The six index cards; on Home they sit inside the cockpit's market panel.
  function benchmarkStrip(hub) {
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
    return benchmark;
  }
  async function homeBody(hub, ticket, focus, contractScreen) {
    const suggestions = document.getElementById("stock-symbols");
    suggestions.replaceChildren();
    for (const [symbol, card] of Object.entries(hub.stocks)) {
      const o = el("option", card.name || symbol);
      o.value = symbol;
      suggestions.append(o);
    }
    // Setup guidance only when something is missing; otherwise just the compact PAPER control while paused.
    const keyed = snapshot.source_keys.MASSIVE_API_KEY;
    if (!keyed || !snapshot.asof) {
      const setup = el("div", undefined, "setup-strip");
      const message = !keyed
        ? focus === "options"
          ? "Options use the selected Schwab or Tradier connection. The separate broad-market history feed is not configured; this does not prevent connected chains or quotes."
          : "Broad-market research history is not configured. Set up that source in Settings; connected Schwab charts and options operate independently."
        : researchReadiness().message;
      setup.append(
        el("div", message),
        link("Set up data sources ↗", "#settings"),
        collectionControls(),
      );
      app.append(setup);
    } else if (!snapshot.worker_running) app.append(collectionControls());
    // Options: its own data first; Learning findings and the social note close the page.
    const optionsTail = [];
    if (focus === "options") {
      app.append(optionsCollections(hub));
      app.append(
        KestrelExplore.setups(contractScreen.option_setups, {
          title: "Calls & puts: contract shortlist",
          kind: "call",
        }),
      );
      const learned = await KestrelLearning.findings(ticket);
      if (ticket !== generation) return;
      optionsTail.push(learned);
      const social = el("section", undefined, "paper-status");
      social.append(
        el("h2", "Social discovery · separate from market evidence"),
        el(
          "p",
          "WSB and other ticker mentions can identify attention, but they do not establish an option trade, strike, expiration or buy direction. Social scores do not enter the market/options ranking or the PAPER learning evidence count.",
          "muted",
        ),
        link(
          "Inspect WSB vs recorded fundamentals ↗",
          "#lists/wsb_vs_reality",
        ),
      );
      optionsTail.push(social);
    }
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
            ["trending", "Trending"],
            ["momentum", "Rising trends"],
            ["gainers", "Top gainers"],
            ["losers", "Top losers"],
            ["wsb", "WallStreetBets"],
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
        trending: "Trending top 10 · market data",
        wsb: "WallStreetBets · most mentioned",
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
      if (["trending", "wsb"].includes(selected))
        content.append(
          el(
            "p",
            selected === "wsb"
              ? "Social attention, not direction. WSB mentions and reported upvotes do not influence Trending."
              : "Market activity, not direction. Ranked from stock volume, volatility-scaled moves and qualified options observations; no social input.",
            "caption muted",
          ),
        );
      if (list?.rows.length) {
        if (selected === "wsb") {
          content.append(
            table(
              [
                "#",
                "Stock",
                "Mentions · 24h",
                "Change · 24h",
                "Reported upvotes",
              ],
              list.rows
                .slice(0, 10)
                .map((row, index) => [
                  String(index + 1).padStart(2, "0"),
                  symbolCell(row, hub.stocks[row.symbol]),
                  number(row.mentions),
                  percent(row.mention_change),
                  number(row.upvotes),
                ]),
            ),
          );
          const hours = list.rows
            .map((r) => r.observed_hour)
            .filter(Number.isFinite);
          if (hours.length)
            content.append(
              el(
                "p",
                `Latest source observation: ${new Date(Math.max(...hours) * 3600000).toLocaleString("en-US", { timeZone: "America/New_York" })} ET. Counts describe the source's rolling 24-hour window.`,
                "caption muted",
              ),
            );
        } else if (focus === "options" || selected === "options_activity") {
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
              ["market_activity", "trending"].includes(selected),
            ),
          );
          if (["market_activity", "trending"].includes(selected))
            content.append(
              el(
                "p",
                "Activity, not direction. Hover a row's note for every measured input.",
                "muted caption",
              ),
            );
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
                ? "No covered stock has volume at least 1.5× normal or a move of at least two daily standard deviations. Social activity cannot qualify a stock here."
                : selected === "wsb"
                  ? "No WSB mention counts are saved for covered tickers. Public mention collection runs independently of market-data credentials; see System for its source status."
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
    if (focus !== "options") main.append(opportunities);
    if (focus !== "options") main.append(optionsCollections(hub));
    const notable = el("section", undefined, "sidebar-section hub-events");
    notable.append(
      sectionTitle("Notable events", "Logged by fixed rules", "#events"),
    );
    const recentEvents = hub.events?.recent || [];
    for (const event of recentEvents.slice(0, 5))
      notable.append(KestrelEvents.row(event));
    if (!recentEvents.length)
      notable.append(
        el("p", "Nothing unusual logged in the last 30 days.", "muted caption"),
      );
    const fromHigh = (hub.events?.standing || []).filter(
      (s) => s.subject === "SPY" || s.subject === "QQQ" || s.subject === "ai",
    );
    if (fromHigh.length)
      notable.append(
        el(
          "p",
          fromHigh
            .map(
              (s) =>
                `${s.label} ${s.from_high > -0.0005 ? "at its high" : `${(s.from_high * 100).toFixed(1).replace("-", "−")}% from high`}`,
            )
            .join(" · "),
          "muted caption",
        ),
      );
    aside.append(notable);
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
    app.append(layout, ...optionsTail);
  }

  function chartWidget(
    dailyBars,
    onInspect = () => {},
    basis = "Split-adjusted daily OHLC",
    symbol = "",
    intraday = {},
  ) {
    const wrapper = el("section", undefined, "chart-widget"),
      tools = el("div", undefined, "chart-tools");
    let { range = intraday.bars?.length ? "1D" : "3M", kind = "Line" } =
      chartChoices.get(symbol) || {};
    const periods = {
      "1D": 0,
      "1W": 0,
      "1M": 30,
      "3M": 91,
      YTD: "ytd",
      "1Y": 365,
      "5Y": 1826,
      Max: Infinity,
    };
    const chart = KestrelCharts.create({
      id: "stock",
      headline: false,
      legend: false,
      className: "chart-plot",
      tooltipClass: "chart-tooltip",
      tooltipTestid: "candle-tooltip",
      svgClass: "price-chart",
      volume: true,
    });
    function draw() {
      const minute = range === "1D" || range === "1W",
        week = range === "1W";
      const bars = minute
        ? ((week ? intraday.week : intraday.bars) || []).map((p, i) => ({
            ...p,
            day: Date.parse(p.at) / 86400000 + 719163,
            gap_before: !!p.gap_before && i > 0,
          }))
        : dailyBars;
      onInspect(null, null, range === "1D" ? intraday : null, range);
      const end = bars.at(-1)?.day || 0,
        yearStart =
          Date.UTC(
            new Date(dateOf(end) + "T12:00:00Z").getUTCFullYear(),
            0,
            1,
          ) /
            86400000 +
          719163;
      let rows = minute
        ? bars
        : bars.filter((p) =>
            periods[range] === "ytd"
              ? p.day >= yearStart
              : p.day >= end - periods[range],
          );
      if (kind === "Candles" && rows.length > 520) {
        const monthly = rows.length > 2000,
          groups = new Map();
        for (const row of rows) {
          const d = new Date(dateOf(row.day) + "T12:00:00Z"),
            key = monthly
              ? dateOf(row.day).slice(0, 7)
              : row.day - ((d.getUTCDay() + 6) % 7),
            prior = groups.get(key);
          if (prior) {
            prior.day = row.day;
            prior.high = Math.max(prior.high, row.high);
            prior.low = Math.min(prior.low, row.low);
            prior.close = row.close;
            prior.volume += row.volume || 0;
            prior.gap_before ||= row.gap_before;
          } else
            groups.set(key, {
              ...row,
              start_day: row.day,
              interval: monthly ? "Monthly" : "Weekly",
              previous_close: bars[bars.indexOf(row) - 1]?.close,
            });
        }
        rows = [...groups.values()];
      }
      const points = rows.map((p) => ({
        ...p,
        at: p.at || dateOf(p.day),
        x: minute ? Date.parse(p.at) : Date.parse(dateOf(p.day) + "T12:00:00Z"),
        value: p.close,
      }));
      const source = week
        ? "Schwab 10-minute candles · last five regular sessions"
        : minute
          ? `Schwab 1-minute candles · regular session ${intraday.session || ""} · latest minute may still be forming`
          : rows[0]?.interval
            ? `${rows[0].interval} candles · aggregated from daily OHLC`
            : basis;
      const reference = range === "1D" ? intraday.previous_close : null;
      wrapper.dataset.direction =
        rows.at(-1)?.close >= (reference || rows[0]?.close) ? "up" : "down";
      chart.update({
        points,
        viewKey: range,
        mode: kind === "Candles" ? "candles" : "line",
        xMode: minute ? "index" : "time",
        ariaLabel: `${kind} chart of observed ${week ? "10-minute" : minute ? "1-minute intraday" : "daily"} stock prices. Arrow keys inspect real candles.`,
        baseline: reference,
        series: [
          {
            id: "value",
            label: "Close",
            value: (p) => p.close,
            className: "price-path",
          },
        ],
        coverage: points.length
          ? `${source} · ${number(points.length)} observations · volume below`
          : intraday.error || source,
        emptyTitle: minute
          ? intraday.error
            ? "Intraday chart unavailable"
            : "Intraday data is loading"
          : "Price history is building",
        emptyAction:
          minute && intraday.error
            ? { label: "Review Schwab connection", href: "#settings" }
            : null,
        empty: minute
          ? intraday.error
            ? `Intraday chart unavailable: ${intraday.error}`
            : "Intraday data is loading. The app is requesting Schwab minute candles; longer ranges remain available."
          : "Price history is building. No observations have been invented.",
        timeFormat: (p) =>
          minute ? intradayTime(p.at, true) : shortDate(dateOf(p.day)),
        xFormat: (p) =>
          minute
            ? week
              ? KestrelCharts.date(p.at)
              : intradayTime(p.at)
            : KestrelCharts.date(p.at),
        readoutRows: (p) => [
          ["Close", money(p.close)],
          ["Volume", compact(p.volume)],
        ],
        tooltipFooter: minute
          ? week
            ? "Schwab 10-minute OHLC · regular sessions · USD"
            : "Schwab 1-minute OHLC · regular session · USD"
          : `${basis} · USD`,
        tooltipRows: (p) => [
          ["Open", money(p.open)],
          ["High", money(p.high)],
          ["Low", money(p.low)],
          ["Close", money(p.close)],
          ["Volume", number(p.volume)],
          [
            p.interval
              ? "Period change"
              : minute
                ? "Candle change"
                : "Day change",
            (() => {
              const prev =
                p.previous_close ||
                bars.find((b, i) => bars[i + 1]?.day === p.day)?.close;
              return prev ? change(p.close / prev - 1) : "Not available";
            })(),
          ],
          ...(p.interval
            ? [["Candle", `${p.interval} · from ${dateOf(p.start_day)}`]]
            : []),
        ],
        onInspect: (p, i, first) =>
          onInspect(p, first, range === "1D" ? intraday : null, range),
      });
    }
    tools.append(
      KestrelCharts.ranges(
        Object.keys(periods),
        range,
        (v) => {
          range = v;
          chartChoices.set(symbol, { range, kind });
          draw();
        },
        { className: "range-buttons" },
      ),
      KestrelCharts.ranges(
        ["Line", "Candles"],
        kind,
        (v) => {
          kind = v;
          chartChoices.set(symbol, { range, kind });
          draw();
        },
        { label: "Chart type", className: "chart-types" },
      ),
    );
    chart.querySelector(".kc-coverage").classList.add("chart-readout");
    wrapper.append(chart, tools);
    draw();
    return wrapper;
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
        // Say here what is happening, and fill in by itself when the chain arrives: no trip to another page.
        const running = queued?.running ?? snapshot.worker_running,
          job = snapshot.jobs?.options || {},
          box = empty(
            running ? "Option chain on its way" : "Collection is paused",
            data.message ||
              (running
                ? `${symbol} is first in line for the next options pass, usually within about 5 minutes. This section fills in by itself.`
                : "The chain arrives once collection runs. Start it here."),
            null,
          ),
          when = (iso) =>
            new Date(iso).toLocaleString("en-US", {
              timeZone: "America/New_York",
              weekday: "short",
              hour: "numeric",
              minute: "2-digit",
            }) + " ET";
        box.append(
          el(
            "p",
            [
              job.last_ok_at
                ? `Last options pass ${when(job.last_ok_at)}`
                : "No options pass yet",
              job.error ? `last error: ${job.error}` : null,
            ]
              .filter(Boolean)
              .join(" · "),
            job.error ? "error" : "muted",
          ),
        );
        if (!running) box.append(collectionControls());
        else if (job.error)
          box.append(link("Check the options connection ↗", "#settings"));
        container.append(box);
        const tries = (chainPolls.get(symbol) || 0) + 1;
        chainPolls.set(symbol, tries);
        if (running && tries <= 20)
          setTimeout(() => {
            if (ticket === generation && container.isConnected)
              optionChain(symbol, container, ticket, expiration);
          }, 30000);
        return;
      }
      chainPolls.delete(symbol);
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
    const page = KestrelStockPage,
      F = page.fmt,
      d = data.analysis,
      p = d?.price || {},
      etf = !!data.listing?.etf,
      chartHost = el("div"),
      historyStatus = el("p", "", "muted caption"),
      coverageDetails = el("details", undefined, "chart-coverage"),
      coverageInfo = el("p", "", "muted caption"),
      view = { financials: "quarterly", aboutOpen: false };
    coverageDetails.append(
      el("summary", "Chart history and quote coverage"),
      coverageInfo,
      historyStatus,
    );
    let latest = data.bars.at(-1),
      previous = data.bars.at(-2),
      inspected = false,
      intradayMode = false,
      renderedDailyAt = null,
      renderedMinuteAt = null,
      profile = null,
      renderedProfile = "";
    const RANGE_LABEL = {
      "1W": "Past week",
      "1M": "Past month",
      "3M": "Past 3 months",
      YTD: "Year to date",
      "1Y": "Past year",
      "5Y": "Past 5 years",
      Max: "All time",
    };
    function liveQuote() {
      return data.quote && !data.quote.stale ? data.quote : null;
    }
    function todayChange() {
      const q = liveQuote();
      if (q?.prev_close) return [q.last - q.prev_close, q.prev_close];
      return latest && previous?.close
        ? [latest.close - previous.close, previous.close]
        : [null, null];
    }
    const breadcrumb = el("div", undefined, "breadcrumbs");
    breadcrumb.setAttribute("role", "navigation");
    breadcrumb.setAttribute("aria-label", "Breadcrumb");
    breadcrumb.append(
      link("Stocks", "#stocks"),
      el("span", "/"),
      el("span", d?.sector || data.listing?.exchange || "US equities"),
      el("span", "/"),
      el("span", symbol),
    );
    app.append(breadcrumb);
    const layout = el("div", undefined, "stock-layout"),
      main = el("div", undefined, "stock-main"),
      aside = el("aside", undefined, "stock-aside"),
      hero = el("section", undefined, "stock-hero"),
      identity = el("div", undefined, "stock-identity"),
      mark = el("div", symbol.slice(0, 1), "stock-mark"),
      names = el("div");
    mark.setAttribute("aria-hidden", "true");
    const ticker = el("div", undefined, "stock-ticker");
    for (const part of [
      symbol,
      data.listing?.exchange,
      etf ? "ETF market/options profile" : d?.sector,
    ].filter(Boolean))
      ticker.append(el("span", part));
    names.append(ticker, el("h1", d?.name || data.listing?.name || symbol));
    identity.append(mark, names);
    const headlinePrice = el("div", money(latest?.close ?? p.close), "big"),
      changeLine = el("div", undefined, "stock-change"),
      changeAmount = el("span"),
      changeLabel = el("span", "Today", "muted"),
      afterLine = el("div", undefined, "stock-change after"),
      priceCaption = el("p", "", "muted caption");
    headlinePrice.dataset.testid = "headline-price";
    priceCaption.dataset.testid = "price-caption";
    changeLine.append(changeAmount, changeLabel);
    function setChange(delta, base, label) {
      const pct = delta != null && base ? delta / base : null;
      changeAmount.textContent =
        delta == null ? "—" : `${F.signedUsd(delta)} (${F.signedPct(pct)})`;
      changeAmount.className = tone(delta);
      changeLabel.textContent = label;
      hero.dataset.direction = delta == null || delta >= 0 ? "up" : "down";
    }
    function afterHours() {
      const q = liveQuote(),
        x = profile?.quote_details || {};
      const ext = x.ext_last,
        close = x.regular_last ?? q?.last;
      afterLine.hidden = !(q && ext && close && Math.abs(ext - close) > 1e-9);
      if (afterLine.hidden) return;
      const delta = ext - close;
      afterLine.replaceChildren(
        el(
          "span",
          `${F.signedUsd(delta)} (${F.signedPct(delta / close)})`,
          tone(delta),
        ),
        el("span", "After hours", "muted"),
      );
    }
    const inspectPrice = (row, baseline, session = null, range = null) => {
      inspected = !!row;
      intradayMode = session !== null;
      const quote = liveQuote();
      const minuteLast = session?.bars?.at(-1),
        minuteBase = session?.previous_close || session?.bars?.[0]?.open;
      const price =
        row?.close ??
        quote?.last ??
        minuteLast?.close ??
        latest?.close ??
        p.close;
      headlinePrice.textContent = money(price);
      if (row) {
        const base = session && minuteBase ? minuteBase : baseline?.close;
        setChange(
          base ? row.close - base : null,
          base,
          row.at ? intradayTime(row.at) : F.date(dateOf(row.day)),
        );
      } else if (range && RANGE_LABEL[range] && baseline?.close) {
        setChange(price - baseline.close, baseline.close, RANGE_LABEL[range]);
      } else if (!quote && minuteLast && minuteBase) {
        const today = new Date().toLocaleDateString("en-CA", {
          timeZone: "America/New_York",
        });
        setChange(
          minuteLast.close - minuteBase,
          minuteBase,
          !session.session || session.session === today
            ? "Today"
            : F.date(session.session, false),
        );
      } else {
        const [delta, base] = todayChange();
        setChange(
          delta,
          base,
          quote || !latest ? "Today" : F.date(dateOf(latest.day), false),
        );
      }
      priceCaption.textContent = row?.at
        ? `${intradayTime(row.at, true)} · ${range === "1W" ? "10-minute" : "1-minute"} close · USD · ${session?.previous_close ? "Change vs previous close" : "Change since session open"}`
        : row
          ? `${new Date(dateOf(row.day) + "T12:00:00Z").toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" })} · Stored close · USD · Change since ${dateOf(baseline.day)}`
          : quote
            ? `Schwab quote · ${new Date(quote.quoted_at).toLocaleString("en-US", { timeZone: "America/New_York" })} ET · USD · Change vs previous close`
            : minuteLast
              ? `${intradayTime(minuteLast.at, true)} · Last saved 1-minute close · USD · ${session.previous_close ? "Change vs previous close" : "Change since session open"}`
              : latest
                ? `Stored close · ${shortDate(dateOf(latest.day))} · USD · Change vs previous observed close`
                : "No stored price history";
      afterHours();
      updateHistoryStatus();
    };
    const quoteBox = el("div", undefined, "stock-quote");
    quoteBox.append(headlinePrice, changeLine, afterLine, priceCaption);
    hero.append(identity, quoteBox);
    inspectPrice(null);
    function updateHistoryStatus() {
      const c = data.coverage;
      const minute = data.stock_data?.intraday || {};
      historyStatus.textContent = intradayMode
        ? `Schwab · 1-minute candles · regular trading session${minute.session ? ` ${minute.session}` : " pending"} · ${minute.bars?.length || 0} observations${minute.last_at ? ` · latest ${intradayTime(minute.last_at)}` : ""}${minute.error ? ` · ${minute.error}` : ""}`
        : `${data.chart_source} · ${c?.sessions || 0} daily observations${c?.first ? ` · ${c.first} through ${c.last}` : ""}${data.stock_data?.history_error ? ` · ${data.stock_data.history_error}` : ""}`;
      coverageInfo.textContent = `1D uses actual Schwab 1-minute candles for the latest available regular trading session and 1W five sessions of 10-minute candles; both exclude extended hours. The latest minute may still be forming. ${data.stock_data?.max_note || "Max shows all downloaded history. Connect Schwab in Settings for longer chart history."} ${data.chart_basis}. Daily candles may include an unfinished session. Research statistics below use separately dated bulk history. ${data.quote?.stale ? `The last Schwab quote is from ${new Date(data.quote.quoted_at).toLocaleString("en-US", { timeZone: "America/New_York" })} ET; current quotes are not being inferred from candles.` : ""} ${data.stock_data?.quote_error || ""}`;
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
          data.stock_data?.intraday || {},
        ),
      );
      renderedDailyAt = data.stock_data?.updated_at;
      renderedMinuteAt = data.stock_data?.intraday?.updated_at;
      updateHistoryStatus();
    }
    drawHistory();
    main.append(hero, chartHost, coverageDetails);

    // Company sections arrive from the profile endpoint and refresh as background sources fill in.
    const sectionsHost = el("div", undefined, "stock-sections");
    const caseHost = el("div"),
      chains = el("section", undefined, "detail-section option-chain"),
      desks = el("section", undefined, "detail-section");
    chains.id = "sp-chain";
    chains.append(
      sectionTitle("Options chain", "Loading stored observations…"),
    );
    function renderProfile() {
      const sig = JSON.stringify(profile);
      if (!profile || sig === renderedProfile) return;
      renderedProfile = sig;
      if (profile.logo && !mark.querySelector("img")) {
        const img = el("img");
        img.src = `/api/stocks/stock/${encodeURIComponent(symbol)}/logo`;
        img.alt = "";
        img.onload = () => mark.classList.add("has-logo");
        mark.replaceChildren(img);
      }
      afterHours();
      const parts = [
        page.about(profile, data, view),
        page.keyStats(profile, data),
        page.relatedLists(profile),
        caseHost,
        page.news(profile, symbol),
        KestrelEvents.section(profile.events),
        ...(etf
          ? []
          : [
              page.ratings(profile),
              page.earnings(profile),
              page.financials(profile, view),
            ]),
        page.shortInterest(profile),
        ...(etf ? [] : [page.insiders(profile), page.dividends(profile)]),
      ].filter(Boolean);
      sectionsHost.replaceChildren(...parts);
      const peerHost = page.peers(profile);
      peersHost.replaceChildren(...(peerHost ? [peerHost] : []));
      sourcesHost.replaceChildren(page.sources(profile));
      const history = page.scoreHistory(profile);
      historyHost.replaceChildren(...(history ? [history] : []));
    }
    const peersHost = el("div"),
      sourcesHost = el("div"),
      historyHost = el("div");
    let profilePolls = 0;
    async function pollProfile() {
      if (ticket !== generation) return;
      try {
        if (document.visibilityState === "visible") {
          profile = await api(`stock/${encodeURIComponent(symbol)}/profile`);
          if (ticket !== generation) return;
          renderProfile();
        }
      } catch (error) {
        if (ticket === generation && !profile)
          sectionsHost.replaceChildren(el("p", error.message, "error"));
      }
      profilePolls += 1;
      if (ticket === generation)
        setTimeout(pollProfile, profilePolls < 12 ? 15000 : 60000);
    }
    async function pollStock() {
      if (ticket !== generation) return;
      try {
        if (document.visibilityState === "visible") {
          await api(`stock/${encodeURIComponent(symbol)}/history/request`, {});
          const next = await api(`stock/${encodeURIComponent(symbol)}`);
          if (ticket !== generation) return;
          const changed =
            next.stock_data?.updated_at !== renderedDailyAt ||
            next.stock_data?.intraday?.updated_at !== renderedMinuteAt;
          data = next;
          latest = data.bars.at(-1);
          previous = data.bars.at(-2);
          if (changed && !inspected) drawHistory();
          else {
            updateHistoryStatus();
            if (!inspected)
              inspectPrice(
                null,
                null,
                intradayMode ? data.stock_data?.intraday || {} : null,
              );
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
    pollStock().then(pollProfile);
    if (d) {
      const rationale = el("section", undefined, "sp-section");
      rationale.append(
        sectionTitle(
          etf ? "Trend and risk observations" : "The investment case",
          etf
            ? "Calculated from stored prices; these are observations, not a forecast."
            : "What supports KESTREL's view, and what challenges it",
        ),
      );
      const cases = el("div", undefined, "case-grid");
      for (const [title, items, color] of [
        [etf ? "Positive trend observations" : "Bull case", d.bull, "up"],
        [etf ? "Risk and limitations" : "Bear case", d.bear, "down"],
      ]) {
        const card = el("div", undefined, `case-card ${color}`);
        card.append(el("h3", title));
        const list = el("ul");
        for (const text of items || []) list.append(el("li", text));
        if (!items?.length)
          list.append(
            el(
              "li",
              etf
                ? "No positive trend observations in the measured windows."
                : "Insufficient evidence for a case.",
            ),
          );
        card.append(list);
        cases.append(card);
      }
      rationale.append(cases);
      caseHost.append(rationale);
    }
    desks.id = "desks";
    desks.append(
      sectionTitle(
        etf
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
    const deskTone = (score) =>
      score == null
        ? "unavailable"
        : score >= 58
          ? "favorable"
          : score < 42
            ? "unfavorable"
            : "mixed";
    const grid = el("div", undefined, "desk-grid");
    for (const [id, label] of Object.entries(snapshot.desks)) {
      if (etf && !d?.desks?.[id]) continue;
      const desk = d?.desks?.[id],
        card = el("article", undefined, `desk-card ${deskTone(desk?.score)}`);
      card.id = `desk-${id}`;
      const top = el("div", undefined, "desk-top");
      top.append(
        el("div", label, "desk-label"),
        el("strong", etf ? etfValue(id) : number(desk?.score), "desk-score"),
      );
      const meter = el("div", undefined, "desk-meter"),
        fill = el("span");
      fill.style.width = `${Math.max(0, Math.min(100, desk?.score ?? 0))}%`;
      meter.append(fill);
      card.append(
        top,
        meter,
        el("p", desk?.verdict || "Awaiting data", "desk-verdict"),
      );
      const list = el("ul");
      for (const text of desk?.reasons || []) list.append(el("li", text));
      card.append(
        list,
        el(
          "small",
          `${desk?.confidence == null ? "—" : `${Math.round(100 * desk.confidence)}%`} data coverage${d?.weights && !etf ? ` · weight ${number(d.weights[id] ?? 0)}` : ""}`,
        ),
      );
      grid.append(card);
    }
    desks.append(grid);
    main.append(sectionsHost, chains, desks, peersHost);
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
    main.append(sourcesHost);

    // The KESTREL card: the committee's view beside the chart, like an order panel.
    const committee = el("section", undefined, "committee-card");
    committee.append(
      el("div", etf ? "ETF PROFILE" : "COMMITTEE VIEW", "eyebrow"),
    );
    const score = el("div", undefined, "committee-number");
    score.append(
      el("strong", etf ? "ETF" : number(d?.committee?.score)),
      el("span", etf ? "" : "/ 100", "muted"),
    );
    const verdict = etf
      ? "Market & options evidence"
      : d?.committee?.label || "Awaiting evidence";
    committee.append(score, el("h2", verdict));
    committee.dataset.tone = /bullish/i.test(verdict)
      ? "up"
      : /bearish/i.test(verdict)
        ? "down"
        : "neutral";
    committee.append(
      el(
        "p",
        etf
          ? "Company valuation, earnings and insider models do not apply to funds. This profile shows observed price, risk and options data without a company committee score."
          : "A weighted synthesis of the eleven desks below. Desks with no weight are shown but do not vote.",
        "muted caption",
      ),
    );
    const strip = el("div", undefined, "desk-strip");
    for (const [id, label] of Object.entries(snapshot.desks)) {
      if (etf && !d?.desks?.[id]) continue;
      const deskScore = d?.desks?.[id]?.score,
        a = link("", `#desk-${id}`);
      a.className = etf
        ? d?.desks?.[id]?.confidence
          ? "mixed"
          : "unavailable"
        : deskTone(deskScore);
      a.title = `${label}: ${etf ? etfValue(id) : number(deskScore)}`;
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
          ? etf
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
    const actions = el("div", undefined, "committee-actions");
    actions.append(watch, link("Options chain", "#sp-chain"));
    actions.lastChild.onclick = (e) => {
      e.preventDefault();
      chains.scrollIntoView({ behavior: "smooth", block: "start" });
    };
    committee.append(actions, historyHost);
    aside.append(committee);
    const overview = el("section", undefined, "sidebar-section");
    overview.append(sectionTitle("Desk breakdown"));
    for (const [id, label] of Object.entries(snapshot.desks)) {
      if (etf && !d?.desks?.[id]) continue;
      const row = el("div", undefined, "sector-row"),
        value = d?.desks?.[id]?.score;
      row.append(
        el("span", label),
        el(
          "strong",
          etf ? etfValue(id) : number(value),
          etf ? "" : deskTone(value),
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
    layout.append(main, aside);
    app.append(layout);
    await optionChain(symbol, chains, ticket);
  }
  return { home, stock };
})();
