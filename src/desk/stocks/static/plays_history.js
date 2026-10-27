"use strict";
// Plays history: what each session's picks did afterwards, and a P/L chart for every play from the moment it was
// suggested. Everything here is read from stored records: picks, closing marks, five-minute paths and live quotes.
const KestrelPlaysHistory = (() => {
  const svg = (tag, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs))
      node.setAttribute(key, value);
    return node;
  };
  const pct = (x, digits = 1) =>
    x === null || x === undefined
      ? "—"
      : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(100 * x).toFixed(digits)}%`;
  const usd = (x) =>
    x === null || x === undefined
      ? "—"
      : `${x > 0 ? "+" : x < 0 ? "−" : ""}$${Math.abs(x).toFixed(2)}`;
  const chance = (x) =>
    x === null || x === undefined ? "—" : `${Math.round(100 * x)}%`;
  const price = (x) =>
    x === null || x === undefined ? "—" : `$${Number(x).toFixed(2)}`;
  const tone = (x) =>
    x === null || x === undefined
      ? ""
      : x > 0
        ? "positive"
        : x < 0
          ? "negative"
          : "";
  const shortDate = (iso) =>
    new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
    });
  const longDate = (iso) =>
    new Date(`${iso.slice(0, 10)}T12:00:00`).toLocaleDateString("en-US", {
      weekday: "short",
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  const clock = (iso) =>
    new Date(iso).toLocaleTimeString("en-US", {
      timeZone: "America/New_York",
      hour: "numeric",
      minute: "2-digit",
    });
  const QUICK = [
    ["latest", "Today"],
    ["yesterday", "Yesterday"],
    ["week", "1 week"],
    ["month", "1 month"],
    ["quarter", "3 months"],
    ["year", "1 year"],
  ];
  const STATUS = {
    quoted: "recorded quote",
    settled: "settled at expiration",
    live: "live quote",
    modelled: "modelled (no closing quote)",
    estimated: "estimated (laptop was off)",
    provisional: "provisional estimate",
  };
  // Round axis steps (1, 2, 2.5, 5 × a power of ten) and a domain that starts and ends on them.
  function niceScale(lo, hi, count = 4) {
    const raw = (hi - lo || Math.abs(hi) || 1) / count,
      mag = 10 ** Math.floor(Math.log10(raw)),
      step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((v) => v >= raw);
    const start = Math.floor(lo / step) * step,
      end = Math.ceil(hi / step) * step,
      ticks = [];
    for (let v = start; v <= end + step / 2; v += step)
      ticks.push(Math.round(v / step) * step);
    return { lo: start, hi: end, ticks };
  }
  const isDashed = (status) =>
    ["modelled", "estimated", "provisional"].includes(status);

  // The three Plays views share one switcher.
  function views(active) {
    const nav = el("nav", undefined, "research-tabs plays-views");
    nav.setAttribute("aria-label", "Plays views");
    for (const [href, label, id] of [
      ["#plays", "Today's plays", "board"],
      ["#plays/history", "History", "history"],
      ["#plays/performance", "Performance", "performance"],
    ]) {
      const a = link(label, href);
      a.setAttribute("aria-selected", String(id === active));
      if (id === active) a.setAttribute("aria-current", "page");
      nav.append(a);
    }
    return nav;
  }

  function sparkline(points) {
    const box = el("span", undefined, "pl-spark");
    if (!points?.length) return box;
    const values = points.map((p) => p[1]),
      lo = Math.min(0, ...values),
      hi = Math.max(0, ...values),
      span = hi - lo || 1,
      w = 120,
      h = 32,
      x = (i) => (points.length === 1 ? w / 2 : (i / (points.length - 1)) * w),
      y = (v) => h - 3 - ((v - lo) / span) * (h - 6);
    const chart = svg("svg", {
      viewBox: `0 0 ${w} ${h}`,
      width: w,
      height: h,
      "aria-hidden": "true",
    });
    chart.append(
      svg("line", { x1: 0, x2: w, y1: y(0), y2: y(0), class: "pl-zero" }),
    );
    const cls = `pl-line ${tone(values.at(-1)) || "flat"}`;
    for (let i = 1; i < points.length; i++)
      chart.append(
        svg("line", {
          x1: x(i - 1),
          y1: y(values[i - 1]),
          x2: x(i),
          y2: y(values[i]),
          class: `${cls}${isDashed(points[i][2]) || isDashed(points[i - 1][2]) ? " dashed" : ""}`,
        }),
      );
    if (points.length === 1)
      chart.append(
        svg("circle", { cx: x(0), cy: y(values[0]), r: 2.5, class: cls }),
      );
    box.append(chart);
    return box;
  }

  // ── a session ───────────────────────────────────────────────────────────────────────────────
  function quickBar(when, list, data) {
    const bar = el("div", undefined, "pl-quick");
    for (const [key, label] of QUICK) {
      const a = link(label, `#plays/history/${key}`);
      a.className = "pl-quick-button";
      a.setAttribute("aria-pressed", String(key === when));
      bar.append(a);
    }
    const sessions = list.sessions || [],
      pick = el("label", undefined, "pl-date"),
      input = el("input");
    input.type = "date";
    input.setAttribute("aria-label", "Show the plays of a recorded session");
    if (sessions.length) {
      input.min = sessions.at(-1).session;
      input.max = sessions[0].session;
    }
    input.value = data.session || "";
    input.onchange = () => {
      if (input.value) location.hash = `#plays/history/${input.value}`;
    };
    pick.append(el("span", "Session", "muted"), input);
    bar.append(pick);
    return bar;
  }

  function summaryPanel(title, s) {
    const box = el("section", undefined, "panel pl-summary");
    box.append(el("h3", title));
    if (!s) {
      box.append(el("p", "No marks yet.", "muted"));
      return box;
    }
    const stats = el("div", undefined, "pl-stats");
    const stat = (label, value, cls) => {
      const s = el("div");
      s.append(el("span", label, "muted"), el("strong", value, cls));
      stats.append(s);
    };
    stat("Average", pct(s.average), tone(s.average));
    stat("Median", pct(s.median), tone(s.median));
    stat("In profit", `${Math.round(100 * s.hit_rate)}% of ${s.plays}`);
    stat(
      "$100 in each",
      `$${number(Math.round(s.hundred_each.value))} of $${number(s.hundred_each.invested)}`,
      tone(s.hundred_each.value - s.hundred_each.invested),
    );
    stat(
      "Random twins",
      s.twins_average === null ? "—" : pct(s.twins_average),
      tone(s.twins_average),
    );
    box.append(stats);
    box.append(
      el(
        "p",
        `Best: ${s.best.label} ${pct(s.best.pct)} · Worst: ${s.worst.label} ${pct(s.worst.pct)}${s.estimated ? ` · ${s.estimated} latest values are estimates (dashed)` : ""}`,
        "caption muted",
      ),
    );
    return box;
  }

  function playRow(session, row) {
    const a = link("", `#plays/play/${session}/${row.category}/${row.rank}`);
    a.className = "pl-row";
    a.setAttribute(
      "aria-label",
      `${row.label}: ${row.latest ? pct(row.latest.pct) : "no mark yet"}, ${row.state_label}`,
    );
    const what = el("div", undefined, "pl-what");
    what.append(
      el("strong", row.label),
      el(
        "span",
        `${row.action === "SELL" ? "Sell" : "Buy"} · entry ${price(row.entry)}${row.first_seen ? ` · first shown ${price(row.first_seen.entry)}` : ""}`,
        "muted",
      ),
    );
    const now = el("div", undefined, "pl-now");
    const latest = row.latest;
    now.append(
      el(
        "strong",
        latest ? pct(latest.pct) : "—",
        latest ? tone(latest.pct) : "",
      ),
      el(
        "span",
        latest
          ? `${latest.status === "live" ? `live ${clock(latest.at)}` : shortDate(latest.day)} · ${usd(latest.pnl)}${isDashed(latest.provisional ? "provisional" : latest.status) ? " · est." : ""}`
          : "waiting",
        "muted",
      ),
    );
    const range = el("div", undefined, "pl-range");
    range.append(
      el(
        "span",
        `Peak ${pct(row.peak, 0)} · Low ${pct(row.worst, 0)}`,
        "muted",
      ),
      el(
        "span",
        `${row.days_held} ${row.days_held === 1 ? "day" : "days"} · ${row.state_label}`,
        `muted pl-state ${row.state}`,
      ),
    );
    a.append(what, now, range, sparkline(row.spark));
    return a;
  }

  async function history(ticket, when) {
    when = when || "latest";
    const [list, data] = await Promise.all([
      api("plays/sessions"),
      api(`plays/history?when=${encodeURIComponent(when)}`),
    ]);
    if (ticket !== generation) return;
    app.className = "plays-page";
    const hero = el("div", undefined, "explorer-heading"),
      titles = el("div");
    titles.append(
      el("div", "WHAT THE PICKS DID", "eyebrow"),
      el(
        "h1",
        data.session ? `Plays of ${longDate(data.session)}` : "Plays history",
      ),
      el(
        "p",
        "Every pick as if bought at that session's close (and when first shown), marked every session until it expires. Percentages are on the money at risk, after fees. Research, not financial advice.",
        "muted",
      ),
    );
    hero.append(titles);
    app.append(hero, views("history"), quickBar(when, list, data));
    if (data.repair?.pending || data.repair?.fetching)
      app.append(
        el(
          "p",
          `Repairing ${data.repair.fetching ? `${data.repair.fetching} stocks' history` : `${data.repair.pending} plays`}: filling a gap from Schwab's price history. Estimates are dashed until real quotes bracket them.`,
          "caption pl-repair",
        ),
      );
    if (
      when !== "latest" &&
      data.session &&
      /^\d/.test(when) &&
      data.session !== when
    )
      app.append(
        el(
          "p",
          `No plays were recorded on ${when}; showing the nearest earlier session, ${data.session}.`,
          "caption muted",
        ),
      );
    if (!data.categories?.length) {
      app.append(panel("Nothing recorded yet", data.message || ""));
      return;
    }
    app.append(summaryPanel("This session", data.summary));
    if ((data.methods || []).length > 1)
      app.append(
        el(
          "p",
          `This session mixes ${data.methods.length} rule versions; each play keeps the version it was picked under.`,
          "caption muted",
        ),
      );
    for (const cat of data.categories) {
      const section = el("section", undefined, "pl-category");
      const head = el("div", undefined, "section-heading");
      const s = cat.summary;
      head.append(
        el("h2", `${cat.emoji} ${cat.title}`),
        el(
          "p",
          s
            ? `${s.plays} plays · average ${pct(s.average)} · median ${pct(s.median)} · ${Math.round(100 * s.hit_rate)}% in profit · random twins ${pct(s.twins_average)}`
            : "No marks yet",
          "muted",
        ),
      );
      section.append(head);
      const list = el("div", undefined, "pl-rows");
      for (const row of cat.plays) list.append(playRow(data.session, row));
      section.append(list);
      app.append(section);
    }
    app.append(
      el(
        "p",
        "At the close = bought at the ask of the session's last quotes. First shown = bought at the ask when the play first appeared on the live board. Values are what closing would bring: long legs at the bid, short legs bought back at the ask. Solid lines are real quotes or settlements; dashed are modelled or estimated (never used for grading or learning). Random twins are matched contracts on other stocks with the same expiry and delta.",
        "muted caption",
      ),
    );
  }

  // ── the P/L chart ───────────────────────────────────────────────────────────────────────────
  const pointTime = (p) =>
    Date.parse(p.at || (p.t.length === 10 ? `${p.t}T21:00:00Z` : p.t));
  const pointWhen = (p) =>
    `${longDate(p.t)} · ${clock(new Date(pointTime(p)).toISOString())} ET${p.t.length === 10 ? " close" : ""}`;

  function series(data, range) {
    const daily = data.daily || [],
      intraday = data.intraday || [];
    const dayOf = (p) =>
      p.t.length === 10
        ? p.t
        : new Date(p.t).toLocaleDateString("en-CA", {
            timeZone: "America/New_York",
          });
    // Keep the actual intraday path in EVERY range, not only 1D/1W. A closing-only chart hides a spike followed
    // by a reversal. Deduplicate the same instant, preserving a real quote over a modelled daily value.
    const points = new Map();
    for (const p of [...intraday, ...daily]) {
      const key = pointTime(p),
        previous = points.get(key);
      if (!previous || isDashed(previous.status) || !isDashed(p.status))
        points.set(key, p);
    }
    const dense = [...points.values()].sort(
      (a, b) => pointTime(a) - pointTime(b),
    );
    const sessions = [...new Set(dense.map(dayOf))];
    if (range === "1D") {
      const last = sessions.at(-1);
      return dense.filter((p) => dayOf(p) === last);
    }
    if (range === "1W") {
      const keep = new Set(sessions.slice(-5));
      return dense.filter((p) => keep.has(dayOf(p)));
    }
    if (range === "1M") {
      const since = dense.length ? pointTime(dense.at(-1)) - 31 * 864e5 : 0;
      return dense.filter((p) => pointTime(p) >= since);
    }
    return dense;
  }

  function plChart(data) {
    const box = el("section", undefined, "pl-chart"),
      controls = el("div", undefined, "pl-controls"),
      plot = el("div", undefined, "pl-plot"),
      readout = el("p", "", "caption pl-readout"),
      legend = el("p", "", "caption muted");
    const hasFirst = data.bases.first !== undefined;
    let range = "All",
      unit = "usd",
      basis = hasFirst ? "first" : "close",
      twin = false,
      includeEstimates = false,
      quantity = 1,
      inspectedTime = null,
      seekPeak = false;
    const summary = el("div", undefined, "pl-exit-summary");
    const metric = (title, cls) => {
      const node = el("section", undefined, `pl-exit-metric ${cls}`),
        label = el("h3", title),
        value = el("strong", "—"),
        meta = el("p", "", "caption muted");
      node.append(label, value, meta);
      summary.append(node);
      return { node, label, value, meta };
    };
    const selected = metric("If closed at selected time", "pl-selected-exit"),
      peakCard = metric("Best observed exit before expiry", "pl-best-exit"),
      endCard = metric("Latest recorded value", "pl-latest-exit");
    const jump = el("button", "Show best exit on chart", "pl-jump-peak");
    jump.type = "button";
    jump.onclick = () => {
      range = "All";
      seekPeak = true;
      for (const b of controls.querySelector('[aria-label="Time range"]')
        .children)
        b.setAttribute("aria-pressed", String(b.textContent === "All"));
      draw();
      plot.scrollIntoView({ block: "center" });
    };
    peakCard.node.append(jump);
    const contracts = el("label", undefined, "pl-contracts"),
      input = el("input");
    input.type = "number";
    input.min = "1";
    input.step = "1";
    input.value = "1";
    contracts.append(
      el("span", data.legs.length > 1 ? "Contracts per leg" : "Contracts"),
      input,
    );
    input.oninput = () => {
      const n = Number(input.value);
      const valid = Number.isSafeInteger(n) && n > 0;
      input.setCustomValidity(
        valid ? "" : "Enter a positive whole number of contracts.",
      );
      if (valid) {
        quantity = n;
        draw();
      }
    };
    const showMetric = (target, p) => {
      target.value.textContent = p
        ? `${usd(p.pnl * quantity)} (${pct(p.pct)})`
        : "—";
      target.value.className =
        p && isDashed(p.status) ? "pl-estimated-value" : p ? tone(p.pnl) : "";
      target.meta.textContent = p
        ? `${pointWhen(p)} · ${quantity} contract${quantity === 1 ? "" : "s"}${data.legs.length > 1 ? " per leg" : ""}`
        : "No eligible quote-based exit recorded yet.";
    };
    const group = (label, options, get, set) => {
      const g = el("div", undefined, "research-tabs pl-group");
      g.setAttribute("role", "group");
      g.setAttribute("aria-label", label);
      for (const [value, text, disabled] of options) {
        const b = el("button", text);
        b.type = "button";
        b.disabled = Boolean(disabled);
        b.setAttribute("aria-pressed", String(get() === value));
        b.onclick = () => {
          set(value);
          for (const c of g.children)
            c.setAttribute("aria-pressed", String(c === b));
          draw();
        };
        g.append(b);
      }
      return g;
    };
    controls.append(
      group(
        "Time range",
        [
          ["1D", "1D"],
          ["1W", "1W"],
          ["1M", "1M"],
          ["All", "All"],
        ],
        () => range,
        (v) => (range = v),
      ),
      group(
        "Units",
        [
          ["pct", "%"],
          ["usd", "$"],
        ],
        () => unit,
        (v) => (unit = v),
      ),
      group(
        "Bought",
        [
          ["close", "At the close"],
          ["first", "When first shown", !hasFirst],
        ],
        () => basis,
        (v) => (basis = v),
      ),
      contracts,
    );
    if (data.twin?.marks?.length) {
      const t = el("label", undefined, "pl-toggle"),
        box2 = el("input");
      box2.type = "checkbox";
      box2.onchange = () => {
        twin = box2.checked;
        draw();
      };
      t.append(box2, el("span", `Random twin (${data.twin.symbol})`));
      controls.append(t);
    }
    const estimates = el("label", undefined, "pl-toggle"),
      estimatesInput = el("input");
    estimatesInput.type = "checkbox";
    estimatesInput.onchange = () => {
      includeEstimates = estimatesInput.checked;
      draw();
    };
    estimates.append(estimatesInput, el("span", "Include estimated gaps"));
    controls.append(estimates);
    const method = el("details", undefined, "pl-chart-method"),
      methodBody = el("p", "", "caption muted");
    method.append(
      el("summary", "How this P/L and peak are calculated"),
      methodBody,
    );
    box.append(summary, controls, plot, readout, legend, method);

    function draw() {
      const available = series(data, range).filter((p) => p[basis]);
      const hiddenTimes = includeEstimates
        ? []
        : available.filter((p) => isDashed(p.status)).map(pointTime);
      const rows = available.filter(
        (p) => includeEstimates || !isDashed(p.status),
      );
      plot.replaceChildren();
      const exits = data.exits?.[basis] || {},
        best = exits.best,
        latest = exits.latest;
      showMetric(peakCard, best);
      jump.disabled = !best;
      showMetric(endCard, latest);
      endCard.label.textContent =
        latest?.status === "settled"
          ? "Expiry outcome — for comparison"
          : latest && isDashed(latest.status)
            ? "Latest estimated value"
            : "Latest recorded value";
      if (latest && exits.giveback > 0)
        endCard.meta.append(
          document.createTextNode(
            ` · ${usd(exits.giveback * quantity)} below the best observed exit`,
          ),
        );
      methodBody.textContent = [
        data.intraday_note,
        "Best observed exit uses retained bid/ask quotes after your selected entry, before settlement, and includes fees. It is hindsight, not a forecast or a guaranteed fill; unobserved peaks may be missed.",
        "The quoted option value reflects stock moves, remaining time and implied volatility, rather than intrinsic value alone. Contract quantity applies to each leg.",
        exits.best_estimate
          ? `Estimated high (not observed): ${usd(exits.best_estimate.pnl * quantity)} · ${pct(exits.best_estimate.pct)}.`
          : "",
      ]
        .filter(Boolean)
        .join(" ");
      if (!rows.length) {
        plot.append(
          el(
            "p",
            available.length
              ? "Only estimated values are available in this range. Turn on Include estimated gaps to inspect them; no quote-based sale opportunity is established."
              : "No marks in this range yet. Daily marks start at the close it was picked; five-minute marks while the dashboard tracks it.",
            "muted",
          ),
        );
        readout.textContent = "";
        legend.textContent =
          "No observed quote path is available in this range.";
        showMetric(selected, null);
        return;
      }
      const value = (p) =>
        unit === "pct" ? p[basis].pct : p[basis].pnl * quantity;
      const values = rows.map(value);
      const daily = range === "1M" || range === "All";
      const twinRows =
        twin && daily && unit === "pct"
          ? rows.map(
              (p) =>
                (data.twin.marks.find((m) => m.t === p.t.slice(0, 10)) || {})
                  .pct ?? null,
            )
          : [];
      const all = [...values, ...twinRows.filter((v) => v !== null), 0];
      const scale = niceScale(Math.min(...all), Math.max(...all)),
        lo = scale.lo,
        hi = scale.hi;
      const width =
          window.innerWidth < 600
            ? Math.max(260, window.innerWidth - 48)
            : 1000,
        height = 300,
        left = 70,
        right = 20,
        top = 16,
        bottom = 34,
        plotW = width - left - right,
        plotH = height - top - bottom;
      const times = rows.map(pointTime),
        span = times.at(-1) - times[0] || 1;
      const x = (i) =>
          left +
          (rows.length === 1
            ? plotW / 2
            : ((times[i] - times[0]) / span) * plotW),
        y = (v) => top + (1 - (v - lo) / (hi - lo)) * plotH;
      const fmt = (v) =>
        unit === "pct"
          ? pct(v, Math.abs(hi - lo) < 0.05 ? 1 : 0)
          : usd(v).replace(".00", "");
      const chart = svg("svg", {
        viewBox: `0 0 ${width} ${height}`,
        preserveAspectRatio: "none",
        role: "img",
        tabindex: "0",
        "aria-label": `Profit and loss of ${data.label}, ${rows.length} marks. Arrow keys inspect each mark.`,
      });
      for (const v of scale.ticks) {
        chart.append(
          svg("line", {
            x1: left,
            x2: width - right,
            y1: y(v),
            y2: y(v),
            class: "chart-guide",
          }),
        );
        const label = svg("text", {
          x: left - 8,
          y: y(v) + 4,
          "text-anchor": "end",
          class: "pl-axis",
        });
        label.textContent = fmt(v);
        chart.append(label);
      }
      chart.append(
        svg("line", {
          x1: left,
          x2: width - right,
          y1: y(0),
          y2: y(0),
          class: "pl-zero",
        }),
      );
      if (twinRows.some((v) => v !== null)) {
        let d = "";
        twinRows.forEach((v, i) => {
          if (v === null) return;
          d += `${d && twinRows[i - 1] !== null ? "L" : "M"}${x(i)},${y(v)}`;
        });
        chart.append(svg("path", { d, class: "pl-twin" }));
      }
      const lineTone = tone(values.at(-1)) || "flat";
      for (let i = 1; i < rows.length; i++) {
        if (hiddenTimes.some((t) => times[i - 1] < t && t < times[i])) continue;
        const dashed = isDashed(rows[i].status) || isDashed(rows[i - 1].status);
        chart.append(
          svg("line", {
            x1: x(i - 1),
            y1: y(values[i - 1]),
            x2: x(i),
            y2: y(values[i]),
            class: `pl-line ${lineTone}${dashed ? " dashed" : ""}`,
          }),
        );
      }
      const peak = best
          ? rows.findIndex((p) => pointTime(p) === Date.parse(best.at))
          : -1,
        low = values.indexOf(Math.min(...values));
      const marker = (i, kind, title) => {
        const m = svg("circle", {
          cx: x(i),
          cy: y(values[i]),
          r: kind === "entry" ? 4.5 : 4,
          class: `pl-marker ${kind}`,
        });
        const t = svg("title", {});
        t.textContent = title;
        m.append(t);
        chart.append(m);
      };
      const first = (data.daily || [])[0];
      if (first && rows[0].t === first.t)
        marker(0, "entry", `Picked ${first.t}`);
      if (peak >= 0) {
        marker(
          peak,
          "peak",
          `Best observed exit ${usd(best.pnl * quantity)} (${pct(best.pct)}) · ${pointWhen(best)}`,
        );
        const annotation = svg("text", {
          x: x(peak),
          y: Math.max(top + 14, y(values[peak]) - 12),
          "text-anchor": x(peak) > width / 2 ? "end" : "start",
          class: "pl-peak-label",
        });
        annotation.textContent = `Best ${usd(best.pnl * quantity)}`;
        chart.append(annotation);
      }
      if (rows.length > 2 && low !== 0)
        marker(low, "low", `Low ${fmt(values[low])}`);
      if (rows.at(-1).status === "settled")
        marker(rows.length - 1, "expiry", `Expired ${rows.at(-1).t}`);
      const guide = svg("line", {
          x1: 0,
          x2: 0,
          y1: top,
          y2: height - bottom,
          class: "pl-crosshair",
        }),
        dot = svg("circle", {
          cx: 0,
          cy: 0,
          r: 5,
          class: `pl-dot ${lineTone}`,
        });
      chart.append(guide, dot);
      const tooltip = el("div", undefined, "pl-tooltip");
      tooltip.hidden = true;
      const previousIndex = rows.findIndex(
        (p) => pointTime(p) === inspectedTime,
      );
      let index =
        seekPeak && peak >= 0
          ? peak
          : previousIndex >= 0
            ? previousIndex
            : rows.length - 1;
      seekPeak = false;
      function inspect(i) {
        index = Math.max(0, Math.min(rows.length - 1, i));
        const p = rows[index];
        inspectedTime = pointTime(p);
        dot.setAttribute(
          "class",
          `pl-dot ${isDashed(p.status) ? "estimated" : tone(values[index]) || "flat"}`,
        );
        guide.setAttribute("x1", x(index));
        guide.setAttribute("x2", x(index));
        dot.setAttribute("cx", x(index));
        dot.setAttribute("cy", y(values[index]));
        const when = pointWhen(p);
        showMetric(selected, { ...p, ...p[basis] });
        selected.label.textContent =
          p.status === "settled"
            ? "At expiry — not an earlier sale"
            : isDashed(p.status)
              ? "Estimated P/L at selected time"
              : "If closed at selected time";
        readout.replaceChildren(
          el("strong", when),
          document.createTextNode(` · worth ${price(p.value)}/share · P/L `),
          el(
            "strong",
            `${usd(p[basis].pnl * quantity)} (${pct(p[basis].pct)})`,
            tone(p[basis].pct),
          ),
          document.createTextNode(
            ` · ${data.play.symbol} ${price(p.underlying)} · ${STATUS[p.status] || p.status}${p.note ? ` · ${p.note}` : ""}`,
          ),
        );
        tooltip.replaceChildren(
          el(
            "strong",
            `${usd(p[basis].pnl * quantity)} · ${pct(p[basis].pct)}`,
          ),
          el("span", when),
          el("span", STATUS[p.status] || p.status),
        );
      }
      chart.onpointermove = (e) => {
        const b = chart.getBoundingClientRect();
        const target =
          times[0] +
          ((((e.clientX - b.left) / b.width) * width - left) / plotW) * span;
        let lo = 0,
          hi = times.length - 1;
        while (lo < hi) {
          const middle = Math.floor((lo + hi) / 2);
          if (times[middle] < target) lo = middle + 1;
          else hi = middle;
        }
        inspect(
          lo > 0 &&
            Math.abs(times[lo - 1] - target) < Math.abs(times[lo] - target)
            ? lo - 1
            : lo,
        );
        tooltip.hidden = false;
        const bounds = plot.getBoundingClientRect();
        tooltip.style.left = `${Math.max(0, Math.min(bounds.width - tooltip.offsetWidth, e.clientX - bounds.left + 12))}px`;
        tooltip.style.top = `${Math.max(0, Math.min(bounds.height - tooltip.offsetHeight, e.clientY - bounds.top - tooltip.offsetHeight - 12))}px`;
      };
      chart.onpointerleave = () => {
        tooltip.hidden = true;
      };
      chart.onpointerdown = chart.onpointermove;
      chart.onkeydown = (e) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
        e.preventDefault();
        inspect(
          e.key === "Home"
            ? 0
            : e.key === "End"
              ? rows.length - 1
              : index + (e.key === "ArrowLeft" ? -1 : 1),
        );
      };
      const axis = el("div", undefined, "chart-axis");
      const tick = (p) =>
        range === "1D"
          ? clock(new Date(pointTime(p)).toISOString())
          : shortDate(p.t);
      const middleTime = new Date((times[0] + times.at(-1)) / 2);
      const middlePoint = {
        t: middleTime.toLocaleDateString("en-CA", {
          timeZone: "America/New_York",
        }),
        at: middleTime.toISOString(),
      };
      axis.append(
        el("span", tick(rows[0])),
        el("span", rows.length > 2 ? tick(middlePoint) : ""),
        el("span", tick(rows.at(-1))),
      );
      plot.append(chart, axis, tooltip);
      inspect(index);
      const statuses = new Set(rows.map((p) => p.status));
      legend.textContent = [
        `${rows.length} stored marks in this range. Solid: quoted or settled.`,
        [...statuses].some(isDashed)
          ? "Dashed: modelled or estimated (not real quotes; never graded)."
          : "",
        hiddenTimes.length
          ? `${hiddenTimes.length} estimated marks hidden; unquoted intervals are left as gaps. Use Include estimated gaps to inspect the modelled path.`
          : "",
        twinRows.length ? "Grey: the random twin." : "",
        data.expired
          ? `Expired ${data.settles}: nothing is drawn after it.`
          : `Expires ${data.expiration}; the line extends each session until then.`,
        rows.length < 3
          ? "Too few observations to show the intervening path; no extra price movement has been invented."
          : "",
      ]
        .filter(Boolean)
        .join(" ");
    }
    draw();
    return box;
  }

  // ── one play ────────────────────────────────────────────────────────────────────────────────
  function greeksTable(g) {
    const rows = [
      [
        "Implied volatility",
        "iv",
        (v) =>
          v === null || v === undefined ? "—" : `${(100 * v).toFixed(1)}%`,
      ],
      [
        "Delta",
        "delta",
        (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(3)),
      ],
      [
        "Gamma",
        "gamma",
        (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(4)),
      ],
      [
        "Theta / day",
        "theta",
        (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(3)),
      ],
      [
        "Vega / vol pt",
        "vega",
        (v) => (v === null || v === undefined ? "—" : Number(v).toFixed(3)),
      ],
    ];
    return table(
      ["", "Picked", "Now", "Schwab now"],
      rows.map(([label, key, f]) => [
        label,
        f(g.then[key]),
        f(g.now[key]),
        f(g.schwab_now[key]),
      ]),
    );
  }

  async function detail(ticket, session, category, rank) {
    const data = await api(
      `plays/play?session=${encodeURIComponent(session)}&category=${encodeURIComponent(category)}&rank=${encodeURIComponent(rank)}`,
    );
    if (ticket !== generation) return;
    app.className = "plays-page";
    const p = data.play,
      last = (data.daily || []).at(-1),
      live = (data.intraday || []).filter((x) => x.status === "live").at(-1),
      latest = live && (!last || live.t.slice(0, 10) > last.t) ? live : last;
    const hero = el("div", undefined, "explorer-heading"),
      titles = el("div");
    titles.append(
      el(
        "div",
        `${data.emoji} ${data.category_title.toUpperCase()} · PICKED ${longDate(data.session).toUpperCase()}`,
        "eyebrow",
      ),
      el("h1", data.label),
    );
    titles.append(
      el(
        "p",
        "Explore selling before expiry: inspect the option's recorded P/L path, compare an earlier exit with the final result, and change the contract quantity.",
        "muted",
      ),
    );
    hero.append(titles);
    app.append(
      hero,
      views("history"),
      link(
        `← All plays of ${longDate(data.session)}`,
        `#plays/history/${data.session}`,
      ),
    );
    if (data.quarantine) app.append(panel("Quarantined", data.quarantine));
    app.append(plChart(data));
    const grid = el("div", undefined, "pl-detail-grid");
    const why = el("section", undefined, "panel");
    why.append(el("h3", "Why it was picked"));
    const reasons = el("ul", undefined, "play-reasons");
    for (const r of p.reasons || []) reasons.append(el("li", r));
    for (const f of p.flags || []) reasons.append(el("li", f, "play-flag"));
    why.append(
      reasons,
      el(
        "p",
        `${p.chance_label || "Chance"}: KESTREL ${chance(p.itm_kestrel)} · market ${chance(p.itm_market)} · history ${chance(p.itm_history)} · chance of profit ${chance(p.profit_kestrel)}`,
        "caption",
      ),
      el(
        "p",
        `${p.action === "SELL" ? "Credit" : "Cost"} ${price(p.action === "SELL" ? p.credit : p.cost)} · worst case ${price(p.max_loss)} · break-even ${price(p.breakeven)} · expected value ${usd(p.expected_value)} · stock ${price(p.underlying_price)} when picked`,
        "caption muted",
      ),
    );
    if (data.first_seen)
      why.append(
        el(
          "p",
          `First shown ${new Date(data.first_seen.at).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} ET at ${price(data.first_seen.entry)}; recorded at the close at ${price(data.bases.close)}.`,
          "caption",
        ),
      );
    else
      why.append(
        el(
          "p",
          "Not seen on the live board before the close (the dashboard was not running), so only the close basis exists.",
          "caption muted",
        ),
      );
    const greeks = el("section", undefined, "panel");
    greeks.append(el("h3", "Greeks then and now"), greeksTable(data.greeks));
    greeks.append(
      el(
        "p",
        `Net for the position as held (short legs count negative). Now = KESTREL's own, solved from the ${data.greeks.as_of || "latest"} closing mid; Schwab's come with Schwab's quotes when it sends them.`,
        "caption muted",
      ),
    );
    const happened = el("section", undefined, "panel");
    happened.append(el("h3", "Earlier exits and expiry comparison"));
    const bestExit =
      data.exits?.[data.bases.first !== undefined ? "first" : "close"]?.best;
    if (bestExit)
      happened.append(
        el(
          "p",
          `Best observed earlier exit: ${usd(bestExit.pnl)} (${pct(bestExit.pct)}) per contract, ${pointWhen(bestExit)}. This uses the recorded closing bid/ask and fees; it was not a prediction of the peak.`,
        ),
      );
    if (data.outcome)
      happened.append(
        el(
          "p",
          `Holding until expiry: the stock closed at ${price(data.outcome.underlying_close)}; the position's intrinsic value was ${price(data.outcome.payoff)} a share, ${pct(data.outcome.net_return)} after fees. This expiry comparison is separate from the earlier sale opportunities; no exercise or assignment was performed.`,
        ),
      );
    else if (latest)
      happened.append(
        el(
          "p",
          `Still open. Latest: ${pct(latest.close.pct)} at the close basis${latest.first ? `, ${pct(latest.first.pct)} if bought when first shown` : ""}.`,
        ),
      );
    if (data.twin) {
      const tl = data.twin.marks.at(-1);
      happened.append(
        el(
          "p",
          `Random twin: ${data.twin.symbol} ${data.twin.legs.map((l) => `$${l.strike} ${l.kind}`).join("/")} expiring ${data.twin.expiration} — ${tl ? pct(tl.pct) : "no mark yet"}.`,
          "caption muted",
        ),
      );
    }
    grid.append(why, greeks, happened);
    app.append(grid);
    const marks = el("details", undefined, "pl-marks");
    marks.append(el("summary", `Every daily mark (${data.daily.length})`));
    marks.append(
      table(
        ["Session", "Status", "Worth", "P/L", "P/L %", "Stock", "Note"],
        [...data.daily]
          .reverse()
          .map((m) => [
            m.t,
            STATUS[m.status] || m.status,
            price(m.value),
            usd(m.close.pnl),
            pct(m.close.pct),
            price(m.underlying),
            m.note || "",
          ]),
      ),
    );
    app.append(marks);
    app.append(
      el(
        "p",
        `Picked under rule version ${data.method}. Marks are append-only: a later quote never changes an earlier one. Research, not financial advice.`,
        "caption muted",
      ),
    );
  }

  // ── performance across sessions ─────────────────────────────────────────────────────────────
  const cell = (s) => {
    if (!s) return "—";
    const box = el("span", undefined, "pf-cell");
    box.append(
      el("strong", pct(s.mean), tone(s.mean)),
      el("small", `${Math.round(100 * s.hit_rate)}% up · n ${s.n}`, "muted"),
    );
    return box;
  };

  function cumulativeChart(curves, categories) {
    const box = el("section", undefined, "pf-chart"),
      controls = el("div", undefined, "pl-controls"),
      plot = el("div", undefined, "pl-plot"),
      readout = el("p", "", "caption pl-readout"),
      legend = el("div", undefined, "pf-legend");
    const pick = el("select");
    pick.setAttribute("aria-label", "Category");
    pick.append(new Option("Every category", "all"));
    for (const c of categories)
      if (curves[c.id]) pick.append(new Option(`${c.emoji} ${c.title}`, c.id));
    controls.append(pick);
    const series = [
      ["picks", "Picks", "pf-picks"],
      ["twins", "Random twins", "pf-twins"],
      ["spy", "SPY, same dates", "pf-spy"],
    ];
    for (const [, label, cls] of series) {
      const item = el("span", undefined, "pf-key");
      item.append(el("i", undefined, cls), document.createTextNode(label));
      legend.append(item);
    }
    box.append(
      el("h2", "$100 into every play"),
      el(
        "p",
        "Cumulative profit or loss of putting $100 into each play when it was picked (at the close), counted when it settles, against the same $100 into its random twin and into SPY over the same dates.",
        "muted",
      ),
      controls,
      legend,
      plot,
      readout,
    );
    function draw() {
      const rows = curves[pick.value] || [];
      plot.replaceChildren();
      if (!rows.length) {
        plot.append(el("p", "No play in this view has settled yet.", "muted"));
        readout.textContent = "";
        return;
      }
      const values = rows.flatMap((r) => [r.picks, r.twins, r.spy, 0]),
        scale = niceScale(Math.min(...values), Math.max(...values)),
        lo = scale.lo,
        hi = scale.hi;
      const width = window.innerWidth < 600 ? 520 : 1000,
        height = 280,
        left = 70,
        right = 110,
        top = 14,
        bottom = 30,
        plotW = width - left - right,
        plotH = height - top - bottom;
      const x = (i) =>
          left +
          (rows.length === 1 ? plotW / 2 : (i / (rows.length - 1)) * plotW),
        y = (v) => top + (1 - (v - lo) / (hi - lo)) * plotH;
      const chart = svg("svg", {
        viewBox: `0 0 ${width} ${height}`,
        preserveAspectRatio: "none",
        role: "img",
        tabindex: "0",
        "aria-label": `Cumulative profit of $100 per play, ${rows.length} settlement dates. Arrow keys inspect each date.`,
      });
      for (const v of scale.ticks) {
        chart.append(
          svg("line", {
            x1: left,
            x2: width - right,
            y1: y(v),
            y2: y(v),
            class: "chart-guide",
          }),
        );
        const t = svg("text", {
          x: left - 8,
          y: y(v) + 4,
          "text-anchor": "end",
          class: "pl-axis",
        });
        t.textContent = usd(v).replace(".00", "");
        chart.append(t);
      }
      chart.append(
        svg("line", {
          x1: left,
          x2: width - right,
          y1: y(0),
          y2: y(0),
          class: "pl-zero",
        }),
      );
      for (const [key, label, cls] of series) {
        const d = rows
          .map((r, i) => `${i ? "L" : "M"}${x(i)},${y(r[key])}`)
          .join("");
        chart.append(svg("path", { d, class: `pf-line ${cls}` }));
        if (rows.length === 1)
          chart.append(
            svg("circle", {
              cx: x(0),
              cy: y(rows[0][key]),
              r: 4,
              class: `pf-dot ${cls}`,
            }),
          );
      }
      // direct labels at the line ends, nudged apart so they never overlap
      const ends = series
        .map(([key, label]) => ({
          label: label.split(",")[0],
          y: y(rows.at(-1)[key]),
        }))
        .sort((a, b) => a.y - b.y);
      for (let i = 1; i < ends.length; i++)
        ends[i].y = Math.max(ends[i].y, ends[i - 1].y + 15);
      for (const e of ends) {
        const end = svg("text", {
          x: x(rows.length - 1) + 8,
          y: e.y + 4,
          class: "pf-end",
        });
        end.textContent = e.label;
        chart.append(end);
      }
      const guide = svg("line", {
        x1: 0,
        x2: 0,
        y1: top,
        y2: height - bottom,
        class: "pl-crosshair",
      });
      chart.append(guide);
      let index = rows.length - 1;
      function inspect(i) {
        index = Math.max(0, Math.min(rows.length - 1, i));
        const r = rows[index];
        guide.setAttribute("x1", x(index));
        guide.setAttribute("x2", x(index));
        readout.replaceChildren(
          el("strong", longDate(r.t)),
          document.createTextNode(
            ` · ${r.settled} settled ($${number(r.invested)} put in) · picks `,
          ),
          el("strong", usd(r.picks), tone(r.picks)),
          document.createTextNode(" · random twins "),
          el("strong", usd(r.twins), tone(r.twins)),
          document.createTextNode(" · SPY "),
          el("strong", usd(r.spy), tone(r.spy)),
        );
      }
      chart.onpointermove = (e) => {
        const b = chart.getBoundingClientRect();
        inspect(
          Math.round(
            ((((e.clientX - b.left) / b.width) * width - left) / plotW) *
              (rows.length - 1),
          ),
        );
      };
      chart.onpointerleave = () => inspect(rows.length - 1);
      chart.onkeydown = (e) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
        e.preventDefault();
        inspect(
          e.key === "Home"
            ? 0
            : e.key === "End"
              ? rows.length - 1
              : index + (e.key === "ArrowLeft" ? -1 : 1),
        );
      };
      const axis = el("div", undefined, "chart-axis");
      axis.append(
        el("span", shortDate(rows[0].t)),
        el("span", shortDate(rows.at(-1).t)),
      );
      plot.append(chart, axis);
      inspect(index);
    }
    pick.onchange = draw;
    draw();
    return box;
  }

  function calibrationChart(cal) {
    const box = el("section", undefined, "pf-chart"),
      readout = el("p", "Hover a dot to see its bucket.", "caption pl-readout"),
      legend = el("div", undefined, "pf-legend");
    const groups = [
      ["itm_kestrel", "pf-picks"],
      ["itm_market", "pf-spy"],
    ];
    for (const [key, cls] of groups) {
      const item = el("span", undefined, "pf-key");
      item.append(
        el("i", undefined, cls),
        document.createTextNode(
          `${cal[key].label} (${cal[key].resolved} resolved${cal[key].brier === null ? "" : `, Brier ${cal[key].brier}`})`,
        ),
      );
      legend.append(item);
    }
    box.append(
      el("h2", "Predicted chance vs what happened"),
      el(
        "p",
        "Each dot is a tenth of predicted chances: across (what was predicted) against up (how often the play's event actually happened at expiry). Dots on the diagonal are well calibrated. KESTREL's chance is what the page showed when the play was picked.",
        "muted",
      ),
      legend,
    );
    const size = 320,
      pad = 40,
      span = size - 2 * pad,
      x = (p) => pad + p * span,
      y = (p) => size - pad - p * span;
    const chart = svg("svg", {
      viewBox: `0 0 ${size} ${size}`,
      class: "pf-calibration",
      role: "img",
      "aria-label":
        "Calibration: predicted chance against observed frequency, by tenths. A table follows.",
    });
    for (const v of [0, 0.25, 0.5, 0.75, 1]) {
      chart.append(
        svg("line", {
          x1: x(0),
          x2: x(1),
          y1: y(v),
          y2: y(v),
          class: "chart-guide",
        }),
      );
      const a = svg("text", {
        x: x(0) - 6,
        y: y(v) + 4,
        "text-anchor": "end",
        class: "pl-axis",
      });
      a.textContent = `${100 * v}%`;
      const b = svg("text", {
        x: x(v),
        y: size - pad + 16,
        "text-anchor": "middle",
        class: "pl-axis",
      });
      b.textContent = `${100 * v}%`;
      chart.append(a, b);
    }
    chart.append(
      svg("line", {
        x1: x(0),
        y1: y(0),
        x2: x(1),
        y2: y(1),
        class: "pf-diagonal",
      }),
    );
    for (const [key, cls] of groups)
      for (const p of cal[key].points) {
        const dot = svg("circle", {
          cx: x(p.predicted),
          cy: y(p.observed),
          r: Math.max(4, Math.min(10, 2 + Math.sqrt(p.n))),
          class: `pf-dot ${cls}`,
          tabindex: "0",
        });
        const title = svg("title", {});
        const text = `${cal[key].label} · predicted ${p.bucket} (mean ${Math.round(100 * p.predicted)}%) · happened ${Math.round(100 * p.observed)}% of ${p.n}`;
        title.textContent = text;
        dot.append(title);
        dot.onpointerenter = dot.onfocus = () => (readout.textContent = text);
        chart.append(dot);
      }
    box.append(chart, readout);
    const rows = groups.flatMap(([key]) =>
      cal[key].points.map((p) => [
        cal[key].label,
        p.bucket,
        `${Math.round(100 * p.predicted)}%`,
        `${Math.round(100 * p.observed)}%`,
        p.n,
      ]),
    );
    const t = el("details", undefined, "pl-marks");
    t.append(
      el("summary", "As a table"),
      table(
        ["Model", "Predicted", "Mean predicted", "Happened", "Plays"],
        rows,
      ),
    );
    box.append(t);
    return box;
  }

  // What the nightly learning concluded: labels, exit rules, inputs, challengers. Failures stay listed.
  function learningSection(report, titles) {
    const box = el("section", undefined, "pf-section"),
      title = (id) =>
        titles[id] ? `${titles[id].emoji} ${titles[id].title}` : id,
      graded = (report.challengers || []).filter((t) => t.sessions || t.live),
      p = (x) =>
        x === null || x === undefined
          ? "—"
          : x < 0.001
            ? "< 0.001"
            : x.toFixed(3);
    box.append(
      el("h2", "What it has learned"),
      el(
        "p",
        `Report for the ${longDate(report.session)} close, rules ${report.method}. Grading uses quoted marks and exact settlements only; every test compares sessions with a stationary block bootstrap (nearby sessions are not independent) and is Holm-corrected across everything tested.`,
        "muted",
      ),
      el("h3", "Categories"),
      table(
        ["Category", "Verdict"],
        Object.entries(report.labels || {}).map(([id, l]) => [
          title(id),
          el("span", l.text, `pf-label ${l.state}`),
        ]),
      ),
      el("h3", "Exit rules (walk-forward)"),
      table(
        [
          "Category",
          "Best rule so far",
          "Unseen sessions",
          "vs holding",
          "Holm p",
          "Use it?",
        ],
        Object.entries(report.exits || {}).map(([id, e]) => [
          title(id),
          e.best_in_sample?.rule || "not enough settled plays",
          e.test_sessions,
          e.mean_improvement === null ? "—" : pct(e.mean_improvement),
          p(e.p_holm),
          e.suggested ? "Suggested" : "Hold to expiry until proven",
        ]),
      ),
      el("h3", "Which inputs predicted the return a week later"),
      table(
        ["Input", "Average rank correlation", "Sessions", "p"],
        (report.features || []).map((f) => [
          f.label,
          f.ic === null ? "—" : f.ic.toFixed(3),
          f.sessions,
          p(f.p),
        ]),
      ),
      el("h3", "Challengers in shadow"),
      table(
        [
          "Challenger",
          "Category",
          "Sessions",
          "vs live rules",
          "Holm p",
          "Status",
        ],
        graded.length
          ? graded.map((t) => [
              el("span", t.title, undefined),
              title(t.category),
              t.sessions,
              t.mean_difference === null ? "—" : pct(t.mean_difference),
              p(t.p_holm),
              t.status,
            ])
          : [
              [
                "No challenger pick has expired yet",
                "—",
                0,
                "—",
                "—",
                "testing",
              ],
            ],
      ),
      el(
        "p",
        `${(report.challengers || []).length} challenger tests in all (${[...new Set((report.challengers || []).map((t) => t.title))].join(", ")}); a challenger is promoted only after ${20} sessions of later results and a Holm-corrected p below 0.05, and is marked failed after 40 sessions without one.`,
        "caption muted",
      ),
    );
    return box;
  }

  async function performance(ticket) {
    const data = await api("plays/performance");
    if (ticket !== generation) return;
    app.className = "plays-page";
    const hero = el("div", undefined, "explorer-heading"),
      titles = el("div");
    titles.append(
      el("div", "HOW THE PICKS HAVE DONE", "eyebrow"),
      el("h1", "Performance"),
      el(
        "p",
        "Every recorded play graded on quoted closing marks and exact settlements only, by category and rule version, against random twins and SPY. Research, not financial advice.",
        "muted",
      ),
    );
    hero.append(titles);
    app.append(hero, views("performance"));
    if (!data.categories?.length) {
      app.append(panel("Nothing graded yet", data.message || ""));
      return;
    }
    app.append(
      el(
        "p",
        `${number(data.plays)} plays over ${data.sessions} sessions · ${number(data.resolved)} settled · current rules ${data.method} · built ${new Date(data.generated_at).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })} ET. ${data.note}`,
        "muted caption",
      ),
    );
    const overall = el("section", undefined, "pf-section");
    overall.append(
      el("h2", "By holding period"),
      table(
        ["Held", "Plays", "Median", "Random twins"],
        data.overall.map((h) => [
          h.label,
          cell(h.plays),
          h.plays ? pct(h.plays.median) : "—",
          cell(h.twins),
        ]),
      ),
    );
    app.append(overall, cumulativeChart(data.cumulative, data.categories));
    const cats = el("section", undefined, "pf-section");
    cats.append(
      el("h2", "By category"),
      el(
        "p",
        "Average return on the money at risk (share of plays in profit, number graded). Each rule version is graded separately; earlier versions stay visible below the current one.",
        "muted",
      ),
    );
    const rows = [];
    for (const c of data.categories)
      for (const v of c.versions) {
        const name = el("span", undefined, "pf-name");
        name.append(
          el("strong", `${c.emoji} ${c.title}`),
          el(
            "small",
            v.current ? "current rules" : `earlier rules ${v.method}`,
            "muted",
          ),
        );
        if (c.label && v.current)
          name.append(el("small", c.label.text, `pf-label ${c.label.state}`));
        const at = (label) =>
          cell(v.horizons.find((h) => h.label === label)?.plays);
        rows.push([
          name,
          `${v.plays} (${v.open} open)`,
          at("1 day"),
          at("1 week"),
          at("1 month"),
          at("At expiry"),
          cell(v.horizons.find((h) => h.label === "At expiry")?.twins),
        ]);
      }
    cats.append(
      table(
        [
          "Category",
          "Plays",
          "1 day",
          "1 week",
          "1 month",
          "At expiry",
          "Twins at expiry",
        ],
        rows,
      ),
    );
    app.append(cats, calibrationChart(data.calibration));
    if (data.learning)
      app.append(learningSection(data.learning, data.titles || {}));
    for (const [title, key, first] of [
      ["By days to expiry when picked", "dte", "Days to expiry"],
      ["By price per share when picked", "price", "Entry price"],
    ]) {
      const section = el("section", undefined, "pf-section");
      section.append(
        el("h2", title),
        table(
          [first, "Plays", "1 week", "At expiry", "Twins at expiry"],
          data[key].map((b) => [
            b.label,
            b.plays,
            cell(b.week),
            cell(b.expiry),
            cell(b.twins_expiry),
          ]),
        ),
      );
      app.append(section);
    }
  }

  return { views, history, detail, performance, sparkline };
})();
