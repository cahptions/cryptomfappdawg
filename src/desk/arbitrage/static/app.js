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
document.addEventListener(
  "error",
  (event) => {
    if (
      event.target instanceof HTMLImageElement &&
      event.target.closest(".product-photo,.lead-image,.dossier-photo")
    ) {
      const fallback = document.createElement("span");
      fallback.className = "no-photo";
      fallback.textContent = "Source photo unavailable";
      event.target.replaceWith(fallback);
    }
  },
  true,
);
let state = null,
  csrf = "",
  editing = false,
  productHistory = null,
  setupGuides = [];
const browse = {
  query: "",
  category: "",
  mode: "all",
  order: "profit",
  leads: "fresh",
  manage: false,
};
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
const picker = (selected = "") =>
  `<label>Product<select name="product_id" required>${state.board.map((b) => `<option value="${esc(b.product.id)}" ${selected === b.product.id ? "selected" : ""}>${esc(b.product.title)}</option>`).join("")}</select></label>`;
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
              ? "Discovery is running. New leads and source observations appear as requests finish."
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
    ["PRODUCTS IN RESEARCH", state.board.length, "From your connected sources"],
    [
      "CHECKED SELLING ROUTES",
      state.qualified_count,
      "Estimates after required checks",
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
const fractionFromPercent = (value) => {
  const [whole, decimals = ""] = value.split(".");
  const digits = (whole || "0").padStart(3, "0");
  return digits.slice(0, -2) + "." + digits.slice(-2) + decimals;
};
const pct = (v) => (v == null ? "—" : `${(Number(v) * 100).toFixed(1)}%`);
const stamp = (value) =>
  value
    ? new Date(value).toLocaleString([], {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      })
    : "Not checked";
function photo(product) {
  return product.image_url
    ? `<img src="${esc(product.image_url)}" alt="${esc(product.title)}" loading="lazy" referrerpolicy="no-referrer">`
    : '<span class="photo-placeholder" aria-label="Source supplied no product photo">▧<small>Photo not supplied</small></span>';
}
function offerFor(b, r) {
  return b.offers.find((o) => o.id === r?.observation_id) || b.offers[0];
}
function board(rows, qualified = false) {
  if (!rows.length)
    return `<div class="empty"><span class="empty-symbol">⌕</span><h3>${qualified ? "No buy-ready comparisons yet" : "Your ranked shortlist starts here"}</h3><p>${qualified ? "Research candidates remain available below. Stock, resale evidence and costs must pass before a buy recommendation." : "The app is checking its configured sources. Public deal leads appear in Discover; connect eBay for active listings or add an observed source offer."}</p><a class="button" href="#sources">Choose sources</a> <button data-action="observation">Add an offer you found</button></div>`;
  return `<div class="deal-grid">${rows
    .slice(0, 100)
    .map((b, index) => {
      const candidates = b.comparison?.alternatives || [];
      const chosen = state.rankings?.selections?.[browse.order]?.[b.product.id];
      let r =
        candidates.find((r) => r.id === chosen) || b.comparison?.modeled_best;
      if (qualified)
        r = [...candidates]
          .filter((r) => r.qualifies)
          .sort(
            (a, b) =>
              Number(b[browse.order === "roi" ? "roi" : "stress_profit"]) -
              Number(a[browse.order === "roi" ? "roi" : "stress_profit"]),
          )[0];
      let o = offerFor(b, r);
      const p = b.product;
      if (browse.order === "drops" && !qualified) {
        const d = state.rankings.price_drops.find((d) => d.product_id === p.id);
        o = b.offers.find((o) => o.source_id === d?.source_id) || o;
        r = b.routes.find(
          (r) => r.observation_id === o?.id && r.net_profit != null,
        );
      }
      const rankingIds =
        browse.order === "drops"
          ? state.rankings.price_drops.map((d) => d.product_id)
          : state.rankings?.[browse.order] || [];
      const rank = qualified ? index : rankingIds.indexOf(p.id);
      const drop = b.deal_history.find(
        (d) =>
          d.source_id === o?.source_id &&
          d.discount_fraction &&
          Number(d.discount_fraction) > 0,
      );
      return `<article class="deal-card"><a class="product-photo" href="#product/${esc(p.id)}">${photo(p)}<span class="card-rank">${rank < 0 ? "Research" : rank + 1}</span>${drop ? `<span class="price-drop">${pct(drop.discount_fraction)} below observed median</span>` : ""}</a><div class="deal-body"><span class="eyebrow">${esc(p.category)} · ${esc(p.condition)}</span><h3><a href="#product/${esc(p.id)}">${esc(p.title)}</a></h3><div class="trade-path"><div><span>Source price</span><strong>${o ? esc(money(o.price, o.currency)) : "Unknown"}</strong></div><span class="path-arrow">→</span><div><span>${r ? esc(r.channel) : "Resale price"}</span><strong>${r ? esc(money(r.price, r.currency)) : "Needs evidence"}</strong></div></div><div class="profit-strip"><span>Net / unit <strong class="${r && Number(r.net_profit) > 0 ? "good" : ""}">${r ? esc(money(r.net_profit, r.currency)) : "Not calculated"}</strong></span><span>Downside estimate <strong>${r ? esc(money(r.stress_profit, r.currency)) : "Unknown"}</strong></span></div><div class="card-facts"><span>ROI ${r ? pct(r.roi) : "—"}</span><span>${r?.effort_minutes ? `${r.effort_minutes} min work/unit` : "Work estimate missing"}</span><span>${r?.days_to_cash ? `${r.days_to_cash}d cash cycle` : "Cash cycle unknown"}</span></div><span class="evidence-state ${r?.qualifies ? "ready" : "pending"}">${r?.qualifies ? "Checks passed · recheck before purchase" : "Research candidate · not buy-ready"}</span><p class="next-step">${!p.identity_confirmed ? "Check the exact model, pack and condition." : !r ? "Add a resale comparison to estimate your margin." : esc(r.blockers[0] || "Prices and stock can change. Nothing is reserved.")}</p><div class="toolbar"><a class="button" href="#product/${esc(p.id)}">Compare & research</a>${o ? external(o.url, "Source ↗") : ""}</div><span class="caption">Source observed ${o ? esc(stamp(o.at)) : "never"} · ${o ? esc(availability(o).replaceAll("_", " ").toLowerCase()) : "No offer"}</span></div></article>`;
    })
    .join("")}</div>`;
}
function leadCards() {
  const rows = (state.leads || []).filter((l) =>
    browse.leads === "saved"
      ? l.saved && !l.dismissed
      : !l.dismissed && l.fresh,
  );
  return `<div class="section-heading"><div><p class="eyebrow">DISCOVERY INBOX</p><h2>Fresh finds from your discovery feeds.</h2><p>New deal mentions to investigate. Price, stock and resale margin still need checking.</p></div><div class="segmented"><button data-leads="fresh" class="${browse.leads === "fresh" ? "selected" : ""}">Fresh</button><button data-leads="saved" class="${browse.leads === "saved" ? "selected" : ""}">Saved</button></div></div>${
    rows.length
      ? `<div class="lead-grid">${rows
          .slice(0, 12)
          .map(
            (l) =>
              `<article class="lead-card"><div class="lead-image">${photo(l)}</div><div><span class="eyebrow">${esc(l.source_id.replace("deals-", ""))} · ${l.fresh ? "Recent mention" : "Older lead"}</span><h3>${external(l.url, l.title)}</h3><p>${esc(l.summary.slice(0, 160))}</p><span class="caption">${esc(l.age_basis)} ${esc(stamp(l.published_at || l.first_seen_at))}</span><div class="toolbar"><button class="primary" data-action="lead-offer" data-id="${esc(l.id)}">Check this deal</button><button data-action="lead-save" data-id="${esc(l.id)}">${l.saved ? "Unsave" : "Save"}</button><button data-action="lead-dismiss" data-id="${esc(l.id)}" aria-label="Dismiss ${esc(l.title)}">Dismiss</button></div></div></article>`,
          )
          .join("")}</div>`
      : `<div class="empty compact"><h3>${browse.leads === "saved" ? "No saved leads yet" : "No recent feed leads have arrived"}</h3><p>${browse.leads === "saved" ? "Save a discovery lead to keep its research context." : "Two public feed adapters are included. Source access must succeed before deals appear; their live acceptance is still pending. eBay listings use a separate connection."}</p><a href="#sources">See source progress and connection options ↗</a></div>`
  }`;
}
function orderedRows(qualified) {
  let rows = state.board.filter(
    (b) => !qualified || b.routes.some((r) => r.qualifies),
  );
  rows = rows.filter(
    (b) =>
      (!browse.category || b.product.category === browse.category) &&
      b.product.title.toLowerCase().includes(browse.query.toLowerCase()) &&
      (browse.mode !== "qualified" || b.routes.some((r) => r.qualifies)) &&
      (browse.mode !== "fresh" ||
        b.offers.some(
          (o) =>
            !availability(o).startsWith("STALE") &&
            ["DECLARED_AVAILABLE", "VERIFIED_AVAILABLE"].includes(
              o.availability,
            ),
        )),
  );
  const ids =
    browse.order === "drops"
      ? (state.rankings?.price_drops || []).map((d) => d.product_id)
      : state.rankings?.[browse.order] || [];
  return rows.sort((a, b) => {
    if (qualified) {
      const key = browse.order === "roi" ? "roi" : "stress_profit";
      const best = (x) =>
        Math.max(
          ...x.comparison.alternatives
            .filter((r) => r.qualifies)
            .map((r) => Number(r[key])),
        );
      return (
        best(b) - best(a) || a.product.title.localeCompare(b.product.title)
      );
    }
    const ai = ids.indexOf(a.product.id),
      bi = ids.indexOf(b.product.id);
    return (
      (ai < 0 ? 99999 : ai) - (bi < 0 ? 99999 : bi) ||
      a.product.title.localeCompare(b.product.title)
    );
  });
}
function bindBrowse(qualified) {
  for (const [selector, key] of [
    ["#product-search", "query"],
    ["#category", "category"],
    ["#offer-filter", "mode"],
    ["#rank-order", "order"],
  ]) {
    const input = $(selector);
    input.value = browse[key];
    input.addEventListener(
      selector === "#product-search" ? "input" : "change",
      (e) => {
        browse[key] = e.target.value;
        $("#board").innerHTML = board(
          orderedRows(qualified || browse.mode === "qualified"),
          qualified || browse.mode === "qualified",
        );
      },
    );
  }
}
function comparisons(b) {
  const c = b.comparison;
  return `<section class="panel comparison-panel"><div class="section-heading"><div><p class="eyebrow">WHERE TO SELL</p><h2>${c?.recommended ? `Best checked destination: ${esc(c.recommended.channel)}` : "Compare the exit before buying."}</h2><p>${esc(c?.reason || "No destination comparisons yet.")}</p></div><button class="primary" data-action="route" data-id="${esc(b.product.id)}">+ Compare destination</button></div>${c?.alternatives.length ? `<div class="table-wrap"><table><thead><tr><th>Destination</th><th>Source → sell</th><th>Net / unit</th><th>Downside / break-even</th><th>Work / cash cycle</th><th>Remaining checks</th></tr></thead><tbody>${c.alternatives.map((r) => `<tr><td><strong>${esc(r.channel)}</strong><span class="meta">${esc(r.fulfillment)}</span></td><td>${esc(money(r.landed_cost, r.currency))} → ${esc(money(r.price, r.currency))}<br>${external(r.source_url, "Source offer")}</td><td class="${Number(r.net_profit) > 0 ? "good" : ""}"><strong>${esc(money(r.net_profit, r.currency))}</strong><span class="meta">${pct(r.roi)} ROI on landed cost</span></td><td>${esc(money(r.stress_profit, r.currency))}<br><span class="meta">Break-even ${esc(money(r.break_even, r.currency))}</span></td><td>${r.effort_minutes ? `${r.effort_minutes} min/unit` : "Work unknown"}<br>${r.days_to_cash ? `${r.days_to_cash} days` : "Timing unknown"}</td><td>${r.qualifies ? '<span class="good">Checks passed</span>' : r.blockers.map((x) => `<span class="blocker">${esc(x)}</span>`).join("")}<button class="text-button" data-action="route-edit" data-id="${esc(r.route_id)}">Edit assumptions</button></td></tr>`).join("")}</tbody></table></div>` : '<div class="empty compact"><h3>No selling destinations compared.</h3><p>Add a realistic resale price and actual channel costs. Missing fields stay unknown, so you can begin research without claiming a profitable purchase.</p></div>'}</section>`;
}
function render() {
  if ($("#route-manager")) browse.manage = $("#route-manager").open;
  status();
  const [page, id] = location.hash.slice(1).split("/");
  const route = page || "home";
  document
    .querySelectorAll("nav a")
    .forEach((a) => a.classList.toggle("active", a.hash === "#" + route));
  const el = $("#content");
  if (route === "home" || route === "research" || route === "top") {
    const qualified = route === "top";
    const ready = state.board.filter((b) =>
      b.routes.some((r) => r.qualifies),
    ).length;
    el.innerHTML = `<div class="section-heading"><div><p class="eyebrow">${qualified ? "CHECKED ECONOMICS" : "SOURCING → RESALE"}</p><h2>${qualified ? "Top opportunities" : route === "research" ? "Your product research" : "Find the products worth your capital."}</h2><p>Prioritize meaningful dollar profit. See the downside, work and cash commitment before you buy.</p></div><a class="quiet-link" href="#sources">Manage discovery ↗</a></div><div class="pipeline"><span><b>${(state.leads || []).filter((l) => l.fresh && !l.dismissed).length}</b> fresh leads</span><span>→</span><span><b>${state.board.length}</b> products observed</span><span>→</span><span><b>${state.rankings?.profit.length || 0}</b> costed comparisons</span><span>→</span><span><b>${ready}</b> checked opportunities</span></div><section class="ranked-section"><div class="section-heading"><div><h2>Ranked product shortlist</h2><p>Highest conservative dollars per unit first. Missing resale costs stay unranked; estimates are not realized profit.</p></div><button data-action="observation">+ Add source offer</button></div><div class="browse-bar"><label class="search-field"><span class="sr-only">Search products</span><input id="product-search" type="search" placeholder="Search brands, models or products"></label><label>Rank by<select id="rank-order"><option value="profit">Highest modeled $ profit</option><option value="roi">Return on capital</option><option value="drops">Observed price drops</option></select></label><label>Stage<select id="offer-filter"><option value="all">All research candidates</option><option value="fresh">Recent source listings</option><option value="qualified">Checks passed</option></select></label><label>Category<select id="category"><option value="">All categories</option>${[...new Set(state.board.map((b) => b.product.category))].map((c) => `<option>${esc(c)}</option>`).join("")}</select></label></div><div id="board">${board(orderedRows(qualified || browse.mode === "qualified"), qualified || browse.mode === "qualified")}</div></section>${route === "home" ? `<section class="discovery-section">${leadCards()}</section>` : ""}<section class="insight-grid"><article><span class="eyebrow">BUY LOW WITH EVIDENCE</span><h3>Price history, not a crossed-out MSRP.</h3><p>${state.rankings?.price_drops.length || 0} current source price drops supported by at least six prior observation days. Historical asking prices remain separate from sales.</p></article><article><span class="eyebrow">DOLLARS, WORK, CASH</span><h3>A bigger margin must earn its overhead.</h3><p>Compare net dollars, downside price, fulfillment cost, hands-on time and cash-cycle days. High ROI alone does not make a small flip worthwhile.</p></article><article><span class="eyebrow">SELL WHERE IT MAKES SENSE</span><h3>The destination is half the deal.</h3><p>Compare Amazon, Walmart, eBay or another channel using actual seller eligibility and dated costs. No marketplace wins by default.</p><a href="#profitability">Compare destinations ↗</a></article></section>`;
    bindBrowse(qualified);
  } else if (route === "product") {
    const b = state.board.find((x) => x.product.id === id);
    if (!b) {
      el.innerHTML = '<div class="panel">Product not found.</div>';
      return;
    }
    const query = encodeURIComponent(
      [b.product.brand, b.product.mpn || b.product.title]
        .filter(Boolean)
        .join(" "),
    );
    el.innerHTML = `<div class="product-hero"><div class="dossier-photo">${photo(b.product)}</div><div><a class="quiet-link" href="#home">← Back to discovery</a><p class="eyebrow">PRODUCT RESEARCH</p><h2>${esc(b.product.title)}</h2><p>${esc(b.product.variant || "Variant not reviewed")} · Pack ${esc(b.product.pack_count ?? "unknown")} · ${esc(b.product.condition)}</p><div class="research-steps"><span class="${b.product.identity_confirmed ? "complete" : ""}">1. Exact product</span><span>2. Resale evidence</span><span>3. Costs & destination</span><span>4. Fresh stock check</span></div><div class="toolbar"><button data-action="identity" data-id="${esc(id)}">Review identity</button><button data-action="signal" data-id="${esc(id)}">Import evidence</button>${external("https://www.ebay.com/sch/i.html?_nkw=" + query + "&LH_Sold=1&LH_Complete=1", "Research sold listings")}</div><p class="caption">Sold-search prices are external research, not a confirmed sales feed. Hidden accepted offers remain unknown.</p></div></div>${comparisons(b)}<div class="research-grid"><section class="panel"><h2>Source offers & delivery checks</h2>${b.offers.map((o) => `<div class="line"><div><strong>${esc(money(o.price, o.currency))}</strong> · ${external(o.url, "Open source")}<br><span class="caption">${esc(availability(o).replaceAll("_", " "))} · ${esc(stamp(o.at))}</span><p class="caption">${esc(o.evidence)}</p></div><button data-action="verify" data-id="${o.id}">Record delivery check</button></div>`).join("") || "<p>No source offer recorded.</p>"}<h3>Heat evidence</h3>${b.heat.map((m) => `<div class="metric"><span>${esc(m.channel)} / ${esc(m.kind)}<br><span class="caption">${m.count} observations / ${m.days} days; cohort ${m.cohort_size}</span></span><span>${esc(m.rating)}</span></div>`).join("") || "<p>No reliable volume or popularity evidence connected. Asking prices and deal mentions do not establish sales.</p>"}</section><section class="panel"><h2>Asking prices & reported sales</h2><div id="history">Loading recorded history…</div><p class="caption">Actual dated observations only. Green: asking prices. Blue: imported reported sales. Gaps stay gaps.</p></section></div>`;
    if (productHistory?.id === id) drawHistory(productHistory.data);
    else
      api("/api/product/" + encodeURIComponent(id))
        .then((data) => {
          productHistory = { id, data };
          if (location.hash === "#product/" + id) drawHistory(data);
        })
        .catch((e) => notice(e.message, true));
  } else if (route === "profitability") {
    el.innerHTML = `<div class="section-heading"><div><p class="eyebrow">DESTINATION RESEARCH</p><h2>Where will the profit survive the costs?</h2><p>Compare real net dollars, price compression, fulfillment work and time to recover cash.</p></div><button data-action="route">+ Add destination</button></div>${
      state.board
        .filter((b) => b.routes.length)
        .map(
          (b) =>
            `<h3><a href="#product/${esc(b.product.id)}">${esc(b.product.title)}</a></h3>${comparisons(b)}`,
        )
        .join("") ||
      '<div class="empty"><h3>Start with a product from Discover.</h3><p>Its research page carries the exact item into each destination comparison.</p><a class="button" href="#home">Find products</a></div>'
    }<details id="route-manager" class="panel" ${browse.manage ? "open" : ""}><summary>Manage stored comparisons</summary>${state.configured_routes.map((r) => `<div class="line"><span>${esc(state.board.find((b) => b.product.id === r.product_id)?.product.title || r.product_id)} · ${esc(r.channel)}</span><button data-action="route-toggle" data-id="${esc(r.id)}">${r.enabled ? "Pause route" : "Resume route"}</button></div>`).join("") || "<p>No comparisons recorded.</p>"}</details>`;
  } else if (route === "suggestions") {
    const b = state.basket;
    el.innerHTML = `<section class="panel"><h2>Capital planner</h2><p>${esc(b.reason)}</p><p class="caption">Conservative greedy baseline, not a global optimum. One destination per product; integer units; no leverage. Existing inventory reduces capacity. Nothing is purchased.</p><a href="#settings">Edit separate business capital ↗</a><div class="line"><span>Cash available / preserved</span><span>${esc(money(b.cash_available))} / ${esc(money(b.cash_remaining))}</span></div>${b.basket.map((r) => `<div class="line"><span>${esc(r.product_id)} → ${esc(r.channel)} · ${r.units} units<br><span class="meta">${esc(r.source_url)} · observation ${r.observation_id}</span></span><span>${esc(money(r.commitment))} cost<br>${esc(money(r.estimated_total_profit))} estimated net</span></div>`).join("") || "<p>No current basket meets the capital and evidence requirements. Keeping cash is a valid outcome.</p>"}</section>`;
  } else if (route === "inventory" || route === "planning") {
    el.innerHTML = `<section class="panel"><h2>${route === "planning" ? "Planning & inventory aging" : "Inventory · append-only lots"}</h2><p>Receive stock with its total landed cost. Reserve units explicitly; release them before a sale. Returns stay quarantined until restocked. Sale amounts are net proceeds; return amounts are refunds.</p><button data-action="inventory">Record inventory event</button><div class="line"><span>Realized recorded profit</span><span>${esc(money(state.inventory.realized_profit))}</span></div><div class="table-wrap"><table><thead><tr><th>LOT / PRODUCT</th><th>AVAILABLE / RESERVED</th><th>QUARANTINED</th><th>COST / REALIZED</th><th>AGE</th></tr></thead><tbody>${state.inventory.lots.map((l) => `<tr><td>${esc(l.id)}<br>${esc(l.product_id)}</td><td>${l.available} / ${l.reserved}</td><td>${l.quarantined}</td><td>${esc(money(l.committed_cost))} / ${esc(money(l.realized_profit))}</td><td>${Math.max(0, Math.floor((Date.now() - Date.parse(l.received_at)) / 86400000))} days</td></tr>`).join("")}</tbody></table></div>${route === "planning" ? '<h3>Before committing capital</h3><ul class="checks"><li>Confirm exact variant, pack, condition, authenticity and acceptable supplier documents.</li><li>Check seller/category eligibility and shipping restrictions on the destination.</li><li>Recheck regional stock and delivered cost within five minutes of review.</li><li>Stress selling price, returns, holding time and all incremental fees.</li><li>Review aged and quarantined inventory before replenishment.</li></ul><p>No seasonality forecast is available without sufficient dated observations.</p>' : ""}</section>`;
  } else if (route === "sources") {
    el.innerHTML = `<section class="panel"><h2>Find your next product</h2><p>Start with a product or model. Connected searches collect automatically; the discovery links below open external sites and do not import their results.</p><form id="discovery-form"><label>What do you want to source?<input id="discovery-query" type="search" placeholder="e.g. LEGO 75375 or DeWalt DCD800" required maxlength="100"></label><button class="primary">Find sourcing and resale searches</button></form><div id="discovery-links"></div><h3>Your automatic monitors</h3><p>Public feed adapters discover deal mentions, eBay searches discover active listings, and the retailer adapter watches exact product pages. Each source reports its actual access result below. New feed endpoints still require real-network acceptance; they are not a confirmed sales feed.</p><div class="toolbar"><button id="toggle">${state.settings.enabled ? "Pause collection" : "Resume collection"}</button><button data-action="source">Add supported product URL</button><button data-action="ebay-source">Add eBay search</button><button data-action="observation">Record real observation</button><button data-action="signal">Import evidence</button></div>${state.sources
      .map((s) => {
        const j = state.jobs[s.id] || {};
        return `<div class="line"><div>${external(s.url, s.id)} ${esc(s.query || "")}<br><span class="caption">${esc(j.status || "Waiting for first attempt")} · last success ${esc(j.last_ok_at || "None")}<br>${esc(j.error || "")}${j.retry_at ? "<br>Next retry after " + esc(j.retry_at) : ""}</span></div><span><span class="badge">${esc(s.adapter)}</span><button data-action="source-toggle" data-id="${esc(s.id)}">${s.enabled ? "Pause source" : "Resume source"}</button></span></div>`;
      })
      .join(
        "",
      )}<h3>Coverage gaps</h3><p>Automatic completed-sales, broad retailer discovery, Amazon FBA/Walmart WFS fee estimates and marketplace eligibility checks are not connected. Enter reviewed scenarios and your own evidence locally; never paste private API credentials into chat.</p><p>FBA means Amazon stores, packs and ships your inventory for fees. WFS is Walmart’s fulfillment service. These services require separate seller eligibility and item-specific costs.</p><p class="caption">Asking-price retention: up to 90 days / 100,000 observations; eBay API listing cache is capped at 24 hours pending a fuller licensing review. Inventory events remain append-only. Ratings require a comparable channel/metric cohort and sufficient dated evidence.</p><a href="/api/capabilities" target="_blank" rel="noopener">Machine-readable capabilities & contracts ↗</a></section>`;
    $("#discovery-form").onsubmit = (e) => {
      e.preventDefault();
      const q = encodeURIComponent($("#discovery-query").value.trim());
      const links = [
        [
          "Google Shopping",
          "https://www.google.com/search?tbm=shop&q=" + q,
          "Compare advertised offers; stock and shipping vary.",
        ],
        [
          "Slickdeals",
          "https://slickdeals.net/newsearch.php?q=" + q,
          "Community deal leads; posts may be expired.",
        ],
        [
          "Walmart",
          "https://www.walmart.com/search?q=" + q,
          "Retail and marketplace offers; check seller and exact pack.",
        ],
        [
          "Target",
          "https://www.target.com/s?searchTerm=" + q,
          "Retail search; local availability varies.",
        ],
        [
          "eBay sold listings",
          "https://www.ebay.com/sch/i.html?_nkw=" +
            q +
            "&LH_Sold=1&LH_Complete=1",
          "Manual comparable research; accepted-offer prices may be hidden.",
        ],
        [
          "Amazon",
          "https://www.amazon.com/s?k=" + q,
          "Compare listing prices; these are not confirmed sales.",
        ],
      ];
      $("#discovery-links").innerHTML =
        '<div class="source-grid">' +
        links
          .map(
            ([name, url, note]) =>
              `<article class="panel"><h3>${external(url, name)}</h3><span class="badge">External research · not an automatic feed</span><p>${esc(note)}</p></article>`,
          )
          .join("") +
        "</div><p>Found a candidate? Use Record real observation below. Only eBay searches and supported retailer URLs are monitored automatically.</p>";
    };
    $("#toggle").onclick = async () => {
      await save("/api/settings", {
        ...state.settings,
        enabled: !state.settings.enabled,
      });
    };
  } else if (route === "settings") {
    const s = state.settings;
    el.innerHTML = `<section class="panel"><h2>Your business profile</h2><p>Business capital includes inventory cost. It is independent of the $500 trading reference accounts. Unknown capital produces no purchase basket.</p><form id="settings-form"><div class="form-grid">${field("capital", "Business capital", "number", s.capital ?? "", false)}${field("reserve", "Cash reserve", "number", s.reserve)}${field("region", "Delivery region (use the same value in checks)", "text", s.region)}${field("currency", "Currency", "text", s.currency)}</div><details><summary>Advanced limits · optional</summary><p>Defaults limit any one product to 20% of capital, any one selling channel to 60%, and orders to five units per product. Change these only to fit your business.</p><div class="form-grid">${field("max_product_fraction", "Maximum product share (0–1)", "number", s.max_product_fraction)}${field("max_channel_fraction", "Maximum channel share (0–1)", "number", s.max_channel_fraction)}${field("min_profit", "Minimum estimated profit per unit", "number", s.min_profit)}${field("max_units", "Maximum units per product", "number", s.max_units)}${field("max_days", "Maximum cash-cycle days", "number", s.max_days)}${field("max_monthly_cost", "Maximum monthly channel subscription", "number", s.max_monthly_cost)}</div></details><button class="primary">Save business profile</button></form></section>`;
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
  const recordId = crypto.randomUUID();
  modal(
    `<h2>${esc(title)}</h2><form id="record-form">${fields}<p id="form-message" class="warn" role="alert"></p><button class="primary">Save local record</button></form>`,
  );
  $("#record-form").onsubmit = async (e) => {
    e.preventDefault();
    const data = Object.fromEntries(new FormData(e.target));
    await onSubmit(data, e.target, recordId);
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
  const tab = e.target.closest("[data-leads]");
  if (tab && state) {
    browse.leads = tab.dataset.leads;
    render();
    return;
  }
  const b = e.target.closest("[data-action]");
  if (!b || !state) return;
  const a = b.dataset.action,
    id = b.dataset.id,
    now = () => new Date().toISOString();
  if (a === "ebay-source")
    form(
      "Add eBay live search",
      field("query", "Specific product keyword or model") +
        "<p>Requires an enabled production connection in Business settings. Search results stay separate until exact product identity is reviewed.</p>",
      (d, _f, recordId) =>
        save("/api/record/source", {
          ...d,
          id: "search-" + recordId,
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
    form("Add retailer URL", field("url", "Adafruit product URL", "url"), (d) =>
      save("/api/record/source", {
        ...d,
        id: "source-" + crypto.randomUUID(),
        adapter: "jsonld",
        enabled: true,
      }),
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
  if (a === "route" || a === "route-edit") {
    const old =
      a === "route-edit"
        ? state.configured_routes.find((r) => r.id === id)
        : null;
    const v = (key) => old?.[key] ?? "";
    form(
      "Compare a selling destination",
      picker(old?.product_id || id) +
        `<p>Start with the prices you can support. Leave unknown costs blank; the app will show what is missing. No default fee rate is assumed.</p><h3>1 · Where and for how much?</h3><div class="form-grid">${field("channel", "Selling marketplace / channel", "text", v("channel"))}${field("fulfillment", "How will it be shipped?", "text", v("fulfillment") || "Seller fulfilled")}${field("destination_url", "Matched destination listing (optional)", "url", v("destination_url"), false)}${field("sale_price", "Supported resale price", "number", v("sale_price"))}${field("stress_price", "Lower resale price to test", "number", v("stress_price"))}</div><h3>2 · What will selling cost per unit?</h3><div class="form-grid">${field("fee_percent", "Variable selling fees (%)", "number", old?.fee_fraction != null ? Number(old.fee_fraction) * 100 : "", false)}${field("fixed_fee", "Fixed selling fees", "number", v("fixed_fee"), false)}${field("fulfillment_cost", "All inbound, prep and delivery costs", "number", v("fulfillment_cost"), false)}${field("return_allowance", "Returns / damaged-stock allowance", "number", v("return_allowance"), false)}${field("overhead", "Other overhead (include your labor value)", "number", v("overhead"), false)}${field("monthly_cost", "Extra monthly channel cost (0 if none)", "number", v("monthly_cost"))}</div><h3>3 · Is it worth the work and tied-up cash?</h3><div class="form-grid">${field("effort_minutes", "Hands-on minutes per unit", "number", v("effort_minutes"), false)}${field("days_to_cash", "Expected days until cash is recovered", "number", v("days_to_cash"), false)}${field("demand_limit", "Units supported by your demand evidence", "number", v("demand_limit"), false)}</div>${field("evidence_note", "Resale price, fee and demand sources", "text", v("evidence_note"), false)}${check("eligible", "I checked seller/category eligibility and sourcing documents", old?.eligible)}${check("identity_confirmed", "Exact model, variant, pack and condition match", old?.identity_confirmed)}${check("reviewed", "I reviewed the prices, fees and costs", old?.reviewed)}<p class="caption">Research comparison only. The current snapshot expires in 24 hours. Recheck prices and fees when continuing research.</p>`,
      async (d, f, recordId) => {
        const { fee_percent, ...values } = d;
        for (const key of [
          "fixed_fee",
          "fulfillment_cost",
          "return_allowance",
          "overhead",
          "effort_minutes",
          "days_to_cash",
          "demand_limit",
        ])
          values[key] = values[key] === "" ? null : values[key];
        await save("/api/record/route", {
          ...values,
          id: old?.id || "route-" + recordId,
          fee_fraction:
            fee_percent === "" ? null : fractionFromPercent(fee_percent),
          currency: state.settings.currency,
          eligible: f.eligible.checked,
          identity_confirmed: f.identity_confirmed.checked,
          reviewed: f.reviewed.checked,
          fee_version:
            "operator-reviewed-" + new Date().toISOString().slice(0, 10),
          valid_until: new Date(Date.now() + 86400000).toISOString(),
        });
      },
    );
  }
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
      picker(id) +
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
  if (a === "lead-save" || a === "lead-dismiss") {
    const lead = state.leads.find((l) => l.id === id);
    if (lead)
      save(
        `/api/leads/${encodeURIComponent(id)}/${a === "lead-dismiss" ? "dismiss" : lead.saved ? "unsave" : "save"}`,
        {},
      );
  }
  if (a === "lead-offer") {
    const lead = state.leads.find((l) => l.id === id);
    if (lead)
      form(
        "Check the source offer",
        `<h3>${esc(lead.title)}</h3><p>${external(lead.url, "Open the original deal")} and follow its source link. Record the actual retailer price; a community post does not establish current stock.</p>${field("url", "Direct source product link", "url")}${field("price", "Actual source price you observed", "number")}${field("currency", "Currency", "text", state.settings.currency)}${field("evidence", "What you observed (pack, condition, promotion)")}`,
        async (d) => {
          try {
            await api(`/api/leads/${encodeURIComponent(id)}/offer`, d);
            $("#editor").close();
            await reload();
            location.hash = "#product/" + lead.id;
          } catch (error) {
            $("#form-message").textContent = error.message;
          }
        },
      );
  }
  if (a === "observation")
    form(
      "Add a source offer",
      `<p>Record the actual price from the source page. This begins research; it does not mark stock or resale profit as verified.</p><div class="form-grid">${field("title", "Exact product title")}${field("url", "Direct source product link", "url")}${field("price", "Actual source price you observed", "number")}${field("category", "Category", "text", "Uncategorized")}${field("currency", "Currency", "text", state.settings.currency)}</div>${field("evidence", "What you observed (pack, condition, promotion)")}`,
      (d, _f, recordId) =>
        save("/api/record/observation", {
          product: {
            id: "product-" + recordId,
            title: d.title,
            category: d.category,
          },
          source_id: "operator",
          url: d.url,
          price: d.price,
          currency: d.currency,
          availability: "UNKNOWN",
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
  if (
    !editing &&
    !$("#settings-form") &&
    !document.activeElement?.matches("input,select,textarea") &&
    !document.hidden
  )
    reload().catch((e) => notice("Dashboard unavailable: " + e.message, true));
}, 10000);

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && !editing && !$("#settings-form"))
    reload().catch((e) => notice(e.message, true));
});
