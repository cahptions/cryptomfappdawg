"use strict";
const $ = (s) => document.querySelector(s);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (v, currency = state?.settings.currency || "USD") =>
  v == null
    ? "Unknown"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency,
      }).format(Number(v));
document.addEventListener("error", (event) => {
  if (event.target instanceof HTMLImageElement && event.target.closest(".product-photo")) {
    const fallback = document.createElement("span");
    fallback.className = "no-photo";
    fallback.textContent = "Source photo unavailable";
    event.target.replaceWith(fallback);
  }
}, true);
let state = null,
  csrf = "",
  editing = false,
  productHistory = null,
  setupGuides = [];
const notice = (message, error = false) => {
  $("#notice").textContent = message;
  $("#notice").className = error ? "error" : "";
};
async function api(path, body, retry = true) {
  const response = await fetch(
    path,
    body === undefined
      ? {}
      : {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
          body: JSON.stringify(body),
        },
  );
  if (response.status === 403 && body !== undefined && retry) {
    csrf = (await api("/api/session")).csrf_token;
    return api(path, body, false);
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(
      `Local request failed (HTTP ${response.status}); the response was not readable JSON`,
    );
  }
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}
const field = (name, label, type = "text", value = "", required = true) =>
  `<label>${esc(label)}<input name="${esc(name)}" type="${type}" value="${esc(value)}" ${type === "number" ? 'step="any" min="0"' : ""} ${required ? "required" : ""}></label>`;
const availability = (o) =>
  Date.now() - Date.parse(o.at) > 300000 ||
  (o.verified_until && Date.parse(o.verified_until) < Date.now())
    ? "STALE · " + o.availability
    : o.availability;
const check = (name, label, value = false) =>
  `<label class="check"><input type="checkbox" name="${name}" ${value ? "checked" : ""}>${esc(label)}</label>`;
const picker = () =>
  `<label>Product<select name="product_id" required>${state.board.map((b) => `<option value="${esc(b.product.id)}">${esc(b.product.title)}</option>`).join("")}</select></label>`;
const external = (url, label) =>
  url
    ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)} ↗</a>`
    : "";
function modal(html) {
  $("#edit-content").innerHTML = html;
  $("#editor").showModal();
  editing = true;
}
$("#editor").addEventListener("close", () => {
  editing = false;
});
async function reload() {
  state = await api("/api/snapshot");
  render();
}
function status() {
  const jobs = Object.values(state.jobs).filter(
    (j) => j.status === "unavailable",
  );
  notice(
    state.coverage.analysis_error ||
      state.collector.error ||
      (!state.settings.enabled
        ? "Observation collection paused. Cached evidence remains available."
        : jobs.length
          ? `${jobs.length} source unavailable. Open Sources & system for the reason. Cached observations remain dated.`
          : state.collector.external
            ? "Another Arbitrage collector owns the lock; this dashboard reads its progress."
            : state.collector.active
              ? "Observation collector running. Availability and missing costs are checked independently."
              : "Collector inactive; enable it in Business settings."),
    Boolean(state.coverage.analysis_error || state.collector.error),
  );
  $("#stats").innerHTML = [
    [
      "AVAILABLE BUSINESS CAPITAL",
      state.settings.capital == null
        ? "Not set"
        : money(state.basket.cash_available),
      "Separate from trading accounts",
    ],
    [
      "OBSERVED PRODUCTS",
      state.board.length,
      "Recorded observations; no sample products",
    ],
    [
      "QUALIFIED ROUTES",
      state.qualified_count,
      "Reviewed scenarios; estimates only",
    ],
    [
      "INVENTORY COMMITTED",
      money(state.inventory.committed),
      "Includes quarantined returns",
    ],
  ]
    .map(
      ([label, n, note]) =>
        `<div class="stat"><div class="caption">${esc(label)}</div><strong>${esc(n)}</strong><div class="caption">${esc(note)}</div></div>`,
    )
    .join("");
}
function board(rows, qualified = false) {
  if (!rows.length)
    return `<div class="empty"><h3>${qualified ? "No qualified routes yet" : "Waiting for the first real observation"}</h3><p>${qualified ? "Product match, delivery availability, seller eligibility and complete costs must pass before an item enters this list." : "The read-only source adapter starts automatically. Network or retailer restrictions appear in Sources & system. You can also record a real observation there."}</p><a href="#sources">Inspect sources ↗</a></div>`;
  return `<div class="deal-grid">${rows.slice(0, 100).map((b) => {
    const o = b.offers[0], r = b.routes[0], p = b.product;
    const image = p.image_url && /^https:\/\//.test(p.image_url)
      ? `<img src="${esc(p.image_url)}" alt="${esc(p.title)}" loading="lazy" referrerpolicy="no-referrer">` : `<span class="no-photo">No source photo yet</span>`;
    const fresh = o && !availability(o).startsWith("STALE") && o.availability !== "UNAVAILABLE";
    return `<article class="deal-card"><a class="product-photo" href="#product/${esc(p.id)}">${image}</a><div class="deal-body"><span class="eyebrow">${esc(p.category)} · ${esc(p.condition)}</span><h3><a href="#product/${esc(p.id)}">${esc(p.title)}</a></h3><div class="deal-price">${o ? esc(money(o.price, o.currency)) : "Price unavailable"}<span>source asking price</span></div><span class="badge ${fresh ? "good" : "warn"}">${o ? esc(availability(o)) : "No offer"}</span><dl class="deal-metrics"><div><dt>Sell on</dt><dd>${r ? esc(r.channel) : "Compare destinations"}</dd></div><div><dt>Estimated net / unit</dt><dd>${r ? esc(money(r.net_profit)) : "Needs costs"}</dd></div><div><dt>Stress net / unit</dt><dd>${r ? esc(money(r.stress_profit)) : "Unknown"}</dd></div><div><dt>Demand evidence</dt><dd>${b.heat.length ? esc(b.heat[0].rating) : "Not connected"}</dd></div></dl><p class="next-step">${!p.identity_confirmed ? "Next: check exact model, pack and condition." : !r ? "Next: compare a selling destination and costs." : r.qualifies ? "Ready for a fresh delivery and price check." : esc(r.blockers[0] || "Review scenario")}</p><div class="toolbar"><a class="button" href="#product/${esc(p.id)}">Research item</a>${o ? external(o.url, "Check source") : ""}</div><span class="caption">${o ? "Observed " + esc(new Date(o.at).toLocaleString()) : "No dated observation"} · Not reserved</span></div></article>`;
  }).join("")}</div>`;
}
function render() {
  status();
  const [page, id] = location.hash.slice(1).split("/");
  const route = page || "home";
  document
    .querySelectorAll("nav a")
    .forEach((a) => a.classList.toggle("active", a.hash === "#" + route));
  const el = $("#content");
  if (route === "home" || route === "research" || route === "top") {
    const qualified = route === "top";
    const rows = state.board.filter(
      (b) => !qualified || b.routes.some((r) => r.qualifies),
    );
    el.innerHTML = `<div class="grid"><section class="panel"><h2>${qualified ? "Top opportunities · up to 100" : route === "research" ? "Product research" : "Your sourcing desk"}</h2><p class="caption">${qualified ? "Qualified reviewed scenarios only. Empty positions are never padded." : "Retailer observations first. Unknown fields stay visible while research grows."}</p><div class="toolbar"><label>Search products<input id="product-search" type="search" placeholder="Brand, model or product"></label><label>Show<select id="offer-filter"><option value="all">All research candidates</option><option value="fresh">Recent available listings</option><option value="qualified">Reviewed profitable scenarios</option></select></label><label>Category<select id="category"><option value="">All categories</option>${[...new Set(rows.map((b) => b.product.category))].map((c) => `<option>${esc(c)}</option>`).join("")}</select></label><button data-action="observation">Record real observation</button></div><div id="board">${board(rows, qualified)}</div></section><aside class="panel"><h2>Find → compare → sell</h2><ol class="checks"><li><a href="#sources">Choose your sourcing searches</a></li><li>Open an item and check its exact model.</li><li>Compare selling destinations after costs.</li><li>Recheck stock before committing cash.</li></ol><h3>Where to sell</h3><p>Compare any low-cost eligible destination using its own fees, fulfillment and payout timing.</p><a href="#profitability">Compare selling routes ↗</a><div class="line"><span>Estimated basket net</span><span class="good">${esc(money(state.basket.estimated_profit))}</span></div><div class="line"><span>Keep-cash alternative</span><span>${esc(money(state.basket.cash_available))}</span></div><p class="caption">${esc(state.coverage.scope)}</p><a href="#sources">Coverage & source health ↗</a></aside></div>`;
    const filterBoard = () => {
      const q = $("#product-search").value.toLowerCase().trim(), category = $("#category").value, mode = $("#offer-filter").value;
      const filtered = rows.filter(b => (!category || b.product.category === category) && b.product.title.toLowerCase().includes(q) && (mode !== "qualified" || b.routes.some(r => r.qualifies)) && (mode !== "fresh" || b.offers.some(o => !availability(o).startsWith("STALE") && ["DECLARED_AVAILABLE", "VERIFIED_AVAILABLE"].includes(o.availability))));
      $("#board").innerHTML = filtered.length ? board(filtered, qualified) : '<div class="empty">No items match these filters. Try another search or broaden your sources.</div>';
    };
    $("#category").onchange = filterBoard;
    $("#offer-filter").onchange = filterBoard;
    $("#product-search").oninput = filterBoard;
  } else if (route === "product") {
    const b = state.board.find((x) => x.product.id === id);
    if (!b) {
      el.innerHTML = '<div class="panel">Product not found.</div>';
      return;
    }
    el.innerHTML = `<section class="panel"><p class="eyebrow">EXACT PRODUCT DOSSIER</p><h2>${esc(b.product.title)}</h2><p>${esc(b.product.variant)} · Pack ${esc(b.product.pack_count ?? "unknown")} · ${esc(b.product.condition)}</p><div class="toolbar"><button data-action="identity" data-id="${esc(id)}">Review identity</button><button data-action="route" data-id="${esc(id)}">Add selling scenario</button><button data-action="signal" data-id="${esc(id)}">Import evidence</button></div><h3>Observed offers</h3>${b.offers.map((o) => `<div class="line"><div>${external(o.url, "Open product")} · ${esc(money(o.price, o.currency))}<br><span class="caption">${esc(availability(o))} · ${esc(o.method)} · ${esc(o.at)}</span><br><span class="caption">${esc(o.evidence)}</span></div><button data-action="verify" data-id="${o.id}">Record delivery check</button></div>`).join("")}<h3>Asking prices and reported sales</h3><div id="history">Loading recorded history…</div><p class="caption">Dots show actual observation times. Gaps remain gaps. No historical data is manufactured. Points before identity review are not assumed to be exact-product comparables.</p><h3>Heat evidence</h3>${b.heat.map((m) => `<div class="metric"><span>${esc(m.channel)} / ${esc(m.kind)}<br><span class="caption">${m.count} observations / ${m.days} days; cohort ${m.cohort_size} · ${esc(m.basis)}</span></span><span>${esc(m.rating)} · ${esc(m.value)}</span></div>`).join("") || "<p>Insufficient volume or popularity evidence. Momentum and sell-through remain unknown in V1.</p>"}</section>`;
    if (productHistory?.id === id) drawHistory(productHistory.data);
    else
      api("/api/product/" + encodeURIComponent(id))
        .then((data) => {
          productHistory = { id, data };
          if (location.hash === "#product/" + id) drawHistory(data);
        })
        .catch((e) => notice(e.message, true));
  } else if (route === "profitability") {
    el.innerHTML = `<section class="panel"><h2>Where to sell · profitability</h2><p>Compare supported net contribution, stress price and cash recovery. A higher asking price alone does not identify the best destination.</p><button data-action="route">Add channel scenario</button><div class="table-wrap"><table><thead><tr><th>PRODUCT / ROUTE</th><th>LANDED</th><th>BASE / STRESS NET</th><th>BREAK-EVEN / CASH DAYS</th><th>QUALIFICATION</th></tr></thead><tbody>${state.routes.map((r) => `<tr><td>${esc(r.product_id)}<br>${esc(r.channel)} · ${esc(r.fulfillment)}<br>${external(r.destination_url, "Destination")}</td><td>${esc(money(r.landed_cost))}</td><td>${esc(money(r.net_profit))} / ${esc(money(r.stress_profit))}</td><td>${esc(money(r.break_even))}<br>${esc(r.days_to_cash ?? "Unknown")} days</td><td>${r.qualifies ? '<span class="good">Reviewed scenario</span>' : r.blockers.map(esc).join("<br>")}<br><span class="meta">${esc(r.evidence_kind)}<br>${esc(r.input_hash)}</span></td></tr>`).join("")}</tbody></table></div></section>`;
    el.insertAdjacentHTML(
      "beforeend",
      `<section class="panel"><h2>Configured selling routes</h2><p>Pause unused routes to bound comparisons without losing their inputs.</p>${state.configured_routes.map((r) => `<div class="line"><span>${esc(r.product_id)} · ${esc(r.channel)} · ${esc(r.id)}</span><button data-action="route-toggle" data-id="${esc(r.id)}">${r.enabled ? "Pause route" : "Resume route"}</button></div>`).join("") || "<p>No routes recorded.</p>"}</section>`,
    );
  } else if (route === "suggestions") {
    const b = state.basket;
    el.innerHTML = `<section class="panel"><h2>Capital planner</h2><p>${esc(b.reason)}</p><p class="caption">Conservative greedy baseline, not a global optimum. One destination per product; integer units; no leverage. Existing inventory reduces capacity. Nothing is purchased.</p><a href="#settings">Edit separate business capital ↗</a><div class="line"><span>Cash available / preserved</span><span>${esc(money(b.cash_available))} / ${esc(money(b.cash_remaining))}</span></div>${b.basket.map((r) => `<div class="line"><span>${esc(r.product_id)} → ${esc(r.channel)} · ${r.units} units<br><span class="meta">${esc(r.source_url)} · observation ${r.observation_id}</span></span><span>${esc(money(r.commitment))} cost<br>${esc(money(r.estimated_total_profit))} estimated net</span></div>`).join("") || "<p>No current basket meets the capital and evidence requirements. Keeping cash is a valid outcome.</p>"}</section>`;
  } else if (route === "inventory" || route === "planning") {
    el.innerHTML = `<section class="panel"><h2>${route === "planning" ? "Planning & inventory aging" : "Inventory · append-only lots"}</h2><p>Receive stock with its total landed cost. Reserve units explicitly; release them before a sale. Returns stay quarantined until restocked. Sale amounts are net proceeds; return amounts are refunds.</p><button data-action="inventory">Record inventory event</button><div class="line"><span>Realized recorded profit</span><span>${esc(money(state.inventory.realized_profit))}</span></div><div class="table-wrap"><table><thead><tr><th>LOT / PRODUCT</th><th>AVAILABLE / RESERVED</th><th>QUARANTINED</th><th>COST / REALIZED</th><th>AGE</th></tr></thead><tbody>${state.inventory.lots.map((l) => `<tr><td>${esc(l.id)}<br>${esc(l.product_id)}</td><td>${l.available} / ${l.reserved}</td><td>${l.quarantined}</td><td>${esc(money(l.committed_cost))} / ${esc(money(l.realized_profit))}</td><td>${Math.max(0, Math.floor((Date.now() - Date.parse(l.received_at)) / 86400000))} days</td></tr>`).join("")}</tbody></table></div>${route === "planning" ? '<h3>Before committing capital</h3><ul class="checks"><li>Confirm exact variant, pack, condition, authenticity and acceptable supplier documents.</li><li>Check seller/category eligibility and shipping restrictions on the destination.</li><li>Recheck regional stock and delivered cost within five minutes of review.</li><li>Stress selling price, returns, holding time and all incremental fees.</li><li>Review aged and quarantined inventory before replenishment.</li></ul><p>No seasonality forecast is available without sufficient dated observations.</p>' : ""}</section>`;
  } else if (route === "sources") {
    el.innerHTML = `<section class="panel"><h2>Find your next product</h2><p>Start with a product or model. Connected searches collect automatically; the discovery links below open external sites and do not import their results.</p><form id="discovery-form"><label>What do you want to source?<input id="discovery-query" type="search" placeholder="e.g. LEGO 75375 or DeWalt DCD800" required maxlength="100"></label><button class="primary">Find sourcing and resale searches</button></form><div id="discovery-links"></div><h3>Your automatic monitors</h3><p>V1 automatically watches a small reviewed source set. It does not scan every retailer or connect to marketplace seller accounts. No API subscription is needed for the included read-only adapter.</p><div class="toolbar"><button id="toggle">${state.settings.enabled ? "Pause collection" : "Resume collection"}</button><button data-action="source">Add supported product URL</button><button data-action="ebay-source">Add eBay search</button><button data-action="observation">Record real observation</button><button data-action="signal">Import evidence</button></div>${state.sources
      .map((s) => {
        const j = state.jobs[s.id] || {};
        return `<div class="line"><div>${external(s.url, s.id)} ${esc(s.query || "")}<br><span class="caption">${esc(j.status || "Waiting for first attempt")} · last success ${esc(j.last_ok_at || "None")}<br>${esc(j.error || "")}${j.retry_at ? "<br>Next retry after " + esc(j.retry_at) : ""}</span></div><span><span class="badge">${esc(s.adapter)}</span><button data-action="source-toggle" data-id="${esc(s.id)}">${s.enabled ? "Pause source" : "Resume source"}</button></span></div>`;
      })
      .join(
        "",
      )}<h3>Coverage gaps</h3><p>Automatic completed-sales, broad retailer discovery, Amazon FBA/Walmart WFS fee estimates and marketplace eligibility checks are not connected. Enter reviewed scenarios and your own evidence locally; never paste private API credentials into chat.</p><p>FBA means Amazon stores, packs and ships your inventory for fees. WFS is Walmart’s fulfillment service. These services require separate seller eligibility and item-specific costs.</p><p class="caption">Asking-price retention: up to 90 days / 100,000 observations; eBay API listing cache is capped at 24 hours pending a fuller licensing review. Inventory events remain append-only. Ratings require a comparable channel/metric cohort and sufficient dated evidence.</p><a href="/api/capabilities" target="_blank" rel="noopener">Machine-readable capabilities & contracts ↗</a></section>`;
    $("#discovery-form").onsubmit = e => {
      e.preventDefault();
      const q = encodeURIComponent($("#discovery-query").value.trim());
      const links = [
        ["Google Shopping", "https://www.google.com/search?tbm=shop&q=" + q, "Compare advertised offers; stock and shipping vary."],
        ["Slickdeals", "https://slickdeals.net/newsearch.php?q=" + q, "Community deal leads; posts may be expired."],
        ["Walmart", "https://www.walmart.com/search?q=" + q, "Retail and marketplace offers; check seller and exact pack."],
        ["Target", "https://www.target.com/s?searchTerm=" + q, "Retail search; local availability varies."],
        ["eBay sold listings", "https://www.ebay.com/sch/i.html?_nkw=" + q + "&LH_Sold=1&LH_Complete=1", "Manual comparable research; accepted-offer prices may be hidden."],
        ["Amazon", "https://www.amazon.com/s?k=" + q, "Compare listing prices; these are not confirmed sales."],
      ];
      $("#discovery-links").innerHTML = '<div class="source-grid">' + links.map(([name, url, note]) => `<article class="panel"><h3>${external(url, name)}</h3><span class="badge">External research · not an automatic feed</span><p>${esc(note)}</p></article>`).join("") + '</div><p>Found a candidate? Use Record real observation below. Only eBay searches and supported retailer URLs are monitored automatically.</p>';
    };
    $("#toggle").onclick = async () => {
      await save("/api/settings", {
        ...state.settings,
        enabled: !state.settings.enabled,
      });
    };
  } else if (route === "settings") {
    const s = state.settings;
    el.innerHTML = `<section class="panel"><h2>Business settings</h2><p>Business capital includes inventory cost. It is independent of the $500 trading reference accounts. Unknown capital produces no purchase basket.</p><form id="settings-form"><div class="form-grid">${field("capital", "Business capital", "number", s.capital ?? "", false)}${field("reserve", "Cash reserve", "number", s.reserve)}${field("region", "Delivery region (use the same value in checks)", "text", s.region)}${field("currency", "Currency", "text", s.currency)}</div><details><summary>Advanced limits · optional</summary><p>Defaults limit any one product to 20% of capital, any one selling channel to 60%, and orders to five units per product. Change these only to fit your business.</p><div class="form-grid">${field("max_product_fraction", "Maximum product share (0–1)", "number", s.max_product_fraction)}${field("max_channel_fraction", "Maximum channel share (0–1)", "number", s.max_channel_fraction)}${field("min_profit", "Minimum estimated profit per unit", "number", s.min_profit)}${field("max_units", "Maximum units per product", "number", s.max_units)}${field("max_days", "Maximum cash-cycle days", "number", s.max_days)}${field("max_monthly_cost", "Maximum monthly channel subscription", "number", s.max_monthly_cost)}</div></details><button class="primary">Save business profile</button></form></section>`;
    el.insertAdjacentHTML(
      "beforeend",
      `<section class="panel"><h2>Data connections & setup guides</h2><p>No paid API is required for the included source adapter. Optional integrations show their actual implementation status.</p>${setupGuides.map((g) => `<details class="panel"><summary>${esc(g.title)} · ${esc(g.status)}</summary><p>${esc(g.purpose)}</p><div class="toolbar">${g.links.map((l) => external(l.url, l.label)).join(" · ")}</div><ol>${g.steps.map((step) => `<li>${esc(step)}</li>`).join("")}</ol><p class="warn">${esc(g.cost)}</p><p class="caption">${esc(g.limitation)}</p></details>`).join("")}</section>`,
    );
    const connection = state.integrations?.ebay || {};
    el.insertAdjacentHTML(
      "beforeend",
      `<section class="panel"><h2>eBay live-listing connection</h2><p class="caption">App key: ${connection.app_key_set ? "saved privately" : "not saved"} · App secret: ${connection.app_secret_set ? "saved privately" : "not saved"} · ${connection.enabled ? "enabled" : "disabled"}. ${esc(connection.note || connection.error || "")}</p><p>These are production developer app credentials, not your eBay login password. Blank fields preserve saved values. Read the eBay guide above before enabling.</p><form id="ebay-form"><label>Production App ID / Client ID<input type="password" name="client_id" autocomplete="new-password" placeholder="Leave blank to preserve"></label><label>Production Cert ID / Client Secret<input type="password" name="client_secret" autocomplete="new-password" placeholder="Leave blank to preserve"></label>${check("free_access_confirmed", "I confirmed permitted personal research access and no added API charges", connection.free_access_confirmed)}${check("enabled", "Enable read-only live eBay listings", connection.enabled)}<button class="primary">Save eBay connection</button></form><p>No purchases, cart changes or seller actions. The collector reloads this connection automatically.</p></section>`,
    );
    $("#ebay-form").onsubmit = async (e) => {
      e.preventDefault();
      const f = e.target;
      await save("/api/integrations/ebay", {
        client_id: f.client_id.value,
        client_secret: f.client_secret.value,
        enabled: f.enabled.checked,
        free_access_confirmed: f.free_access_confirmed.checked,
      });
    };
    $("#settings-form").onsubmit = async (e) => {
      e.preventDefault();
      const d = Object.fromEntries(new FormData(e.target));
      await save("/api/settings", { ...s, ...d, capital: d.capital || null });
    };
  } else
    el.innerHTML =
      '<section class="panel">Page not found. <a href="#home">Return home</a></section>';
}
async function save(path, data) {
  try {
    await api(path, data);
    $("#editor").close();
    await reload();
    notice("Saved locally. Marketplaces were not changed.");
  } catch (e) {
    notice(e.message, true);
    let m = $("#form-message");
    if (m) m.textContent = e.message;
  }
}
function form(title, fields, onSubmit) {
  modal(
    `<h2>${esc(title)}</h2><form id="record-form">${fields}<p id="form-message" class="warn" role="alert"></p><button class="primary">Save local record</button></form>`,
  );
  $("#record-form").onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    await onSubmit(data, e.target);
  };
}
function drawHistory(data) {
  const target = $("#history");
  if (!target) return;
  const rows = data.asking_history;
  const currency = rows[0]?.currency;
  const pts = [
    ...rows
      .filter((r) => r.currency === currency)
      .map((r) => ({
        t: Date.parse(r.at),
        p: Number(r.price),
        sale: false,
        label: r.at + " asking " + r.price + " " + r.currency,
      })),
    ...data.reported_sales
      .filter((r) => r.currency === currency)
      .map((r) => ({
        t: Date.parse(r.at),
        p: Number(r.price),
        sale: true,
        label: r.at + " reported sale " + r.price,
      })),
  ];
  if (!pts.length) {
    target.textContent = "No recorded price points.";
    return;
  }
  const xs = pts.map((p) => p.t),
    ys = pts.map((p) => p.p),
    xmin = Math.min(...xs),
    xspan = Math.max(...xs) - xmin || 1,
    ymin = Math.min(...ys),
    yspan = Math.max(...ys) - ymin || 1;
  target.innerHTML = `<svg class="chart" viewBox="0 0 800 200" role="img" aria-label="Recorded asking prices and reported sales as separate point series">${pts.map((p) => `<circle class="${p.sale ? "sale" : ""}" cx="${20 + ((p.t - xmin) / xspan) * 760}" cy="${175 - ((p.p - ymin) / yspan) * 150}" r="3"><title>${esc(p.label)}</title></circle>`).join("")}</svg><p class="caption">${esc(currency)} · ${esc(new Date(xmin).toISOString())} onward · green: asking prices · blue: imported reported sales. Source claims remain distinct.</p>`;
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-action]");
  if (!b || !state) return;
  const a = b.dataset.action,
    id = b.dataset.id,
    now = () => new Date().toISOString();
  if (a === "ebay-source")
    form(
      "Add eBay live search",
      field("id", "Local search ID") +
        field("query", "Specific product keyword or model") +
        "<p>Requires an enabled production connection in Business settings. Search results stay separate until exact product identity is reviewed.</p>",
      (d) =>
        save("/api/record/source", {
          ...d,
          url: "https://www.ebay.com/",
          adapter: "ebay_browse",
          enabled: true,
        }),
    );
  if (a === "route-toggle") {
    const route = state.configured_routes.find((r) => r.id === id);
    if (route) save("/api/record/route", { ...route, enabled: !route.enabled });
  }
  if (a === "source-toggle") {
    const source = state.sources.find((s) => s.id === id);
    if (source)
      save("/api/record/source", { ...source, enabled: !source.enabled });
  }
  if (a === "source")
    form(
      "Add retailer URL",
      field("url", "Adafruit product URL", "url") +
        field("id", "Local source ID"),
      (d) =>
        save("/api/record/source", { ...d, adapter: "jsonld", enabled: true }),
    );
  if (a === "identity") {
    const p = state.board.find((x) => x.product.id === id).product;
    form(
      "Review exact product",
      field("title", "Product title", "text", p.title) +
        field("category", "Category", "text", p.category) +
        field("brand", "Brand", "text", p.brand || "", false) +
        field("mpn", "Manufacturer part/model", "text", p.mpn || "", false) +
        field("gtin", "GTIN / UPC", "text", p.gtin || "", false) +
        field("variant", "Exact variant", "text", p.variant) +
        field("pack_count", "Pack count", "number", p.pack_count ?? "") +
        field("condition", "Condition", "text", p.condition) +
        check(
          "confirmed",
          "I checked brand, model, variant, pack and condition",
        ),
      (d, f) =>
        save("/api/record/product", {
          ...p,
          ...d,
          brand: d.brand || null,
          mpn: d.mpn || null,
          gtin: d.gtin || null,
          confirmed: undefined,
          identity_confirmed: f.confirmed.checked,
        }),
    );
  }
  if (a === "verify") {
    form(
      "Record a real delivery check",
      `<p>This is your attestation, not a stock reservation. Open the retailer page and check the exact item, destination and price. It expires after five minutes.</p>${field("price", "Current product price", "number")}${field("region", "Delivery region", "text", state.settings.region)}${field("quantity", "Confirmed available quantity", "number")}${field("shipping", "Shipping per unit", "number")}${field("tax", "Purchase tax per unit", "number")}${field("evidence", "What did you check? Include delivery/variant evidence")}`,
      (d) =>
        save("/api/verify/" + id, { ...d, availability: "VERIFIED_AVAILABLE" }),
    );
  }
  if (a === "route")
    form(
      "Selling destination scenario",
      picker() +
        `<div class="form-grid">${field("id", "Unique route ID")}${field("channel", "Selling marketplace / channel")}${field("fulfillment", "Fulfillment method")}${field("sale_price", "Supported base selling price", "number")}${field("stress_price", "Conservative stress price", "number")}${field("fee_fraction", "Variable fees as fraction (e.g. 0.15)", "number")}${field("fixed_fee", "Fixed selling fees per unit", "number")}${field("fulfillment_cost", "All inbound/prep/fulfillment/outbound costs per unit", "number")}${field("return_allowance", "Expected return / write-off allowance per unit", "number")}${field("overhead", "Allocated incremental overhead per unit", "number")}${field("monthly_cost", "Incremental monthly channel subscription", "number")}${field("days_to_cash", "Conservative days to cash recovery", "number")}${field("demand_limit", "Evidence-supported quantity limit", "number")}${field("fee_version", "Fee schedule version / review date")}${field("valid_until", "Scenario expiration (include timezone)", "text", new Date(Date.now() + 86400000).toISOString())}</div>${field("evidence_note", "Price, demand, fee, eligibility and document evidence")}${check("eligible", "I confirmed seller/category eligibility and required sourcing documents")}${check("identity_confirmed", "Exact variant, pack and condition match")}${check("reviewed", "I reviewed every price, fee, cost and quantity input")}`,
      (d, f) =>
        save("/api/record/route", {
          ...d,
          eligible: f.eligible.checked,
          identity_confirmed: f.identity_confirmed.checked,
          reviewed: f.reviewed.checked,
          currency: state.settings.currency,
        }),
    );
  if (a === "inventory")
    form(
      "Inventory event",
      field("id", "Unique event ID (reuse on retry)") +
        field("lot_id", "Lot ID") +
        picker() +
        `<label>Event<select name="kind">${["RECEIVE", "RESERVE", "RELEASE", "SELL", "RETURN", "RESTOCK", "DAMAGE", "DISCARD_RETURN", "ADJUST_COST"].map((k) => `<option>${k}</option>`).join("")}</select></label>` +
        field("units", "Units (0 for ADJUST_COST)", "number") +
        field(
          "amount",
          "Total landed cost / net proceeds / refund / added cost",
          "number",
        ) +
        field("at", "Occurred at (include timezone)", "text", now()) +
        field("note", "Evidence or reference"),
      (d) =>
        save("/api/record/inventory", {
          ...d,
          currency: state.settings.currency,
        }),
    );
  if (a === "signal")
    form(
      "Record dated evidence",
      picker() +
        field("id", "Unique evidence ID (reuse on retry)") +
        field("channel", "Channel / venue") +
        `<label>Evidence kind<select name="kind">${["OWN_SALE", "CONFIRMED_UNITS", "SOLD_LISTINGS", "VIEWS", "WATCHERS", "MENTIONS", "OFFERS"].map((k) => `<option>${k}</option>`).join("")}</select></label>` +
        field(
          "value",
          "Count in this observation, not a cumulative total",
          "number",
        ) +
        field(
          "price",
          "Observed unit sale price (only for sales)",
          "number",
          "",
          false,
        ) +
        field("at", "Event time (include timezone)", "text", now()) +
        field("source", "Document / source reference"),
      (d) =>
        save("/api/record/signal", {
          ...d,
          price: d.price || null,
          available_at: now(),
          currency: state.settings.currency,
        }),
    );
  if (a === "observation")
    form(
      "Record a real observed offer",
      `<p>Record an actual price you observed. This does not begin automatic monitoring of unsupported sites or verify deliverability.</p><div class="form-grid">${field("product_id", "Product ID (reuse for the same exact product)")}${field("title", "Exact product title")}${field("source_id", "Source ID")}${field("url", "Product link", "url")}${field("price", "Observed product price", "number")}${field("category", "Category", "text", "Uncategorized")}${field("currency", "Currency", "text", state.settings.currency)}</div><label>Availability declared on page<select name="availability"><option>UNKNOWN</option><option>DECLARED_AVAILABLE</option><option>UNAVAILABLE</option></select></label>${field("evidence", "What you observed / source evidence")}`,
      (d) =>
        save("/api/record/observation", {
          product: { id: d.product_id, title: d.title, category: d.category },
          source_id: d.source_id,
          url: d.url,
          price: d.price,
          currency: d.currency,
          availability: d.availability,
          evidence: d.evidence,
          at: now(),
          method: "operator-page-observation",
        }),
    );
});
window.addEventListener("hashchange", () => {
  productHistory = null;
  if (state) render();
});
(async () => {
  try {
    csrf = (await api("/api/session")).csrf_token;
    setupGuides = await api("/api/setup-guides");
    await reload();
  } catch (e) {
    notice(e.message, true);
  }
})();
setInterval(() => {
  if (!editing && !$("#settings-form") && !$("#product-search")?.matches(":focus") && !document.hidden)
    reload().catch((e) => notice("Dashboard unavailable: " + e.message, true));
}, 10000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !editing && !$("#settings-form"))
    reload().catch((e) => notice(e.message, true));
});
