"use strict";
const app = document.getElementById("app"),
  statusLine = document.getElementById("status");
let snapshot = null,
  csrf = "",
  generation = 0;
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
  for (const h of headers) tr.append(el("th", h));
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
async function api(path, body) {
  const response = await fetch(`/api/stocks/${path}`, {
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
  const value = await response.json();
  if (!response.ok)
    throw Error(value.detail || `Request failed (${response.status})`);
  return value;
}
function heading(title, subtitle) {
  app.append(el("h1", title));
  if (subtitle) app.append(el("p", subtitle, "muted"));
}
function chart(bars) {
  const values = bars.map((b) => b.close).filter(Number.isFinite);
  if (values.length < 2)
    return el("p", "Not enough stored price history yet.", "muted");
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg"),
    line = document.createElementNS(svg.namespaceURI, "polyline");
  svg.setAttribute("viewBox", "0 0 1000 250");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "chart");
  svg.setAttribute("role", "img");
  svg.setAttribute(
    "aria-label",
    "Stored daily closing prices, observed bars only; not executable quotes",
  );
  const lo = Math.min(...values),
    range = Math.max(...values) - lo || 1;
  line.setAttribute(
    "points",
    values
      .map(
        (v, i) =>
          `${(i / (values.length - 1)) * 1000},${240 - ((v - lo) / range) * 230}`,
      )
      .join(" "),
  );
  svg.append(line);
  return svg;
}
function discover() {
  heading(
    "Find the evidence.",
    "Daily research lists from eleven deterministic desks. No data is invented to fill missing observations.",
  );
  const search = el("form", undefined, "row"),
    input = el("input");
  input.placeholder = "Look up a stored stock · e.g. AAPL";
  input.setAttribute("aria-label", "Stock symbol");
  input.maxLength = 12;
  const button = el("button", "Open stock");
  search.append(input, button);
  search.onsubmit = (e) => {
    e.preventDefault();
    location.hash = `stock/${encodeURIComponent(input.value.trim().toUpperCase())}`;
  };
  app.append(search);
  const select = el("select");
  select.setAttribute("aria-label", "Research list");
  for (const list of snapshot.lists) {
    const option = el("option", list.title);
    option.value = list.id;
    select.append(option);
  }
  const content = el("div");
  function draw() {
    const list = snapshot.lists.find((l) => l.id === select.value);
    content.replaceChildren();
    if (!list) return;
    const box = panel(list.title, list.blurb);
    box.append(
      el(
        "small",
        list.asof
          ? `Closed session: ${list.asof}`
          : "Waiting for the first eligible daily analysis",
      ),
    );
    if (list.rows.length)
      box.append(
        table(
          ["Rank", "Stock", "Reference close", "Why it appears"],
          list.rows.map((r) => [
            r.rank,
            link(r.symbol, `#stock/${encodeURIComponent(r.symbol)}`),
            money(r.price),
            r.reason,
          ]),
        ),
      );
    else
      box.append(
        el(
          "p",
          "No qualifying stocks recorded. Check System for missing sources or warm-up history.",
          "notice",
        ),
      );
    content.append(box);
  }
  select.onchange = draw;
  app.append(select, content);
  draw();
}
async function stock(symbol, ticket) {
  const data = await api(`stock/${encodeURIComponent(symbol)}`);
  if (ticket !== generation) return;
  app.replaceChildren();
  const d = data.analysis;
  heading(
    d?.name || symbol,
    `${symbol} · ${d?.sector || "Stored market history"}`,
  );
  app.append(
    el("div", money(d?.price?.close ?? data.bars.at(-1)?.close), "big"),
  );
  app.append(chart(data.bars));
  app.append(
    el(
      "p",
      "Daily chart prices are reference observations, not broker entry or exit quotes.",
      "muted",
    ),
  );
  if (!d) {
    app.append(
      panel(
        "Analysis not ready",
        "A stock needs sufficient history, volume and source coverage before a research score is available.",
      ),
    );
    return;
  }
  const council = panel(
    "Committee",
    `Closed session ${d.asof}. Coverage measures data completeness, not the chance of profit.`,
  );
  council.append(
    el("div", number(d.committee?.score), "desk-score"),
    details("Committee rationale and weights", {
      committee: d.committee,
      weights: d.weights,
      bull: d.bull,
      bear: d.bear,
      change_view: d.change_view,
    }),
  );
  app.append(council);
  const grid = el("div", undefined, "grid");
  for (const [id, label] of Object.entries(snapshot.desks)) {
    const desk = d.desks[id],
      card = panel(label);
    card.append(
      el("div", number(desk?.score), "desk-score"),
      el("p", desk?.verdict || "Insufficient data", "muted"),
    );
    if (desk) {
      const bar = el("progress");
      bar.max = 1;
      bar.value = desk.confidence || 0;
      bar.setAttribute("aria-label", `${label} data coverage`);
      card.append(
        bar,
        el("small", `${percent(desk.confidence)} data coverage`),
      );
      for (const reason of desk.reasons || []) card.append(el("p", reason));
    }
    grid.append(card);
  }
  app.append(
    grid,
    details("Underlying observations", {
      fundamentals: d.fundamentals,
      options: d.options,
      events: d.events,
      macro: d.macro,
    }),
  );
}
async function paperPage(ticket) {
  heading(
    "Paper portfolios.",
    "Separate research experiments: $10,000 stock accounts and $50,000 options accounts. These are not the $500 crypto reference account.",
  );
  const data = await api("paper");
  if (ticket !== generation) return;
  const accounts = data.report?.accounts || [];
  if (!accounts.length) {
    app.append(
      panel(
        "The forward record starts here",
        "Run the Stocks PAPER worker after connecting the free sources. Historical prices do not create an instant forward trading record.",
      ),
    );
    return;
  }
  app.append(
    table(
      [
        "Strategy",
        "Simulated equity",
        "Net return",
        "Drawdown",
        "Sessions",
        "Evidence grade",
      ],
      accounts.map((a) => [
        a.title,
        money(a.equity),
        percent(a.return),
        percent(a.max_drawdown),
        a.sessions,
        typeof a.grade === "object"
          ? a.grade.label || a.grade.title || JSON.stringify(a.grade)
          : a.grade,
      ]),
    ),
  );
  app.append(
    details("Recent paper decisions and positions", {
      positions: data.positions,
      decisions: data.decisions,
    }),
    details("Report, costs and uncertainty", data.report),
  );
}
async function proofPage(ticket) {
  heading(
    "Evidence before confidence.",
    "Historical reconstruction, synthetic checks and genuine forward observations are different records.",
  );
  const data = await api("proof");
  if (ticket !== generation) return;
  app.append(
    panel(
      "Validation status",
      data.validation
        ? "A stored validation report is available below. Inspect its caveats, uncertainty and source coverage before interpreting any grade."
        : "No validation report yet. After history is collected, run: uv run desk stocks validate",
    ),
  );
  if (data.validation)
    app.append(details("Stored validation report", data.validation));
  app.append(details("Recent research runs", data.runs));
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
        : "The dashboard only reads saved records. It does not start a collector.",
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
  const guide = panel("Start the separate Stocks worker");
  guide.append(
    el(
      "pre",
      "cd ~/kestrel\nuv run desk stocks doctor\nuv run desk stocks paper --minutes 1440",
    ),
    el(
      "p",
      "Keep this terminal and the laptop running. This command collects bounded batches; it does not make the laptop stay awake.",
      "muted",
    ),
  );
  app.append(guide);
}
async function settingsPage() {
  heading(
    "Connect your data.",
    "Keys stay on this laptop. The Stocks desk remains PAPER and cannot place a brokerage order.",
  );
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
  form.append(el("label", "Options data provider"), provider);
  function field(label, id, type, value) {
    const l = el("label", label);
    l.htmlFor = id;
    const input = el("input");
    input.id = id;
    input.type = type;
    input.autocomplete = "off";
    input.spellcheck = false;
    input.value = value || "";
    form.append(l, input);
    return input;
  }
  const key = field(
      "Schwab app key · leave blank to preserve",
      "app-key",
      "password",
    ),
    secret = field(
      "App secret · leave blank to preserve",
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
  save.type = "submit";
  form.append(save);
  form.onsubmit = async (e) => {
    e.preventDefault();
    save.disabled = true;
    message.textContent = "Saving…";
    try {
      await api("connection", {
        app_key: key.value,
        app_secret: secret.value,
        callback_url: callback.value,
        options_provider: provider.value,
      });
      key.value = "";
      secret.value = "";
      message.textContent =
        "Saved privately. Start browser authorization next.";
    } catch (error) {
      message.textContent = error.message;
    } finally {
      save.disabled = false;
    }
  };
  const connect = el("button", "Start browser authorization");
  connect.type = "button";
  const authorization = el("div");
  connect.onclick = async () => {
    connect.disabled = true;
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
      message.textContent = error.message;
    } finally {
      connect.disabled = false;
    }
  };
  box.append(form, el("p"), connect, authorization, message);
  app.append(box);
  const guide = panel("Connection guide");
  const steps = el("ol");
  for (const text of [
    "Create a free developer app in the Schwab developer portal, request the appropriate market-data product and wait for its approval. KESTREL cannot grant data entitlements.",
    "Try registering the exact default HTTPS callback above. Copy the approved app key and secret into this local form, select Schwab and save. Stop the Stocks worker with Ctrl+C before changing its connection.",
    "Click Start browser authorization, then Open Schwab authorization. Complete the broker login in your browser. Allow a self-signed warning only for your exact local callback address.",
    "Check the connection below, then run uv run desk stocks doctor --online. That makes one SPY market quote request and places no order.",
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
  const source = panel(
    "Other free research sources",
    "Schwab supplies quotes and option chains. The existing broad end-of-day research uses a free Massive Stocks Basic key and an SEC contact email; missing inputs stay missing.",
  );
  source.append(
    table(
      ["Private setting", "Present"],
      Object.entries(snapshot.source_keys).map(([k, v]) => [
        k,
        v ? "Yes" : "No",
      ]),
    ),
    el(
      "p",
      "Edit ~/.config/desk/secrets.env on the laptop for these settings, then run chmod 600 ~/.config/desk/secrets.env. Keep existing keys. Source setup and limits are in docs/STOCKS.md and docs/STOCKS_SOURCES.md.",
      "muted",
    ),
  );
  app.append(source);
  const health = panel("Authorization status");
  const output = el("div");
  health.append(output);
  app.append(health);
  async function update() {
    try {
      const data = await api("connection");
      if (location.hash === "#settings") {
        output.replaceChildren(
          el(
            "p",
            data.schwab.connected
              ? "Authorization saved on this laptop."
              : "Not authorized yet.",
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
                  data.schwab.reason ||
                  data.schwab.note ||
                  "",
            "muted",
          ),
          el(
            "p",
            "After authorization, run uv run desk stocks doctor --online to verify market-data access.",
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
}
async function render() {
  const ticket = ++generation,
    route = location.hash.replace(/^#/, "") || "lists";
  app.replaceChildren();
  for (const a of document.querySelectorAll("nav a")) {
    if (a.hash === "#" + route) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (!snapshot) {
    heading("Loading…");
    return;
  }
  try {
    if (route.startsWith("stock/")) {
      heading("Loading stock…");
      await stock(decodeURIComponent(route.slice(6)), ticket);
    } else if (route === "paper") await paperPage(ticket);
    else if (route === "proof") await proofPage(ticket);
    else if (route === "settings") await settingsPage();
    else if (route === "system") systemPage();
    else discover();
  } catch (error) {
    if (ticket === generation) app.append(el("p", error.message, "error"));
  }
}
async function refresh(initial = false) {
  try {
    const previous = snapshot;
    snapshot = await api("snapshot");
    statusLine.textContent = `PAPER · ${snapshot.worker_running ? "Worker running" : "Worker stopped"} · Latest analysis: ${snapshot.asof || "not ready"} · ${snapshot.listings} active listings`;
    statusLine.className = "";
    const changed =
      previous?.asof !== snapshot.asof ||
      previous?.worker?.finished_at !== snapshot.worker?.finished_at;
    if (initial || (changed && location.hash !== "#settings")) render();
  } catch (error) {
    statusLine.textContent = error.message;
    statusLine.className = "error";
  }
}
window.addEventListener("hashchange", render);
(async () => {
  try {
    csrf = (await api("session")).csrf_token;
    await refresh(true);
  } catch (error) {
    statusLine.textContent = error.message;
  }
})();
setInterval(() => {
  if (document.visibilityState === "visible") refresh();
}, 30000);
