"use strict";
// KESTREL Arbitrage: check a price against what the item really sells for, then rank what's worth buying.

const KINDS = [
  ["item", "Anything", "Scan or type a barcode, or the item's name"],
  ["lego", "LEGO", "LEGO set number, e.g. 75192"],
  ["pokemon", "Pokémon", "Card name, optionally its number: charizard 4"],
  ["mtg", "Magic", "Card name: Sol Ring"],
  ["sneakers", "Sneakers", "Style code (DZ5485-612) or name"],
  ["metal", "Gold & silver", ""],
];
const KARATS = ["10k", "14k", "18k", "22k", "24k", "925", "999"];
let csrf = null,
  kind = "item",
  status = null,
  dealsFilter = null;

// ── helpers ─────────────────────────────────────────────────────────────────────────────────────
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
async function api(path, body) {
  if (body !== undefined && !csrf)
    csrf = (await (await fetch("/api/session")).json()).csrf_token;
  const response = await fetch(
    "/api/" + path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "content-type": "application/json", "x-csrf-token": csrf },
          body: JSON.stringify(body),
        },
  );
  const data = await response.json().catch(() => ({}));
  if (
    response.status === 404 &&
    path.startsWith("hunt/") &&
    !data.detail?.startsWith("No such")
  )
    throw new Error(
      "This window is talking to the previous version of Arbitrage. Load the update with: kestrel arbitrage",
    );
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}
function money(x) {
  if (x === null || x === undefined) return "—";
  const v = Number(x);
  return (
    (v < 0 ? "−$" : "$") +
    Math.abs(v).toLocaleString("en-US", {
      minimumFractionDigits: Math.abs(v) >= 1000 ? 0 : 2,
      maximumFractionDigits: Math.abs(v) >= 1000 ? 0 : 2,
    })
  );
}
function pct(x, signed = true) {
  if (x === null || x === undefined) return "—";
  const v = Math.round(Number(x) * 100);
  return (signed && v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v) + "%";
}
function ago(iso) {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 2) return "just now";
  if (mins < 90) return `${mins} min ago`;
  const hours = Math.round(mins / 60);
  return hours < 36 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
}
function info(summary, ...nodes) {
  const d = el("details", undefined, "info");
  d.append(el("summary", summary), ...nodes);
  return d;
}
function failure(host, error) {
  host.replaceChildren(el("p", error.message || String(error), "error"));
}

// ── status bar ──────────────────────────────────────────────────────────────────────────────────
async function loadStatus() {
  const bar = document.getElementById("status");
  try {
    status = await api("hunt/status");
  } catch (e) {
    bar.replaceChildren(el("span", e.message, "pill warn"));
    return;
  }
  const ebay = el(
    "span",
    status.ebay.paused ? "eBay: paused" : "eBay sold prices: on",
    status.ebay.paused ? "pill warn" : "pill ok",
  );
  if (status.ebay.paused) ebay.title = status.ebay.paused;
  const spot = status.spot || {};
  bar.replaceChildren(
    ebay,
    el("span", spot.gold ? `Gold ${money(spot.gold)}/oz` : "Gold —", "pill"),
    el(
      "span",
      spot.silver ? `Silver ${money(spot.silver)}/oz` : "Silver —",
      "pill",
    ),
  );
}

// ── the check bar ───────────────────────────────────────────────────────────────────────────────
function drawKinds() {
  const host = document.querySelector(".kinds");
  host.replaceChildren(
    ...KINDS.map(([id, label]) => {
      const b = el("button", label, "chip");
      b.type = "button";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-checked", String(id === kind));
      b.onclick = () => {
        kind = id;
        drawKinds();
        drawForm();
      };
      return b;
    }),
  );
}
function field(id, placeholder, mode, label) {
  const input = el("input");
  input.id = id;
  input.name = id;
  input.placeholder = placeholder;
  if (mode) input.inputMode = mode;
  input.setAttribute("aria-label", label || placeholder);
  return input;
}
function drawForm() {
  const form = document.getElementById("check"),
    result = document.getElementById("result");
  result.replaceChildren();
  if (kind === "metal") {
    const karat = el("select");
    karat.id = "karat";
    karat.setAttribute("aria-label", "Karat or silver purity");
    for (const k of KARATS)
      karat.append(
        Object.assign(
          el(
            "option",
            k === "925" ? "925 silver" : k === "999" ? "999 silver" : k,
          ),
          { value: k },
        ),
      );
    form.replaceChildren(
      karat,
      field("grams", "Weight in grams", "decimal"),
      field("price", "Tag price $", "decimal"),
      el("button", "Check"),
    );
    form.onsubmit = (e) => {
      e.preventDefault();
      checkMetal();
    };
    return;
  }
  const q = field(
    "q",
    KINDS.find((k) => k[0] === kind)[2],
    kind === "item" ? "search" : "text",
    "What to check",
  );
  const parts = [q];
  if (
    "BarcodeDetector" in window &&
    navigator.mediaDevices &&
    kind === "item"
  ) {
    const scan = el("button", "📷", "icon");
    scan.type = "button";
    scan.title = "Scan a barcode";
    scan.setAttribute("aria-label", "Scan a barcode");
    scan.onclick = () => scanBarcode(q);
    parts.push(scan);
  }
  if (kind === "pokemon" || kind === "mtg") {
    parts.push(el("button", "Find card"));
    form.onsubmit = (e) => {
      e.preventDefault();
      findCards(q.value);
    };
  } else {
    parts.push(
      field("price", "Price $", "decimal", "Price you see"),
      field("qty", "Qty", "numeric", "How many are on the shelf"),
      el("button", "Check"),
    );
    form.onsubmit = (e) => {
      e.preventDefault();
      check({ text: q.value });
    };
  }
  form.replaceChildren(...parts);
  q.focus();
}
function number(id) {
  const raw = (document.getElementById(id)?.value || "").replace(/[$,\s]/g, "");
  return raw ? Number(raw) : null;
}
async function check(body) {
  const result = document.getElementById("result");
  result.replaceChildren(
    el(
      "p",
      "Reading sold prices… (eBay is read slowly, about one search every 20 seconds)",
      "muted",
    ),
  );
  try {
    const view = await api("hunt/check", {
      ...body,
      price: body.price ?? number("price"),
      quantity: number("qty"),
      kind: kind === "item" ? null : kind,
    });
    result.replaceChildren(dealCard(view, { open: true }));
    loadStatus();
    if (location.hash === "#deals" || !location.hash) route();
  } catch (e) {
    failure(result, e);
  }
}
async function findCards(text) {
  const result = document.getElementById("result");
  if (!text.trim()) return;
  result.replaceChildren(el("p", "Searching cards…", "muted"));
  try {
    const { cards } = await api(
      `hunt/cards?game=${kind}&q=${encodeURIComponent(text)}`,
    );
    if (!cards.length)
      return result.replaceChildren(
        el("p", "No priced cards match. Try fewer words.", "muted"),
      );
    const price = field("price", "Price $", "decimal", "Price you see");
    const grid = el("div", undefined, "cards");
    for (const c of cards) {
      const b = el("button", undefined, "card-pick");
      b.type = "button";
      if (c.image)
        b.append(
          Object.assign(el("img"), { src: c.image, alt: "", loading: "lazy" }),
        );
      b.append(el("span", c.title), el("b", money(c.market)));
      b.onclick = () => check({ text: c.id, price: number("price") });
      grid.append(b);
    }
    result.replaceChildren(
      el(
        "p",
        "Type the price you see, then tap the card. Prices are TCGplayer's market price, from its recent sales.",
        "muted",
      ),
      price,
      grid,
    );
  } catch (e) {
    failure(result, e);
  }
}
async function scanBarcode(input) {
  const result = document.getElementById("result");
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: "environment" },
    });
  } catch {
    return result.replaceChildren(
      el(
        "p",
        "The camera isn't available here; type the barcode digits instead.",
        "error",
      ),
    );
  }
  const video = Object.assign(el("video", undefined, "scanner"), {
    srcObject: stream,
    playsInline: true,
    muted: true,
  });
  const stop = el("button", "Stop scanning", "ghost");
  result.replaceChildren(video, stop);
  await video.play();
  const detector = new window.BarcodeDetector({
    formats: ["upc_a", "upc_e", "ean_13", "ean_8"],
  });
  let done = false;
  const finish = () => {
    done = true;
    stream.getTracks().forEach((t) => t.stop());
  };
  stop.onclick = () => {
    finish();
    result.replaceChildren();
  };
  while (!done) {
    const codes = await detector.detect(video).catch(() => []);
    if (codes.length) {
      finish();
      input.value = codes[0].rawValue;
      document.getElementById("price")?.focus();
      result.replaceChildren(
        el(
          "p",
          `Scanned ${codes[0].rawValue}. Type the price, then Check.`,
          "muted",
        ),
      );
      return;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
}
async function checkMetal() {
  const result = document.getElementById("result");
  try {
    const out = await api("hunt/metal", {
      karat: document.getElementById("karat").value,
      grams: number("grams"),
      price: number("price"),
    });
    result.replaceChildren(metalCard(out));
    loadStatus();
  } catch (e) {
    failure(result, e);
  }
}

// ── the deal card ───────────────────────────────────────────────────────────────────────────────
const VERDICT = { buy: "BUY", maybe: "MAYBE", skip: "SKIP" };
function meter(conf) {
  const box = el("div", undefined, "meter");
  const bar = el("span", undefined, "meter-bar");
  bar.setAttribute(
    "aria-label",
    `Confidence ${conf.score} of ${conf.of}: ${conf.label}`,
  );
  for (let i = 0; i < conf.of; i++)
    bar.append(el("i", undefined, i < conf.score ? "on" : ""));
  box.append(
    bar,
    el("span", `${conf.label} · ${conf.score} of ${conf.of} checks`),
  );
  const list = el("ul", undefined, "checks");
  for (const c of conf.checks)
    list.append(
      el(
        "li",
        `${c.ok ? "✓" : "✗"} ${c.label}: ${c.detail}`,
        c.ok ? "ok" : "no",
      ),
    );
  return [box, info("Why", list)];
}
function stat(label, value, cls) {
  const s = el("div", undefined, "stat " + (cls || ""));
  s.append(el("small", label), el("b", value));
  return s;
}
function dealCard(v, { open = false } = {}) {
  const card = el(
    "article",
    undefined,
    "deal " + (v.deal ? v.deal.verdict : "waiting"),
  );
  const head = el("div", undefined, "deal-head");
  if (v.item.image)
    head.append(
      Object.assign(el("img"), { src: v.item.image, alt: "", loading: "lazy" }),
    );
  const title = el("div", undefined, "deal-title");
  const kindLabel =
    v.item.kind === "item"
      ? "Item"
      : (KINDS.find((k) => k[0] === v.item.kind) || [0, "Item"])[1];
  title.append(
    el("span", kindLabel, "tag"),
    el("h3", v.item.title),
    el(
      "small",
      v.check
        ? `${money(v.check.price)}${v.check.store ? " at " + v.check.store : ""}${v.check.quantity ? ` · ${v.check.quantity} on the shelf` : ""} · checked ${ago(v.check.at)}`
        : "No price checked yet",
      "muted",
    ),
  );
  head.append(title);
  if (v.deal) {
    const badge = el("div", undefined, "verdict");
    badge.append(
      el("b", VERDICT[v.deal.verdict]),
      el("small", v.deal.buy ? `buy up to ${v.deal.buy}` : ""),
    );
    head.append(badge);
  }
  card.append(head);
  const m = v.market;
  if (v.deal) {
    const d = v.deal;
    const row = el("div", undefined, "stats");
    row.append(
      stat("You pay", money(d.cost)),
      stat("Sells for", money(d.sells_for)),
      stat("You keep", money(d.you_keep)),
      stat(
        "Profit",
        `${money(d.profit)} · ${pct(d.profit_pct)}`,
        d.profit > 0 ? "up" : "down",
      ),
    );
    card.append(row, ...meter(d.confidence));
  } else if (m) {
    const row = el("div", undefined, "stats");
    row.append(
      stat("Sells for", money(m.value)),
      stat("Range", `${money(m.low)}–${money(m.high)}`),
    );
    card.append(
      row,
      el(
        "p",
        "Type the price you see in the check bar to get the profit and a verdict.",
        "muted",
      ),
    );
  }
  if (m) {
    const speed =
      m.basis === "market_price"
        ? "TCGplayer market price (from its recent sales)"
        : `${m.count} sold in 90 days · about ${m.per_week ?? "—"} a week · most sell ${money(m.low)}–${money(m.high)}`;
    const line = el("p", speed, "muted");
    if (m.trend !== null && m.trend !== undefined && Math.abs(m.trend) >= 0.01)
      line.append(
        el(
          "span",
          ` · prices ${pct(m.trend)} a month`,
          m.trend > 0 ? "up" : "down",
        ),
      );
    card.append(line);
  } else {
    card.append(waiting(v));
  }
  card.append(actions(v, card), details(v, open));
  return card;
}
function waiting(v) {
  const box = el("div", undefined, "waiting-box");
  const st = v.status;
  box.append(
    el(
      "p",
      st
        ? st.note ||
            (st.status === "empty"
              ? "No sales in the last 90 days"
              : "No sold prices read yet")
        : "Sold prices are being read.",
      "muted",
    ),
  );
  box.append(pasteBox(v));
  return box;
}
function pasteBox(v) {
  const wrap = el("div", undefined, "paste");
  const link = Object.assign(el("a", "Open eBay's sold results ↗"), {
    href: v.sold_url,
    target: "_blank",
    rel: "noopener",
  });
  const area = el("textarea");
  area.placeholder = "On eBay's sold results: select all, copy, and paste here";
  area.setAttribute("aria-label", "Pasted eBay sold results");
  const go = el("button", "Use pasted sales", "ghost");
  go.type = "button";
  go.onclick = async () => {
    try {
      const view = await api("hunt/paste", {
        item_id: v.item.id,
        text: area.value,
      });
      wrap.closest("article").replaceWith(dealCard(view, { open: true }));
    } catch (e) {
      wrap.append(el("p", e.message, "error"));
    }
  };
  wrap.append(link, area, go);
  return wrap;
}
function actions(v, card) {
  const row = el("div", undefined, "actions");
  const watch = el("button", v.item.watch ? "★ Watching" : "☆ Watch", "ghost");
  watch.type = "button";
  watch.title = "Watched items' sold prices refresh daily and feed Buy & Hold";
  watch.onclick = async () =>
    card.replaceWith(
      dealCard(
        await api(`hunt/item/${v.item.id}/flag`, {
          flag: "watch",
          on: !v.item.watch,
        }),
      ),
    );
  const refresh = el("button", "↻ Sold prices", "ghost");
  refresh.type = "button";
  refresh.title = `Read now (last read ${ago(v.comps_at)})`;
  refresh.onclick = async () => {
    refresh.disabled = true;
    try {
      card.replaceWith(
        dealCard(await api(`hunt/item/${v.item.id}/refresh`, {}), {
          open: true,
        }),
      );
    } catch (e) {
      refresh.disabled = false;
      card.append(el("p", e.message, "error"));
    }
  };
  const ebay = Object.assign(el("a", "eBay sold ↗", "ghost"), {
    href: v.sold_url,
    target: "_blank",
    rel: "noopener",
  });
  const hide = el("button", "Hide", "ghost");
  hide.type = "button";
  hide.onclick = async () => {
    await api(`hunt/item/${v.item.id}/flag`, { flag: "hidden", on: true });
    card.remove();
  };
  row.append(watch, refresh, ebay, hide);
  return row;
}
function details(v, open) {
  const box = el("div");
  const d = v.deal;
  if (d) {
    const t = el("table", undefined, "breakdown");
    for (const [label, value] of [
      ["Price + your sales tax", money(d.cost)],
      ["Typical sale (buyer's total)", money(d.sells_for)],
      ["eBay fees", "−" + money(d.fees)],
      ["Your shipping label", "−" + money(d.shipping)],
      ["You keep", money(d.you_keep)],
      ["Profit", money(d.profit)],
      ["If it sells at the low end", money(d.profit_quick)],
    ]) {
      const tr = el("tr");
      tr.append(el("td", label), el("td", value));
      t.append(tr);
    }
    box.append(info("Fees and profit", t));
  }
  if (v.sales?.length) {
    const list = el("ul", undefined, "sales");
    for (const s of v.sales)
      list.append(
        el(
          "li",
          `${s.sold_on || "—"} · ${money(s.price + (s.shipping || 0))} · ${s.title}`,
        ),
      );
    box.append(info(`${v.sales.length} newest sales`, list));
  }
  if (v.market && v.item.category !== "cards")
    box.append(info("Paste newer sales", pasteBox(v)));
  if (open) box.querySelector("details")?.setAttribute("open", "");
  return box;
}
function metalCard(out) {
  const card = el("article", undefined, "deal " + out.verdict);
  const head = el("div", undefined, "deal-head");
  const title = el("div", undefined, "deal-title");
  title.append(
    el("span", out.metal === "gold" ? "Gold" : "Silver", "tag"),
    el("h3", "Against its melt value"),
  );
  const badge = el("div", undefined, "verdict");
  badge.append(
    el("b", VERDICT[out.verdict]),
    el("small", `pay at most ${money(out.break_even_price)}`),
  );
  head.append(title, badge);
  const row = el("div", undefined, "stats");
  row.append(
    stat("You pay", money(out.cost)),
    stat("Melt value", money(out.melt)),
    stat("Scrap buyer pays", money(out.scrap_payout)),
    stat(
      "Profit",
      `${money(out.profit)} · ${pct(out.profit_pct)}`,
      out.profit > 0 ? "up" : "down",
    ),
  );
  card.append(
    head,
    row,
    el(
      "p",
      `Spot ${money(out.spot[out.metal])}/oz (${ago(out.spot.at)}). Scrap buyers pay a share of melt; set yours in Settings.`,
      "muted",
    ),
  );
  return card;
}

// ── views ───────────────────────────────────────────────────────────────────────────────────────
async function showDeals(view) {
  const filters = [
    [null, "All"],
    ["item", "Clearance & items"],
    ["lego", "LEGO"],
    ["cards", "Cards"],
    ["sneakers", "Sneakers"],
  ];
  const chips = el("div", undefined, "filters");
  for (const [id, label] of filters) {
    const b = el("button", label, "chip");
    b.type = "button";
    b.setAttribute("aria-pressed", String(id === dealsFilter));
    b.onclick = () => {
      dealsFilter = id;
      route();
    };
    chips.append(b);
  }
  const { items } = await api(
    "hunt/deals" + (dealsFilter ? `?kind=${dealsFilter}` : ""),
  );
  const buys = items.filter((v) => v.deal?.verdict === "buy");
  view.replaceChildren(
    el("h2", "Deals"),
    el(
      "p",
      items.length
        ? `${buys.length} worth buying of ${items.length} checked · ranked by profit after every fee`
        : "Nothing checked yet. Scan or type something in the bar above, with the price you see.",
      "muted",
    ),
    chips,
    ...items.map((v) => dealCard(v)),
  );
}
async function showHolds(view) {
  const { items } = await api("hunt/holds");
  const intro = el(
    "p",
    "Items whose sold prices have been rising, steepest first: at least 10% a year over the last 90 days of sales (or a month of a card's market prices). Trends can reverse; this is where to look, not a promise. Retired LEGO sets have historically gained about 11% a year, usually starting 2–3 years after retirement.",
    "muted",
  );
  const cards = items.map((v) => {
    const card = dealCard(v);
    const m = v.market;
    const yearOut = m.value * (1 + m.yearly);
    card.insertBefore(
      el(
        "p",
        `Rising ${pct(m.yearly)} a year · ${money(m.value)} now → about ${money(yearOut)} in a year if it continues`,
        "rise",
      ),
      card.children[1],
    );
    return card;
  });
  view.replaceChildren(
    el("h2", "Buy & Hold"),
    intro,
    ...(cards.length
      ? cards
      : [
          el(
            "p",
            "No rising items yet. Watch items (☆) and their sold prices are tracked daily.",
            "muted",
          ),
        ]),
  );
}
async function showMetals(view) {
  status = await api("hunt/status");
  const spot = status.spot || {};
  const payout = status?.settings?.scrap_payout ?? 0.8;
  const tax = status?.settings?.purchase_tax ?? 0.07;
  const table = el("table", undefined, "breakdown");
  const head = el("tr");
  head.append(
    el("th", "Purity"),
    el("th", "Melt per gram"),
    el("th", "Pay at most per gram"),
  );
  table.append(head);
  const purity = {
    "10k": 10 / 24,
    "14k": 14 / 24,
    "18k": 18 / 24,
    "22k": 22 / 24,
    "24k": 0.999,
    925: 0.925,
    999: 0.999,
  };
  for (const k of KARATS) {
    const ounce = k === "925" || k === "999" ? spot.silver : spot.gold;
    if (!ounce) continue;
    const perGram = (ounce / 31.1034768) * purity[k];
    const tr = el("tr");
    tr.append(
      el("td", k === "925" || k === "999" ? `${k} silver` : k),
      el("td", money(perGram)),
      el("td", money((perGram * payout) / (1 + tax) / 1.15)),
    );
    table.append(tr);
  }
  view.replaceChildren(
    el("h2", "Gold & silver"),
    el(
      "p",
      `Pick "Gold & silver" in the check bar, then the karat, weight and tag price. "Pay at most" leaves a 15% profit when a scrap buyer pays ${Math.round(payout * 100)}% of melt, after your ${Math.round(tax * 100)}% sales tax. Weigh pieces on a pocket scale; stones and fillers aren't gold.`,
      "muted",
    ),
    table,
  );
}
async function showSettings(view) {
  status = await api("hunt/status");
  const s = status.settings;
  const rows = [
    ["purchase_tax", "Sales tax where you buy (%)", 100],
    [
      "buyer_tax",
      "Sales tax eBay charges buyers, which its fee includes (%)",
      100,
    ],
    ["min_profit_pct", "Smallest profit share to call it a BUY (%)", 100],
    ["min_profit_usd", "Smallest profit in dollars to call it a BUY ($)", 1],
    ["scrap_payout", "What a scrap buyer pays, as a share of melt (%)", 100],
    [
      "bankroll",
      "Bankroll: no single buy uses more than a quarter of it ($, blank for none)",
      1,
    ],
  ];
  const form = el("form", undefined, "settings");
  for (const [key, label, scale] of rows) {
    const l = el("label", label);
    const input = field(key, "", "decimal", label);
    input.value =
      s[key] === null || s[key] === undefined
        ? ""
        : Math.round(s[key] * scale * 100) / 100;
    l.append(input);
    form.append(l);
  }
  const saved = el("p", "", "muted");
  form.append(el("button", "Save"), saved);
  form.onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    for (const [key, , scale] of rows) {
      const raw = document.getElementById(key).value.trim();
      body[key] =
        raw === ""
          ? key === "bankroll"
            ? null
            : undefined
          : Number(raw) / scale;
    }
    try {
      await api("hunt/settings", body);
      saved.textContent = "Saved.";
      loadStatus();
    } catch (err) {
      saved.textContent = err.message;
    }
  };
  const bg = status.background;
  view.replaceChildren(
    el("h2", "Settings"),
    form,
    el(
      "p",
      `eBay sold prices are read from this computer, one search every 20 seconds at most, with an honest identity. If eBay refuses, reading pauses 6 hours and pasting still works. ${status.ebay.paused || ""} Background refresh: ${bg.running ? "on" : "off"}${bg.error ? " — " + bg.error : ""}.`,
      "muted",
    ),
  );
}
async function route() {
  const view = document.getElementById("view");
  const page = (location.hash || "#deals").slice(1);
  for (const a of document.querySelectorAll(".tabs a"))
    a.setAttribute(
      "aria-current",
      String(a.getAttribute("href") === "#" + page),
    );
  try {
    await (
      {
        deals: showDeals,
        holds: showHolds,
        metals: showMetals,
        settings: showSettings,
      }[page] || showDeals
    )(view);
  } catch (e) {
    failure(view, e);
  }
}

window.addEventListener("hashchange", route);
drawKinds();
drawForm();
loadStatus();
route();
