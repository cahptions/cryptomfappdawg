"use strict";
const KestrelCapital = (() => {
  const labels = {
    risk: "Risk-weighted",
    equal: "Equal-weight",
    market: "SPY",
    cash: "Cash",
  };
  let source = "forward",
    selected = new Set(),
    settings = { window: 126, reserve: 20, cap: 60, entry_bps: 10 };
  const description =
    "Compare recorded strategies on the same dates. Allocation weights are fitted on earlier sessions and evaluated on later ones.";
  const signedPct = (x) =>
    x == null ? "—" : `${x > 0 ? "+" : ""}${percent(x)}`;
  function paint(result, output, openReplay) {
    output.replaceChildren();
    if (result.status !== "ready") {
      output.append(
        el("h2", "Matched return coverage"),
        el(
          "p",
          `${result.coverage.matched} of ${result.coverage.needed} matched daily returns required. ${result.coverage.considered} sessions considered. ${result.coverage.excluded} excluded for transfers or recovery processing.`,
        ),
        table(
          ["Strategy", "Returns available"],
          [
            ...result.accounts
              .filter((a) => a.id in result.coverage.per_strategy)
              .map((a) => [a.title, result.coverage.per_strategy[a.id]]),
            ["SPY comparator", result.coverage.benchmark],
          ],
        ),
      );
      if (result.source === "forward") {
        const replay = el("button", "Use historical replay", "primary");
        replay.onclick = openReplay;
        output.append(replay);
      }
      return;
    }
    const a = result.metrics.risk,
      b = result.metrics.equal;
    const header = el("div", undefined, "capital-summary");
    for (const [title, value, note] of [
      [
        "Held-out return",
        signedPct(a.return),
        `${signedPct(b.return)} equal-weight`,
      ],
      [
        "Maximum drawdown",
        percent(a.drawdown),
        `${percent(b.drawdown)} equal-weight`,
      ],
      [
        "Annualized volatility",
        percent(a.volatility),
        `${percent(b.volatility)} equal-weight`,
      ],
      ["Beta to SPY", a.beta?.toFixed(2) ?? "—", "Held-out estimate"],
    ]) {
      const metric = el("div");
      metric.append(el("small", title), el("strong", value), el("span", note));
      header.append(metric);
    }
    const chart = KestrelCharts.create({
      id: "portfolio",
      headline: false,
      className: "capital-chart",
    });
    const series = Object.keys(labels).map((key, i) => ({
      id: key,
      label: labels[key],
      color: `var(--series-${i + 1})`,
      dashed: key === "cash",
      value: (p) => (p[key] - 1) * 100,
    }));
    chart.update({
      points: result.curve.map((p) => ({
        ...p,
        at: p.day,
        x: Date.parse(p.day),
      })),
      label: "Held-out portfolio return",
      series,
      legend: true,
      baseline: 0,
      xFormat: (p) => KestrelCharts.date(p.day),
      axisFormat: (v) => `${v.toFixed(1)}%`,
      timeFormat: (p) => p.day,
      tooltipRows: (p) =>
        Object.keys(labels).map((key) => [labels[key], signedPct(p[key] - 1)]),
    });
    const period = el(
      "p",
      `Fit ${result.fit.first} to ${result.fit.last} · ${result.fit.n} days. Held out ${result.test.first} to ${result.test.last} · ${result.test.n} days.`,
      "muted",
    );
    const titles = Object.fromEntries(
      result.accounts.map((a) => [a.id, a.title]),
    );
    const spread = result.risk_vs_equal_bps;
    const provenance = el("details", undefined, "capital-provenance");
    provenance.append(
      el("summary", "Inspect comparison inputs"),
      el("p", `SPY comparator · ${result.benchmark_basis}.`),
      el("p", `Allocation method · ${result.model}.`),
      el("p", "Input fingerprint (SHA-256)"),
      el("code", result.inputs_sha256),
    );
    output.append(
      el("h2", "Held-out comparison"),
      period,
      header,
      chart,
      el(
        "p",
        `Risk-weighted minus equal-weight averaged ${spread.mean.toFixed(1)} bp per matched session. Paired 90% block-bootstrap interval ${spread.low.toFixed(1)} to ${spread.high.toFixed(1)} bp.`,
        "muted",
      ),
      el("h3", "Allocation at the start of the holdout"),
      table(
        ["Strategy", "Capital weight", "Fitted variance share"],
        [
          ...result.allocations.map((row) => [
            titles[row.account],
            percent(row.weight, 1),
            percent(row.risk_share, 1),
          ]),
          ["Cash reserve", percent(result.controls.reserve), "—"],
        ],
      ),
      el(
        "p",
        "The blends simulate fractional allocations and let initial weights drift. Results compound only matched sessions, skipping unobserved moves. The paths use strategy account marks and a one-time entry cost. You can change strategies or settings after seeing the results, so use this as exploratory evidence.",
        "muted",
      ),
      provenance,
    );
  }

  async function show(ticket) {
    app.className = "capital-page";
    let serial = 0;
    async function renderView() {
      const token = ++serial,
        current = source;
      const catalog = await api(`portfolio?source=${current}`);
      if (ticket !== generation || token !== serial) return;
      const heading = el("div", undefined, "workspace-heading"),
        title = el("div");
      title.append(el("h1", "Portfolio lab"), el("p", description));
      const risk = link("Inspect open exposure", "#risk");
      risk.className = "workspace-action";
      heading.append(title, risk);
      const layout = el("div", undefined, "capital-layout");
      const form = el("form", undefined, "workspace-panel capital-controls");
      const output = el(
        "section",
        undefined,
        "workspace-panel capital-results",
      );
      const ranges = el("div", undefined, "capital-inputs");
      const choices = el("div", undefined, "capital-choices");
      const report = el("select");
      for (const [key, label] of [
        ["forward", "Forward PAPER"],
        ["replay", "Historical replay"],
      ])
        report.append(new Option(label, key));
      report.value = source;
      report.onchange = () => {
        source = report.value;
        void renderView();
      };
      const fld = (label, control) => {
        const node = el("label");
        node.append(el("span", label), control);
        return node;
      };
      form.append(
        el("h2", "Build an allocation"),
        fld("Evidence source", report),
      );
      for (const [key, label, min, max] of [
        ["window", "Latest sessions", 63, 252],
        ["reserve", "Cash reserve (%)", 0, 80],
        ["cap", "Maximum share of invested capital (%)", 1, 100],
        ["entry_bps", "Entry cost (bp)", 0, 100],
      ]) {
        const input = el(key === "window" ? "select" : "input");
        if (key === "window")
          for (const n of [63, 126, 252]) input.append(new Option(n, n));
        else
          Object.assign(input, {
            type: "number",
            min,
            max,
            step: 1,
            required: true,
          });
        input.value = settings[key];
        input.name = key;
        ranges.append(fld(label, input));
      }
      form.append(ranges);
      selected = new Set(
        catalog.accounts.map((a) => a.id).filter((id) => selected.has(id)),
      );
      if (selected.size < 2)
        for (const a of catalog.accounts
          .filter((a) => a.sessions > 0)
          .slice(0, 3))
          selected.add(a.id);
      for (const a of catalog.accounts) {
        const box = el("input");
        box.type = "checkbox";
        box.value = a.id;
        box.name = "account";
        box.checked = selected.has(a.id);
        const label = el("label");
        label.append(
          box,
          el("span", a.title),
          el(
            "small",
            `${a.sessions} sessions${a.mixed_specifications ? " · multiple rule versions" : ""}`,
          ),
        );
        choices.append(label);
      }
      const run = el("button", "Evaluate allocation", "primary");
      const error = el("p", undefined, "error");
      error.setAttribute("role", "status");
      run.disabled = catalog.accounts.length < 2;
      form.append(el("h3", "Strategies"), choices, run, error);
      output.append(
        el("h2", current === "replay" ? "Historical replay" : "Forward PAPER"),
        el(
          "p",
          catalog.accounts.length < 2
            ? current === "replay"
              ? "Run a historical backtest in PAPER to populate this source."
              : "PAPER strategy ledgers will appear here after the first processed session."
            : "Choose two to eight strategies and evaluate them against equal weight, SPY and cash.",
        ),
      );
      if (catalog.accounts.length < 2) {
        const refresh = el("button", "Refresh strategies");
        refresh.onclick = () => void renderView();
        const links = el("div", undefined, "evidence-actions");
        links.append(refresh, link("Open PAPER ↗", "#paper"));
        output.append(links);
      }
      layout.append(form, output);
      app.replaceChildren(heading, layout);
      form.onsubmit = async (e) => {
        e.preventDefault();
        const formData = new FormData(form);
        const accounts = formData.getAll("account");
        if (accounts.length < 2 || accounts.length > 8) {
          error.textContent = "Select two to eight strategies.";
          return;
        }
        selected = new Set(accounts);
        for (const key of Object.keys(settings))
          settings[key] = Number(formData.get(key));
        const query = new URLSearchParams({ ...settings, source: current });
        for (const id of accounts) query.append("accounts", id);
        run.disabled = true;
        error.textContent = "";
        try {
          const result = await api(`portfolio?${query}`);
          if (ticket === generation && token === serial)
            preserveScroll(() =>
              paint(result, output, () => {
                source = "replay";
                void renderView();
              }),
            );
        } catch (err) {
          error.textContent = err.message;
        } finally {
          run.disabled = false;
        }
      };
    }
    await renderView();
  }
  return { show };
})();
