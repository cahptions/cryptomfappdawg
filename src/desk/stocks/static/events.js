"use strict";
// Notable events: a dated log of unusual market days, found by fixed rules from stored prices, VIX, Treasury yields
// and credit spreads (desk.stocks.market_events). Context for reading results, never a forecast or a signal; each
// entry shows the numbers that made it notable. Used by the Events page, the Home sidebar and the stock page.
const KestrelEvents = (() => {
  const MINUS = "−";
  const GROUPS = [
    ["all", "All"],
    ["market", "Market"],
    ["sectors", "Sectors & themes"],
    ["stocks", "Stocks"],
    ["rates", "Volatility & rates"],
  ];
  const SEVERITY = { 1: "Notable", 2: "Major", 3: "Extreme" };
  const filters = { group: "all", major: false };

  const pct = (x, digits = 1) =>
    x == null || !Number.isFinite(x)
      ? "—"
      : (x > 0 ? "+" : x < 0 ? MINUS : "") +
        Math.abs(x * 100).toFixed(digits) +
        "%";
  const onDay = (iso, options) =>
    new Date(iso.slice(0, 10) + "T12:00:00Z").toLocaleDateString("en-US", {
      timeZone: "UTC",
      ...options,
    });
  const longDay = (iso) =>
    onDay(iso, {
      weekday: "long",
      month: "long",
      day: "numeric",
      year: "numeric",
    });
  const shortDay = (iso, year = false) =>
    onDay(iso, {
      month: "short",
      day: "numeric",
      ...(year ? { year: "numeric" } : {}),
    });
  const logged = (iso) =>
    new Date(iso).toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });

  // The one number that made the day notable.
  function figure(e) {
    const d = e.detail || {};
    if (e.kind === "breadth") return `${Math.round(d.share * 100)}%`;
    if (e.kind === "drawdown") return pct(d.fall);
    if (e.kind === "recovery") return "High";
    if (e.kind === "trend") return e.direction === "up" ? "Above" : "Below";
    if (e.kind === "volatility") return Number(d.value).toFixed(1);
    if (e.kind === "rates")
      return (d.change > 0 ? "+" : MINUS) + Math.abs(d.change).toFixed(2);
    return pct(d.move);
  }
  function figureCaption(e) {
    const d = e.detail || {};
    if (e.kind === "breadth")
      return e.direction === "down" ? "of stocks fell" : "of stocks rose";
    if (e.kind === "drawdown") return "from its high";
    if (e.kind === "recovery") return "52-week";
    if (e.kind === "trend") return "200-day average";
    if (e.kind === "volatility") return "VIX close";
    if (e.kind === "rates") return "points";
    if (e.kind === "run") return `${d.sessions} sessions`;
    if (Number.isFinite(d.z)) return `${Math.abs(d.z).toFixed(1)}× usual`;
    return "";
  }

  function members(e) {
    const rows = e.detail?.members || [];
    if (!rows.length) return null;
    const box = el("div", undefined, "event-members");
    box.setAttribute("aria-label", "Basket members, biggest movers first");
    for (const m of rows.slice(0, 8)) {
      const chip = link("", `#stock/${encodeURIComponent(m.symbol)}`);
      chip.className = "event-member";
      chip.append(
        el("span", m.symbol),
        el("b", pct(m.move), m.move > 0 ? "up" : m.move < 0 ? "down" : ""),
      );
      box.append(chip);
    }
    if (rows.length > 8)
      box.append(el("span", `+${rows.length - 8} more`, "event-more"));
    return box;
  }

  // Other indexes or funds that crossed the same rule on the same day, shown as chips on one card.
  function also(e) {
    if (!e.also?.length) return null;
    const box = el("div", undefined, "event-members");
    box.append(el("span", "Also", "event-more"));
    for (const other of e.also.slice(0, 10)) {
      const symbol = other.detail?.symbol,
        chip = symbol
          ? link("", `#stock/${encodeURIComponent(symbol)}`)
          : el("span");
      chip.className = "event-member";
      chip.append(
        el("span", other.label),
        el(
          "b",
          figure(other),
          other.direction === "up"
            ? "up"
            : other.direction === "down"
              ? "down"
              : "",
        ),
      );
      box.append(chip);
    }
    if (e.also.length > 10)
      box.append(el("span", `+${e.also.length - 10} more`, "event-more"));
    return box;
  }

  // Indexes first (S&P 500, Nasdaq 100, Dow, Russell), then the larger move.
  const PRIORITY = ["SPY", "QQQ", "DIA", "IWM"];
  function rank(e) {
    const p = PRIORITY.indexOf(e.subject);
    return [-e.severity, p < 0 ? 9 : p, -Math.abs(e.detail?.move ?? 0)];
  }
  function before(a, b) {
    const x = rank(a),
      y = rank(b);
    for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return x[i] - y[i];
    return 0;
  }
  function combine(events) {
    const out = [],
      groups = new Map();
    for (const e of events) {
      const key =
        e.family === "index" || e.family === "group"
          ? `${e.day}|${e.family}|${e.kind}|${e.direction}`
          : null;
      if (key && groups.has(key)) groups.get(key).push(e);
      else {
        const members = [e];
        if (key) groups.set(key, members);
        out.push(members);
      }
    }
    return out.map((members) => {
      const [first, ...rest] = members.sort(before);
      return { ...first, also: rest };
    });
  }

  function headlines(e) {
    if (!e.headlines?.length) return null;
    const list = el("ul", undefined, "event-headlines");
    for (const h of e.headlines.slice(0, 3)) {
      if (typeof h.url !== "string" || !/^https?:\/\//.test(h.url)) continue;
      const item = el("li"),
        a = link(h.title, h.url);
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      item.append(
        a,
        el(
          "span",
          [h.source, h.published ? logged(h.published) : null]
            .filter(Boolean)
            .join(" · "),
          "muted",
        ),
      );
      list.append(item);
    }
    if (!list.children.length) return null;
    const box = el("div", undefined, "event-news");
    box.append(
      el("p", "Headlines that day · for reading, not used to log it", "label"),
      list,
    );
    return box;
  }

  function card(e) {
    const item = el(
      "article",
      undefined,
      `event-card tone-${e.tone} severity-${e.severity}`,
    );
    const mark = el("span", undefined, "event-mark");
    mark.setAttribute("aria-hidden", "true");
    const body = el("div", undefined, "event-body"),
      meta = el("div", undefined, "event-meta");
    meta.append(el("span", e.kind_label || e.kind));
    if (e.severity > 1)
      meta.append(el("span", SEVERITY[e.severity], "event-severity"));
    body.append(meta, el("h3", e.title), el("p", e.summary, "event-summary"));
    for (const part of [members(e), also(e), headlines(e)])
      if (part) body.append(part);
    body.append(
      el(
        "p",
        e.basis === "close"
          ? `Logged ${logged(e.detected_at)}`
          : `Found in stored history on ${shortDay(e.detected_at, true)}`,
        "event-logged",
      ),
    );
    const value = el("div", undefined, "event-figure");
    value.append(
      el("strong", figure(e)),
      el("small", figureCaption(e), "muted"),
    );
    const symbol = e.detail?.symbol;
    if (symbol) value.append(link(`${symbol} ↗`, `#stock/${symbol}`));
    item.append(mark, body, value);
    return item;
  }

  // One line per event for the Home sidebar.
  function row(e) {
    const item = link(
      "",
      e.detail?.symbol ? `#stock/${e.detail.symbol}` : "#events",
    );
    item.className = `event-row tone-${e.tone} severity-${e.severity}`;
    const mark = el("span", undefined, "event-mark");
    mark.setAttribute("aria-hidden", "true");
    const text = el("span", undefined, "event-row-text");
    text.append(el("strong", e.title), el("small", shortDay(e.day), "muted"));
    item.append(mark, text);
    return item;
  }

  function standing(rows) {
    const box = el("section", undefined, "event-standing");
    box.setAttribute("aria-label", "Distance from the 52-week high");
    for (const s of rows) {
      const tile = s.symbol ? link("", `#stock/${s.symbol}`) : el("div");
      tile.className = `standing-tile state-${s.state.replaceAll(" ", "-")}`;
      const head = el("div", undefined, "standing-head");
      head.append(el("span", s.label), el("small", s.symbol || "Basket"));
      const meter = el("div", undefined, "standing-meter");
      meter.setAttribute("role", "img");
      meter.setAttribute(
        "aria-label",
        `${pct(s.from_high)} from the 52-week high; corrections start at −10%, bear markets at −20%`,
      );
      const fill = el("span");
      fill.style.width = `${Math.min(100, (Math.abs(Math.min(0, s.from_high)) / 0.25) * 100)}%`;
      meter.append(fill, el("i"), el("i"));
      tile.append(
        head,
        el("strong", pct(s.from_high), s.from_high < -0.0005 ? "down" : ""),
        el(
          "span",
          s.state[0].toUpperCase() + s.state.slice(1),
          "standing-state",
        ),
        meter,
        el(
          "small",
          `From the ${shortDay(s.high_day, true)} high` +
            (s.full_year ? "" : ` · ${s.sessions} sessions stored`),
          "muted",
        ),
      );
      box.append(tile);
    }
    return box;
  }

  function timeline(events) {
    const box = el("div", undefined, "event-timeline");
    if (!events.length) {
      box.append(
        el(
          "p",
          filters.major
            ? "No major events for this filter. Turn off Major only to see every logged event."
            : "No events logged for this filter yet.",
          "muted",
        ),
      );
      return box;
    }
    let current = null,
      list = null;
    for (const e of combine(events)) {
      if (e.day !== current) {
        current = e.day;
        const day = el("section", undefined, "event-day");
        const head = el("h2", longDay(e.day));
        list = el("div", undefined, "event-list");
        day.append(head, list);
        box.append(day);
      }
      list.append(card(e));
    }
    return box;
  }

  function method(value) {
    const box = el("details", undefined, "event-method");
    box.append(el("summary", "How events are found"));
    const rules = el("ul");
    for (const r of value.rules || []) rules.append(el("li", r.text));
    box.append(
      el(
        "p",
        "Each session is judged once, from data through its own close. The rules below decide everything; " +
          "headlines are fetched afterwards for big single-stock moves and never decide what is logged.",
        "muted",
      ),
      rules,
    );
    for (const b of value.baskets || [])
      box.append(
        el("p", `${b.label}: ${b.members.join(", ")}. ${b.note}`, "muted"),
      );
    const c = value.coverage || {};
    box.append(
      el(
        "p",
        `${number(c.sessions || 0)} sessions judged` +
          (c.first_session
            ? ` (${shortDay(c.first_session, true)} – ${shortDay(c.last_session, true)})`
            : "") +
          (c.rates_through
            ? ` · rates through ${shortDay(c.rates_through, true)}`
            : "") +
          ` · rules ${String(value.spec_sha256 || "").slice(0, 12)}`,
        "muted caption",
      ),
    );
    return box;
  }

  async function show(ticket) {
    const host = el("div", undefined, "event-results"),
      controls = el("div", undefined, "event-controls");
    let value = await api(
      `events?group=${filters.group}&severity=${filters.major ? 2 : 1}`,
    );
    if (ticket !== generation) return;
    app.className = "events-page";
    const top = el("div", undefined, "events-title");
    top.append(
      el("div", "MARKET LOG", "eyebrow"),
      el("h1", "Notable events"),
      el(
        "p",
        "Big moves, broad sell-offs, corrections and volatility spikes, logged by fixed rules from the closing data this desk stores. Context for reading results, not a forecast.",
        "muted",
      ),
    );
    app.append(top);
    if (value.standing?.length) {
      const where = el("section", undefined, "events-section");
      where.append(
        el("h2", "Where things stand"),
        el(
          "p",
          `Distance from the 52-week high at the ${shortDay(value.standing[0].day, true)} close. A correction is 10% below the high; a bear market 20%.`,
          "muted caption",
        ),
        standing(value.standing),
      );
      app.append(where);
    }
    const tabs = el("div", undefined, "segmented");
    tabs.setAttribute("role", "group");
    tabs.setAttribute("aria-label", "Show events for");
    for (const [id, label] of GROUPS) {
      const b = el("button", label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(filters.group === id));
      b.onclick = () => {
        filters.group = id;
        for (const other of tabs.children)
          other.setAttribute("aria-pressed", String(other === b));
        reload();
      };
      tabs.append(b);
    }
    const majorToggle = el("label", undefined, "event-toggle"),
      box = el("input");
    box.type = "checkbox";
    box.checked = filters.major;
    box.onchange = () => {
      filters.major = box.checked;
      reload();
    };
    majorToggle.append(box, el("span", "Major only"));
    controls.append(tabs, majorToggle);
    app.append(controls, host);
    function draw() {
      const count = value.events.length;
      host.replaceChildren(
        el(
          "p",
          count
            ? `${number(count)} ${count === 1 ? "event" : "events"}${count >= 300 ? " (newest 300)" : ""}`
            : "",
          "muted caption",
        ),
        timeline(value.events),
      );
      if (!value.coverage?.sessions)
        host.prepend(
          el(
            "p",
            "No sessions judged yet. The PAPER worker logs events after its next pass; the first pass goes back over every stored session.",
            "notice",
          ),
        );
    }
    async function reload() {
      host.setAttribute("aria-busy", "true");
      try {
        value = await api(
          `events?group=${filters.group}&severity=${filters.major ? 2 : 1}`,
        );
        if (ticket === generation) draw();
      } catch (error) {
        host.replaceChildren(el("p", error.message, "error"));
      } finally {
        host.removeAttribute("aria-busy");
      }
    }
    draw();
    app.append(method(value));
  }

  // The stock page's section: this symbol's own entries in the log.
  function section(events) {
    if (!events?.length) return null;
    const box = el("section", undefined, "sp-section");
    box.id = "sp-events";
    const head = el("div", undefined, "sp-head"),
      title = el("h2", "Notable moves");
    title.id = "sp-events-title";
    box.setAttribute("aria-labelledby", title.id);
    head.append(title, link("All events ↗", "#events"));
    const list = el("div", undefined, "event-list compact");
    for (const e of combine(events).slice(0, 6)) {
      const c = card(e);
      c.querySelector("h3").before(el("time", shortDay(e.day, true)));
      list.append(c);
    }
    box.append(head, list);
    return box;
  }

  return { card, row, section, show, standing };
})();
