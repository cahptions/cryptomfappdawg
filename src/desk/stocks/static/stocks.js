"use strict";
const app = document.getElementById("app"),
  statusLine = document.getElementById("status");
let snapshot = null,
  csrf = "",
  serverCompatible = true,
  generation = 0,
  pauseRefresh = null;
const money = (x) =>
  x === null || x === undefined
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
      }).format(x);
const number = (x) =>
  x === null || x === undefined
    ? "—"
    : Number(x).toLocaleString("en-US", { maximumFractionDigits: 2 });
const percent = (x) =>
  x === null || x === undefined ? "—" : `${(100 * x).toFixed(2)}%`;
// "Apple Inc. Common Stock" → "Apple Inc.": listing suffixes add nothing on a list row.
function companyName(name) {
  return (name || "")
    .replace(
      /\s*[-–]?\s*(Class [A-Z] )?(Common Stock|Ordinary Shares|Common Shares|American Depositary Shares|Depositary Shares|ADS|ADR)\b.*$/i,
      "",
    )
    .trim();
}
function el(tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = String(text);
  if (cls) n.className = cls;
  return n;
}
function panel(title, text) {
  const n = el("section", undefined, "panel");
  if (title) n.append(el("h2", title));
  if (text) n.append(el("p", text, "muted"));
  return n;
}
function link(label, href) {
  const n = el("a", label);
  n.href = href;
  if (label.includes("↗")) {
    n.textContent = label.replaceAll("↗", "").trim();
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 16 16");
    svg.setAttribute("class", "ui-arrow");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute("d", "M4 12 12 4M4 4h8v8");
    svg.append(path);
    n.append(svg);
  }
  return n;
}
function details(title, value) {
  const d = el("details");
  d.append(el("summary", title), el("pre", JSON.stringify(value, null, 2)));
  return d;
}
function table(headers, rows) {
  const wrap = el("div", undefined, "table-wrap"),
    t = el("table"),
    head = el("thead"),
    tr = el("tr");
  for (const h of headers) {
    const cell = el("th", h);
    cell.setAttribute("scope", "col");
    tr.append(cell);
  }
  head.append(tr);
  t.append(head);
  const body = el("tbody");
  for (const row of rows) {
    const r = el("tr");
    for (const cell of row) {
      const td = el("td");
      if (cell instanceof Node) td.append(cell);
      else td.textContent = cell ?? "—";
      r.append(td);
    }
    body.append(r);
  }
  t.append(body);
  wrap.append(t);
  return wrap;
}
async function api(path, body, renewSession = true) {
  let response;
  try {
    response = await fetch(`/api/stocks/${path}`, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      cache: "no-store",
      headers:
        body === undefined
          ? {}
          : { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
  } catch (error) {
    throw Error(
      error.name === "TimeoutError"
        ? "Stocks took too long to respond. Try again; if it persists, use Evidence → Check data health."
        : "Cannot reach Stocks. Reopen KESTREL Stocks, then use Evidence → Check data health.",
    );
  }
  let value;
  try {
    value = await response.json();
  } catch {
    throw Error(
      `Stocks returned an unreadable response (HTTP ${response.status}). Use Evidence → Check data health.`,
    );
  }
  if (
    body !== undefined &&
    renewSession &&
    response.status === 403 &&
    value.detail === "valid dashboard session required"
  ) {
    // The rejected request has not reached its handler. Renew only this specific failure, once.
    csrf = (await api("session")).csrf_token;
    return api(path, body, false);
  }
  if (!response.ok && response.status === 404 && value.detail === "Not Found")
    throw Error(
      "This running Stocks server does not have the updated feature. Reopen KESTREL Stocks from your application menu to load the installed update; refreshing this browser page cannot restart the server.",
    );
  if (!response.ok)
    throw Error(value.detail || `Request failed (${response.status})`);
  return value;
}
function heading(title, subtitle) {
  app.append(el("h1", title));
  if (subtitle) app.append(el("p", subtitle, "muted"));
}
function collectionControls() {
  const box = el("div", undefined, "collection-controls"),
    state = snapshot.collection || {},
    running = state.running || snapshot.worker_running,
    status = el("p", undefined, "muted"),
    button = el(
      "button",
      state.stopping
        ? "Pausing PAPER…"
        : running
          ? "Pause PAPER"
          : "Start PAPER",
      "primary",
    );
  button.type = "button";
  button.disabled = Boolean(
    state.stopping || (running && !state.owned && !state.managed),
  );
  status.textContent = state.stopping
    ? "Pausing safely. Completed imports and simulated trades stay saved."
    : running
      ? state.owned || state.managed
        ? "Collecting data and simulating trades as each strategy's data arrives."
        : "An existing Stocks worker is collecting. Control it from its terminal."
      : state.error
        ? state.error
        : state.exit_code
          ? "The PAPER worker stopped unexpectedly. Check System and stocks-research.log."
          : "Collection and simulated trading are paused. This choice is remembered.";
  const badge = el(
    "span",
    undefined,
    `collection-state ${state.stopping ? "stopping" : running ? "running" : state.error || state.exit_code ? "failed" : "paused"}`,
  );
  badge.append(
    el("i"),
    el(
      "strong",
      state.stopping
        ? "Pausing"
        : running
          ? "Running"
          : state.error || state.exit_code
            ? "Stopped"
            : "Paused",
    ),
  );
  button.onclick = async () => {
    button.disabled = true;
    try {
      snapshot.collection = await api(
        `research/${running ? "pause" : "start"}`,
        {},
      );
      await refresh();
      if (location.hash !== "#settings") render();
    } catch (error) {
      status.textContent = error.message;
      button.disabled = false;
    }
  };
  box.append(badge, status, button);
  return box;
}
function connectionWorkerControls() {
  const box = el("div", undefined, "connection-worker-controls"),
    state = snapshot.collection || {},
    running = state.running || snapshot.worker_running;
  const text = state.stopping
    ? "PAPER is pausing. Leave this dashboard running; your typed settings stay here. Save becomes available when the collector stops."
    : running && (state.owned || state.managed)
      ? "PAPER and collection are running. Pause them here before saving this connection. Leave the dashboard running."
      : running
        ? "A Stocks collector is running in another terminal. Stop only the terminal running desk stocks collect, desk stocks paper, or desk stocks refresh. Keep the terminal running desk stocks dashboard open. Ctrl+C inside the browser does not pause collection."
        : "Collection is paused. You can save this connection now. Leave the dashboard running.";
  box.append(el("p", text, "muted"));
  if (running && (state.owned || state.managed)) {
    const pause = el(
      "button",
      state.stopping
        ? "Waiting for collector to pause…"
        : "Pause collector to save",
    );
    pause.type = "button";
    pause.disabled = Boolean(state.stopping);
    pause.onclick = async () => {
      pause.disabled = true;
      try {
        snapshot.collection = await api("research/pause", {});
        await refresh();
      } catch (error) {
        box.append(el("p", error.message, "error"));
        pause.disabled = false;
      }
    };
    box.append(pause);
  }
  return box;
}
function updateConnectionControls() {
  const running = snapshot.collection?.running || snapshot.worker_running;
  for (const node of document.querySelectorAll(".connection-worker-controls"))
    node.replaceWith(connectionWorkerControls());
  const save = document.getElementById("save-connection");
  if (save) {
    save.disabled = Boolean(running || save.dataset.saving);
    save.title = running
      ? "Pause collection using the instructions above. Leave the dashboard running."
      : "";
  }
  const connect = document.getElementById("connect-schwab");
  if (connect) {
    connect.disabled =
      connect.dataset.pending === "true" ||
      snapshot.connection.options_provider !== "schwab" ||
      !snapshot.connection.schwab.credentials_set;
    connect.title =
      snapshot.connection.options_provider !== "schwab"
        ? "Choose Schwab market data and Save privately first."
        : !snapshot.connection.schwab.credentials_set
          ? "Save your Client ID and Client Secret first."
          : "";
  }
  for (const [id, configured] of [
    ["app-key", snapshot.connection.schwab.app_key_set],
    ["app-secret", snapshot.connection.schwab.app_secret_set],
  ]) {
    const input = document.getElementById(id),
      presence = document.getElementById(`${id}-presence`);
    if (input)
      input.placeholder = configured
        ? "Saved on this laptop · leave blank to keep"
        : "Not saved yet";
    if (presence)
      presence.textContent = configured
        ? "Saved on this laptop. A blank field keeps the saved value."
        : "Not saved yet.";
  }
}
function sourceSetup() {
  const box = panel(
      "Start here · free stock research",
      "Connected Schwab is preferred for viewed-stock quotes and up to 20 years of available daily chart history. Set it up in the Schwab connection section below. Massive supplies separate market-wide closing-price history for research and daily PAPER; SEC supplies company filings. Collection starts automatically unless paused.",
    ),
    form = el("form", undefined, "source-form"),
    fields = {},
    message = el("p", undefined, "muted");
  for (const [id, name, title, text, type, href, action] of [
    [
      "massive-key",
      "MASSIVE_API_KEY",
      "1. Broad-market history · Massive",
      "Create a free account, choose Stocks Basic ($0), then copy the API key from your account dashboard. End-of-day prices, not live quotes. A recent-close 403 can mean publication delay or missing plan entitlement; the app retries and keeps downloading accessible older dates. Check your plan access before replacing a working key. Options chains use their own connection.",
      "password",
      "https://massive.com/",
      "Open Massive signup ↗",
    ],
    [
      "sec-contact",
      "SEC_CONTACT_EMAIL",
      "2. Company filings · SEC",
      "Enter your contact email below. No account, signup or SEC key is required. This address identifies the app's requests to the SEC.",
      "email",
      null,
      null,
    ],
    [
      "tradier-token",
      "TRADIER_TOKEN",
      "3. Options while waiting for Schwab · optional",
      "Skip this if you are waiting for Schwab. An existing Tradier sandbox token supplies delayed option chains when Tradier is selected below. Stock price history does not require it.",
      "password",
      "https://developer.tradier.com/",
      "Tradier developer information ↗",
    ],
    [
      "finnhub-key",
      "FINNHUB_API_KEY",
      "4. Analyst ratings and earnings estimates · Finnhub · optional",
      "Free: sign up at finnhub.io, then copy the API key from your dashboard. Stock pages then show analyst buy/hold/sell counts, the last four quarters of earnings per share against the estimate, and the next report date. Without it those sections simply stay empty; nothing else depends on it.",
      "password",
      "https://finnhub.io/register",
      "Open Finnhub signup ↗",
    ],
  ]) {
    const section = el("div", undefined, "source-step"),
      input = el("input"),
      label = el("label", title);
    label.htmlFor = id;
    input.id = id;
    input.type = type;
    input.autocomplete = type === "password" ? "new-password" : "off";
    input.spellcheck = false;
    input.maxLength = 2000;
    input.placeholder = snapshot.source_keys[name]
      ? "Saved · leave blank to keep"
      : type === "email"
        ? "Your contact email"
        : "Paste your key here";
    fields[id] = input;
    section.append(label, el("p", text, "muted"));
    if (href) {
      const external = link(action, href);
      external.target = "_blank";
      external.rel = "noopener noreferrer";
      section.append(external);
    }
    section.append(
      input,
      el(
        "small",
        snapshot.source_keys[name]
          ? "Saved privately · collection will check access"
          : "Not saved",
        "source-presence muted",
      ),
    );
    section.classList.toggle("saved", Boolean(snapshot.source_keys[name]));
    form.append(section);
  }
  const save = el("button", "Save research sources", "primary");
  save.type = "submit";
  form.append(save, message);
  form.onsubmit = async (event) => {
    event.preventDefault();
    save.disabled = true;
    try {
      const result = await api("sources", {
        massive_key: fields["massive-key"].value,
        sec_contact: fields["sec-contact"].value,
        tradier_token: fields["tradier-token"].value,
        finnhub_key: fields["finnhub-key"].value,
      });
      snapshot.source_keys = result.source_keys;
      for (const input of Object.values(fields)) input.value = "";
      for (const [id, name] of [
        ["massive-key", "MASSIVE_API_KEY"],
        ["sec-contact", "SEC_CONTACT_EMAIL"],
        ["tradier-token", "TRADIER_TOKEN"],
        ["finnhub-key", "FINNHUB_API_KEY"],
      ]) {
        fields[id].placeholder = result.source_keys[name]
          ? "Saved · leave blank to keep"
          : "Not saved";
        fields[id].nextElementSibling.textContent = result.source_keys[name]
          ? "Saved privately · collection will check access"
          : "Not saved";
      }
      message.textContent =
        "Saved privately. Running PAPER picks up changes on its next pass. If paused, use Start PAPER below. Your existing credentials were preserved.";
    } catch (error) {
      message.textContent = error.message;
    } finally {
      save.disabled = false;
    }
  };
  box.append(
    form,
    collectionControls(),
    el(
      "p",
      "No-signup feeds for listings, market mentions, short activity and Treasury rates are already wired in. Collection starts them automatically. Price history and option chains need the credentials described above.",
      "muted",
    ),
  );
  return box;
}
function discover(selected) {
  // One research list at a time: each stock with its company, moves, a three-month chart, KESTREL's score and
  // every desk's, and in words what lifts the score and what holds it back.
  const F = KestrelStockPage.fmt,
    tone = (x) => (x == null || x === 0 ? "" : x > 0 ? "up" : "down");
  app.className = "lists-page";
  const head = el("div", undefined, "lists-head"),
    titles = el("div");
  titles.append(
    el("div", "Research lists", "eyebrow"),
    el("h1", "Find the evidence."),
    el(
      "p",
      "Daily lists from eleven deterministic desks. Each stock shows why it is here, how it has moved, and which desks lift or hold back KESTREL's score. Nothing is invented to fill a gap.",
      "muted",
    ),
  );
  const controls = el("div", undefined, "lists-controls"),
    select = el("select"),
    search = el("form", undefined, "lists-search"),
    input = el("input");
  select.setAttribute("aria-label", "Research list");
  for (const list of snapshot.lists) {
    const option = el("option", list.title);
    option.value = list.id;
    select.append(option);
  }
  if (selected && snapshot.lists.some((l) => l.id === selected))
    select.value = selected;
  input.placeholder = "Open a stock · e.g. AAPL";
  input.setAttribute("aria-label", "Stock symbol");
  input.maxLength = 12;
  search.append(input, el("button", "Open"));
  search.onsubmit = (e) => {
    e.preventDefault();
    if (input.value.trim())
      location.hash = `stock/${encodeURIComponent(input.value.trim().toUpperCase())}`;
  };
  controls.append(select, search);
  head.append(titles, controls);
  app.append(head);
  const content = el("div");
  app.append(content);
  const labelTone = (label) =>
    /bullish/i.test(label || "")
      ? "up"
      : /bearish/i.test(label || "")
        ? "down"
        : "neutral";
  const deskTone = (d) =>
    d.score == null
      ? "unavailable"
      : d.score >= 58
        ? "favorable"
        : d.score < 42
          ? "unfavorable"
          : "mixed";
  function row(r, optionsClock) {
    const item = el("li", undefined, "list-row"),
      who = el("div", undefined, "list-who"),
      name = link("", `#stock/${encodeURIComponent(r.symbol)}`);
    name.className = "list-name";
    name.append(el("strong", r.symbol), el("span", companyName(r.name)));
    who.append(el("span", String(r.rank).padStart(2, "0"), "list-rank"), name);
    if (r.sector) who.append(el("small", r.sector));
    const price = el("div", undefined, "list-price");
    price.append(
      el("strong", money(r.close ?? r.price)),
      el("span", F.signedPct(r.change_1d), tone(r.change_1d)),
    );
    if (optionsClock) price.append(el("small", "Underlying at snapshot"));
    const moves = el("dl", undefined, "list-moves");
    for (const [label, value] of [
      ["1M", r.ret_1m],
      ["1Y", r.ret_1y],
    ]) {
      const box = el("div");
      box.append(el("dt", label), el("dd", F.signedPct(value, 1), tone(value)));
      moves.append(box);
    }
    const chart = el("div", undefined, "list-chart"),
      spark = r.spark || [];
    chart.append(
      KestrelStockPage.spark(
        spark,
        spark.length > 1 ? (spark.at(-1) >= spark[0] ? "up" : "down") : "",
      ),
    );
    chart.title = "Three months of stored daily closes";
    const verdict = el("div", undefined, "list-verdict"),
      score = el("div", undefined, "list-score");
    score.append(
      el(
        "strong",
        r.committee?.score == null ? "—" : F.num(r.committee.score, 0),
      ),
      el(
        "span",
        r.committee?.label || "No score yet",
        labelTone(r.committee?.label),
      ),
    );
    const strip = el("div", undefined, "list-strip");
    for (const d of r.desks || []) {
      const cell = el(
        "span",
        undefined,
        `${deskTone(d)}${d.voting ? "" : " silent"}`,
      );
      cell.title = `${d.label}: ${d.score ?? "no data"}${d.voting ? "" : " (does not vote)"}`;
      strip.append(cell);
    }
    strip.setAttribute(
      "aria-label",
      (r.desks || [])
        .map((d) => `${d.label} ${d.score ?? "no data"}`)
        .join(", "),
    );
    verdict.append(score, strip);
    const why = el("div", undefined, "list-why");
    why.append(el("p", r.reason, "list-reason"));
    const forces = el("p", undefined, "list-forces");
    if (r.lifts?.length) {
      forces.append(el("span", "Lifts ", "muted"));
      forces.append(
        el(
          "span",
          r.lifts.map((d) => `${d.label} ${d.score}`).join(" · "),
          "up",
        ),
      );
    }
    if (r.drags?.length) {
      if (r.lifts?.length) forces.append(el("span", "   ", "muted"));
      forces.append(el("span", "Holds back ", "muted"));
      forces.append(
        el(
          "span",
          r.drags.map((d) => `${d.label} ${d.score}`).join(" · "),
          "down",
        ),
      );
    }
    if (forces.children.length) why.append(forces);
    const evidence = [...(r.bull || []), ...(r.bear || [])];
    if (evidence.length) {
      const facts = el("ul", undefined, "list-facts");
      for (const text of r.bull || []) facts.append(el("li", text, "bull"));
      for (const text of r.bear || []) facts.append(el("li", text, "bear"));
      why.append(facts);
    }
    item.append(who, chart, price, moves, verdict, why);
    return item;
  }
  async function draw() {
    const id = select.value,
      list = snapshot.lists.find((l) => l.id === id);
    if (location.hash !== `#lists/${id}`)
      history.replaceState(null, "", `#lists/${id}`);
    content.replaceChildren();
    if (!list) return;
    const card = el("section", undefined, "list-card"),
      top = el("div", undefined, "list-card-head");
    const heading = el("div");
    heading.append(el("h2", list.title), el("p", list.blurb, "muted"));
    const meta = el("div", undefined, "list-meta");
    meta.append(
      el("strong", `${list.rows.length}`),
      el("span", list.rows.length === 1 ? "stock" : "stocks"),
    );
    top.append(heading, meta);
    const how = el("p", undefined, "list-how");
    how.textContent = list.asof
      ? `${list.options_clock ? "Options snapshot · mutable research" : list.preview ? "Current research preview · not a frozen forward record" : "Recorded list"} · session ${list.asof}`
      : "Waiting for the first eligible daily analysis";
    card.append(top, how);
    const rows = el("ol", undefined, "list-rows");
    if (!list.rows.length) {
      card.append(
        el(
          "p",
          list.empty_reason ||
            "No qualifying stocks recorded. Check System for missing sources or warm-up history.",
          "notice",
        ),
      );
      content.append(card);
      return;
    }
    // Draw the stored rows at once, then enrich them with company, moves and desks.
    for (const r of list.rows) rows.append(row(r, list.options_clock));
    card.append(rows);
    content.append(card);
    try {
      const detail = await api(`lists/${encodeURIComponent(id)}`);
      if (select.value !== id) return;
      if (detail.rule)
        how.textContent += ` · Rule: ${detail.rule}${detail.claim ? ` · tested for: ${detail.claim}` : ""}`;
      rows.replaceChildren(
        ...detail.rows.map((r) => row(r, list.options_clock)),
      );
    } catch (error) {
      card.append(el("p", error.message, "error"));
    }
  }
  select.onchange = draw;
  draw();
}
async function stock(symbol, ticket) {
  return KestrelResearch.stock(symbol, ticket);
}
async function paperPage(ticket) {
  return KestrelPaper.show(ticket);
}
function systemPage() {
  heading(
    "System.",
    "Source health, collection progress and the local PAPER worker.",
  );
  app.append(
    panel(
      snapshot.worker_running
        ? "Stocks worker is running"
        : "Stocks worker is stopped",
      snapshot.stop
        ? "STOP is active: pending entries are cancelled and no new entries are created. Existing positions continue to be managed when the worker runs."
        : "PAPER and collection start with the dashboard. Each strategy waits only for its required data; optional source failures remain visible.",
    ),
  );
  app.append(collectionControls());
  if (snapshot.progress)
    app.append(
      panel(
        "Current work",
        `${snapshot.progress.phase} · updated ${snapshot.progress.updated_at}`,
      ),
    );
  if (snapshot.report_errors?.length)
    app.append(
      panel(
        "Stored report needs rebuilding",
        `${snapshot.report_errors.join(", ")} report unavailable. Collected observations remain saved. Research rebuilds during collection; use Evidence → Check data health to see any blockers.`,
      ),
    );
  if (snapshot.connection.configuration_error)
    app.append(
      panel(
        "Options connection needs repair",
        snapshot.connection.configuration_error,
      ),
    );
  app.append(
    table(
      ["Source job", "Last success", "Last error"],
      Object.entries(snapshot.jobs).map(([name, j]) => [
        name,
        j.last_ok_at || "Never",
        j.last_error || "—",
      ]),
    ),
  );
  app.append(
    details(
      "Latest bounded collection pass",
      snapshot.worker || { waiting: "No pass recorded" },
    ),
  );
  if (snapshot.options_research) {
    const options = snapshot.options_research,
      coverage = options.coverage;
    app.append(
      panel(
        "Options coverage",
        `Options session ${options.asof || "not collected"} · ${coverage.same_session_options} underlyings · ${coverage.sampled_contracts} contracts · ${coverage.liquid_contracts} usable quotes · ${coverage.quote_quality_pass ?? "—"} quote-qualified underlyings · ${coverage.quality_pass} activity-qualified underlyings. Missing day volume can block activity rankings while chains and quote-based research remain usable.`,
      ),
    );
    app.append(
      table(
        [
          "Underlying",
          "Stored contracts",
          "Usable quotes",
          "Activity status",
          "Reported daily volume",
          "Quote exclusions",
        ],
        Object.entries(coverage.symbols).map(([symbol, info]) => [
          symbol,
          info.contracts,
          info.liquid_contracts,
          info.reason === "low_sampled_volume"
            ? "No positive volume or below the activity threshold"
            : info.reason?.replaceAll("_", " ") || "Qualified",
          info.provider_diagnostics?.raw_contracts &&
          !info.provider_diagnostics.volume_fields_present
            ? "Field not supplied"
            : number(info.reported_volume),
          Object.entries(info.rejected || {})
            .filter(([, count]) => count)
            .map(([reason, count]) => `${count} ${reason.replaceAll("_", " ")}`)
            .join("; ") || "None",
        ]),
      ),
    );
  }
  if (snapshot.plays_tracking)
    app.append(KestrelPlays.tracking(snapshot.plays_tracking));
  if (
    snapshot.jobs.prices?.last_error?.includes("403") ||
    snapshot.jobs.prices?.detail?.deferred_403?.length
  )
    app.append(
      panel(
        "Broad-market closing-price backfill delayed",
        "Massive denied a bulk closing-price request. This affects market-wide research and daily PAPER processing. Viewed stock charts and quotes now prefer connected Schwab data and update independently, as do options chains. Check your Massive plan's history and publication access in Settings. A per-stock Schwab download does not mark the whole market's closing prices complete.",
      ),
    );
  app.append(
    panel(
      "Automatic PAPER simulation",
      "PAPER starts with this dashboard and keeps collecting while individual strategies wait for data. Pause PAPER also pauses its collection; Start PAPER resumes both. No brokerage orders are sent.",
    ),
  );
}
async function settingsPage() {
  heading(
    "Connect your data.",
    "Keys stay on this laptop. The Stocks desk remains PAPER and cannot place a brokerage order.",
  );
  app.append(sourceSetup());
  const roles = panel(
    "What each data connection powers",
    "Sources have different coverage and clocks. A working live quote does not prove that broad-market backtests or historical option quotes are available.",
  );
  const sourceRoles = table(
    ["Source", "Used for", "Coverage and setup"],
    [
      [
        link("Schwab Developer Portal ↗", "https://developer.schwab.com/"),
        "Viewed-stock quotes, 1-minute charts, available daily chart history and option chains",
        "Authorize the brokerage login below and choose Test market-data access. Quote delay and availability depend on your entitlements. Charts can request up to 20 years where supplied.",
      ],
      [
        link("Massive stocks plans ↗", "https://massive.com/stocks"),
        "Market-wide closing bars for cross-sectional research, daily strategy PAPER and chronological stock replay",
        "Check your plan's accessible dates and publication delay. A 403 does not necessarily mean the key is wrong. A per-stock Schwab chart does not substitute for a complete market-wide session.",
      ],
      [
        link("Tradier documentation ↗", "https://docs.tradier.com/"),
        "Alternative stock/options observations",
        "Sandbox data is delayed. Use the selected provider and the timestamps shown on each observation.",
      ],
      [
        link(
          "SEC data access ↗",
          "https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
        ),
        "Dated filings and company fundamentals",
        "Save your contact email above; no paid account is required. Publication dates govern which filings a replay may use.",
      ],
      [
        "Archived historical options",
        "Required for genuine historical bid/ask, IV and option-strategy fills",
        "Not supplied by a stock price chart. This app archives observations it receives; buying a historical dataset alone will not integrate it. A separately implemented and tested import adapter is required.",
      ],
    ],
  );
  sourceRoles.classList.add("wrap-cells");
  roles.append(sourceRoles);
  app.append(roles);
  const box = panel("Schwab connection"),
    form = el("form"),
    message = el("p", undefined, "muted");
  const provider = el("select");
  provider.id = "provider";
  for (const [v, label] of [
    ["tradier", "Tradier (existing default)"],
    ["schwab", "Schwab market data"],
  ]) {
    const o = el("option", label);
    o.value = v;
    provider.append(o);
  }
  provider.value = snapshot.connection.options_provider;
  form.append(
    el(
      "p",
      "Copy Client ID into App key and Client Secret into App secret, save, then authorize with your brokerage login. Connected Schwab supplies stock-page quotes and long chart history automatically. Select Schwab below to use it for options too. No additional history API key is needed. Quotes show their actual timestamps; entitlement and market hours determine freshness.",
      "muted",
    ),
  );
  form.append(el("label", "Options data provider"), provider);
  function field(label, id, type, value) {
    const l = el("label", label);
    l.htmlFor = id;
    const input = el("input");
    input.id = id;
    input.type = type;
    input.autocomplete = type === "password" ? "new-password" : "off";
    input.spellcheck = false;
    input.value = value || "";
    form.append(l, input);
    if (type === "password") {
      const presence = el("small", undefined, "muted source-presence");
      presence.id = `${id}-presence`;
      form.append(presence);
    }
    return input;
  }
  const key = field(
      "Schwab app key (Client ID) · leave blank to preserve",
      "app-key",
      "password",
    ),
    secret = field(
      "App secret (Client Secret) · leave blank to preserve",
      "app-secret",
      "password",
    ),
    callback = field(
      "Exact registered HTTPS callback",
      "callback",
      "url",
      snapshot.connection.callback_url,
    );
  form.append(
    el(
      "p",
      "Default: https://127.0.0.1:8182/oauth/callback. No paid domain or Tailscale is needed if your Schwab app accepts it. The local certificate is self-signed; your browser will ask you to allow it for this exact local address.",
      "muted",
    ),
  );
  const save = el("button", "Save privately", "primary");
  save.id = "save-connection";
  save.type = "submit";
  message.setAttribute("role", "status");
  form.append(connectionWorkerControls(), save, message);
  form.onsubmit = async (e) => {
    e.preventDefault();
    save.dataset.saving = "true";
    save.disabled = true;
    message.textContent = "Saving…";
    try {
      snapshot.connection = await api("connection", {
        app_key: key.value,
        app_secret: secret.value,
        callback_url: callback.value,
        options_provider: provider.value,
      });
      key.value = "";
      secret.value = "";
      message.textContent =
        "Saved privately. Start browser authorization next.";
      update();
    } catch (error) {
      message.textContent = error.message;
    } finally {
      delete save.dataset.saving;
      updateConnectionControls();
    }
  };
  const connect = el("button", "Start browser authorization");
  connect.id = "connect-schwab";
  connect.type = "button";
  const cancel = el("button", "Cancel pending authorization");
  cancel.type = "button";
  cancel.disabled = true;
  const authorization = el("div");
  connect.onclick = async () => {
    connect.disabled = true;
    connect.dataset.pending = "true";
    authorization.replaceChildren();
    try {
      const data = await api("connect", {});
      const a = link("Open Schwab authorization", data.authorize_url);
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.className = "link-button";
      authorization.append(
        a,
        el(
          "p",
          "Finish within five minutes. Return here after the callback says authorization was saved. Never paste the final redirect URL into chat.",
          "muted",
        ),
      );
      message.textContent = "Login pending.";
      update();
    } catch (error) {
      delete connect.dataset.pending;
      message.textContent = error.message;
    } finally {
      updateConnectionControls();
    }
  };
  cancel.onclick = async () => {
    cancel.disabled = true;
    try {
      snapshot.connection = await api("connect/cancel", {});
      delete connect.dataset.pending;
      authorization.replaceChildren();
      message.textContent =
        "Authorization cancelled. Correct Client ID and Client Secret, then Save privately and start a new authorization.";
    } catch (error) {
      message.textContent = error.message;
    } finally {
      await update();
      updateConnectionControls();
    }
  };
  box.append(form, el("p"), connect, cancel, authorization);
  app.append(box);
  const guide = panel("Connection guide");
  const steps = el("ol");
  for (const text of [
    "Create a free developer app in the Schwab developer portal, request the appropriate market-data product and wait for its approval. KESTREL cannot grant data entitlements.",
    "Keep the dashboard terminal running. If collection is active, use Pause collector to save above. A collector started in a separate terminal must be stopped in that collector's terminal; do not stop desk stocks dashboard.",
    "Copy Client ID and Client Secret from the approved Schwab app into these fields. Choose Schwab market data, keep the registered callback identical, then click Save privately. Blank fields preserve already saved values.",
    "Click Start browser authorization, then Open Schwab authorization. Complete the broker login in your browser. Allow a self-signed warning only for your exact local callback address.",
    "Choose Test market-data access below. This checks a SPY quote through your selected connection and places no order.",
    "Using this desk from another computer over SSH? Schwab sends your browser to https://127.0.0.1:8182 after login, and that address must reach this laptop. Forward both ports, then start authorization again: ssh -N -L 8767:127.0.0.1:8767 -L 8182:127.0.0.1:8182 you@laptop",
    "If Schwab rejects the local callback registration, a free Tailscale .ts.net HTTPS hostname is the optional fallback. Read docs/SCHWAB.md before configuring Serve; KESTREL never enables public Funnel.",
  ]) {
    steps.append(el("li", text));
  }
  guide.append(
    steps,
    link("Schwab developer portal", "https://developer.schwab.com/"),
    el("p"),
    link("Full local HTTPS and optional Tailscale guide", "/connection-guide"),
  );
  app.append(guide);
  const health = panel("Authorization status");
  const output = el("div");
  health.append(output);
  app.append(health);
  async function update() {
    try {
      const data = await api("connection");
      if (location.hash === "#settings") {
        cancel.disabled = !data.login?.pending;
        connect.classList.toggle("primary", !!data.schwab.relogin_required);
        if (data.login?.pending) connect.dataset.pending = "true";
        else delete connect.dataset.pending;
        updateConnectionControls();
        output.replaceChildren(
          el(
            "p",
            data.schwab.relogin_required
              ? "Schwab sign-in expired. Log in again with Start browser authorization above."
              : data.schwab.connected
                ? "Authorization saved on this laptop."
                : "Not authorized yet.",
            data.schwab.relogin_required ? "error" : "",
          ),
          el(
            "p",
            data.schwab.credentials_set
              ? "App credentials are saved privately."
              : "App credentials are missing.",
            "muted",
          ),
          el(
            "p",
            data.login?.pending
              ? "Browser login pending · expires after five minutes."
              : data.login?.error ||
                  data.schwab.note ||
                  data.schwab.reason ||
                  "",
            "muted",
          ),
          el(
            "p",
            "After authorization, choose Test market-data access below to verify the connection.",
            "muted",
          ),
        );
        if (data.login?.pending) setTimeout(update, 3000);
      }
    } catch (error) {
      output.textContent = error.message;
    }
  }
  update();
  const test = el("button", "Test market-data access"),
    result = el("p", undefined, "muted");
  test.type = "button";
  test.onclick = async () => {
    test.disabled = true;
    result.textContent = "Checking your selected provider…";
    try {
      result.textContent = (await api("connection/test", {})).message;
    } catch (error) {
      result.textContent = error.message;
    } finally {
      test.disabled = false;
    }
  };
  health.append(test, result);
  updateConnectionControls();
}
async function render() {
  const ticket = ++generation,
    route = location.hash.replace(/^#/, "") || "dashboard";
  app.replaceChildren();
  app.className = "";
  const section = "#" + route.split("/")[0];
  for (const a of document.querySelectorAll("nav a:not(#worker-pill)")) {
    if (a.hash === section) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  moreButton.classList.toggle(
    "current",
    [...moreSheet.querySelectorAll("a")].some((a) => a.hash === section),
  );
  if (!snapshot) {
    heading("Loading…");
    return;
  }
  if (!serverCompatible) {
    const notice = panel(
      "Restart Stocks to finish updating",
      "The page files were updated, but an older Stocks server is still running. Reopen KESTREL Stocks from your application menu so the launcher loads the installed version. Saved research, trades and connection settings are preserved.",
    );
    notice.setAttribute("role", "alert");
    const check = el("button", "Check again", "primary");
    check.onclick = () => location.reload();
    notice.append(
      el(
        "p",
        "Reloading the browser alone cannot restart an older server.",
        "muted",
      ),
      check,
    );
    app.append(notice);
    return;
  }
  try {
    if (route.startsWith("stock/")) {
      heading("Loading stock…");
      await stock(decodeURIComponent(route.slice(6)), ticket);
    } else if (route === "paper") await paperPage(ticket);
    else if (route === "plays" || route.startsWith("plays/"))
      await KestrelPlays.show(ticket, route);
    else if (route === "learning") await KestrelLearning.show(ticket);
    else if (route === "proof") await KestrelEvidence.show(ticket);
    else if (route === "events") await KestrelEvents.show(ticket);
    else if (route === "settings") await settingsPage();
    else if (route === "system") systemPage();
    else if (route === "stocks" || route === "lists")
      await KestrelExplore.show(ticket);
    else if (route.startsWith("lists/")) discover(route.split("/")[1]);
    else
      await KestrelResearch.home(
        ticket,
        route === "options" ? "options" : "stocks",
      );
  } catch (error) {
    if (ticket === generation) app.append(el("p", error.message, "error"));
  }
}
async function refresh(initial = false) {
  try {
    const previous = snapshot;
    snapshot = await api("snapshot");
    snapshot.worker_running ||= snapshot.collection?.running;
    workerState();
    if (!previous) applyExplain(Boolean(snapshot.ui?.explain));
    statusLine.replaceChildren();
    statusLine.className = "";
    // Schwab ends its sign-in 7 days after the browser login; say so on every page before quotes stop.
    const schwab = snapshot.connection?.schwab || {};
    if (
      snapshot.connection?.options_provider === "schwab" &&
      schwab.credentials_set
    ) {
      const left = schwab.login_days_left;
      const warning = schwab.relogin_required
        ? "Schwab sign-in expired: live quotes and play tracking are stopped."
        : left != null && left < 1.5
          ? `Schwab sign-in ends in ${left < 1 ? `${Math.max(1, Math.round(left * 24))} hours` : "about a day"}: log in again before then so tracking has no gap.`
          : null;
      if (warning) {
        statusLine.append(
          el("strong", warning + " "),
          link("Log in again in Settings →", "#settings"),
        );
        statusLine.className = schwab.relogin_required
          ? "status-alert"
          : "status-warn";
      }
    }
    const changed =
      previous?.asof !== snapshot.asof ||
      previous?.worker?.finished_at !== snapshot.worker?.finished_at ||
      previous?.worker_running !== snapshot.worker_running ||
      JSON.stringify(previous?.collection) !==
        JSON.stringify(snapshot.collection) ||
      JSON.stringify(previous?.revision) !== JSON.stringify(snapshot.revision);
    if (
      initial ||
      (changed &&
        location.hash !== "#settings" &&
        location.hash !== "#learning" &&
        location.hash !== "#paper" &&
        !location.hash.startsWith("#plays/") &&
        !document.querySelector("dialog[open]"))
    )
      render();
    else if (location.hash === "#settings") {
      // Keep unsaved credentials intact while updating start/pause state during a long collection pass.
      for (const node of document.querySelectorAll(".collection-controls"))
        node.replaceWith(collectionControls());
      updateConnectionControls();
    }
  } catch (error) {
    statusLine.textContent = error.message;
    statusLine.className = "error";
  } finally {
    if (pauseRefresh) clearTimeout(pauseRefresh);
    pauseRefresh = snapshot?.collection?.stopping
      ? setTimeout(() => refresh(), 1000)
      : null;
  }
}
// The worker pill in the header (and the phone's More sheet): running or paused, and the latest analysed session.
function workerState() {
  const running = !!snapshot?.worker_running,
    session = snapshot?.asof
      ? new Date(snapshot.asof + "T12:00:00Z").toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          timeZone: "UTC",
        })
      : "not ready";
  const pill = document.getElementById("worker-pill");
  pill.className = running ? "running" : "paused";
  pill.querySelector("span").textContent =
    `${running ? "Running" : "Paused"} · ${session}`;
  pill.title = `PAPER worker ${running ? "running" : "paused"} · latest analysis ${snapshot?.asof || "not ready"} · ${number(snapshot?.listings || 0)} active listings`;
  document.getElementById("more-status").textContent = pill.title;
}
// Numbers first: explanatory captions hide until the ⓘ toggle shows them; the choice is saved for this desk.
const explainToggle = document.getElementById("explain-toggle"),
  moreExplain = document.getElementById("more-explain");
function applyExplain(on) {
  document.body.classList.toggle("numbers-first", !on);
  explainToggle.setAttribute("aria-pressed", String(on));
  explainToggle.title = on
    ? "Explanations shown · click to hide"
    : "Show explanations";
  moreExplain.checked = on;
}
async function setExplain(on) {
  applyExplain(on);
  try {
    snapshot.ui = await api("ui", { explain: on });
  } catch (error) {
    statusLine.textContent = error.message;
    statusLine.className = "error";
  }
}
explainToggle.onclick = () =>
  setExplain(document.body.classList.contains("numbers-first"));
moreExplain.onchange = () => setExplain(moreExplain.checked);
// Phone: the More tab opens a sheet with the sections that do not fit in the tab bar.
const moreButton = document.getElementById("more-button"),
  moreSheet = document.getElementById("more-sheet");
function closeMore() {
  moreSheet.hidden = true;
  moreButton.setAttribute("aria-expanded", "false");
}
moreButton.onclick = () => {
  const open = moreSheet.hidden;
  moreSheet.hidden = !open;
  moreButton.setAttribute("aria-expanded", String(open));
  if (open) moreSheet.querySelector("a")?.focus();
};
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !moreSheet.hidden) {
    closeMore();
    moreButton.focus();
  }
});
document.addEventListener("click", (event) => {
  if (
    !moreSheet.hidden &&
    !moreSheet.contains(event.target) &&
    !moreButton.contains(event.target)
  )
    closeMore();
});
window.addEventListener("hashchange", () => {
  closeMore();
  render();
});
(async () => {
  try {
    const session = await api("session");
    csrf = session.csrf_token;
    serverCompatible = session.ui_protocol === 9;
    await refresh(true);
  } catch (error) {
    statusLine.textContent = error.message;
  }
})();
setInterval(() => {
  if (document.visibilityState === "visible") refresh();
}, 30000);

document.getElementById("stock-search").onsubmit = (e) => {
  e.preventDefault();
  const input = document.getElementById("quick-symbol");
  if (input.value.trim())
    location.hash =
      "stock/" + encodeURIComponent(input.value.trim().toUpperCase());
};
