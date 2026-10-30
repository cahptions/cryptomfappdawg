"use strict";
const KestrelPaperChart = (() => {
  const views = new Map();
  function render(
    account,
    { record = "live", benchmark = [], trades = [] } = {},
  ) {
    const box = el("section", undefined, "paper-equity"),
      curves = account.curve || [],
      tools = el("div", undefined, "kc-toolbar"),
      plot = el("div", undefined, "paper-chart-plot");
    const key = `${record}:${account.id || account.title || "account"}`;
    const state = views.get(key) || {
      full: true,
      count: curves.length,
      metric: "equity",
    };
    views.set(key, state);
    let count = state.full ? curves.length : state.count,
      metric = state.metric;
    const modes = el("select");
    modes.setAttribute("aria-label", "PAPER chart metric");
    modes.append(
      new Option("Account value", "equity"),
      new Option("Net trading P/L", "pnl"),
    );
    modes.onchange = () => {
      metric = modes.value;
      draw();
    };
    tools.append(modes);
    const windows = [],
      seen = new Set();
    for (const [label, n] of [
      ["1M", 21],
      ["3M", 63],
      ["6M", 126],
      ["1Y", 252],
      ["5Y", 1260],
      ["Max", curves.length],
    ]) {
      if (n >= curves.length && label !== "Max") continue;
      if (seen.has(Math.min(n, curves.length))) continue;
      seen.add(Math.min(n, curves.length));
      windows.push({ label, id: String(n) });
    }
    if (windows.length === 1)
      windows[0].label = `Available (${curves.length} sessions)`;
    const chart =
      state.chart && !state.chart.disposed
        ? state.chart
        : KestrelCharts.create({
            id: "paper",
            className: "paper-account-chart",
            tooltipClass: "paper-chart-tooltip",
          });
    state.chart = chart;
    modes.value = metric;
    function draw() {
      state.count = count;
      state.metric = metric;
      const reference = new Map(benchmark.map((r) => [r.day, r.equity])),
        ownStart = account.deposits || curves[0]?.equity || 0,
        benchStart = benchmark[0]?.equity;
      const rows = curves.slice(-count).map((r) => ({
        ...r,
        at: r.day,
        x: Date.parse(r.day + "T12:00:00Z"),
        value: r.equity - (metric === "pnl" ? ownStart : 0),
        benchmark:
          reference.has(r.day) && benchStart > 0
            ? (reference.get(r.day) / benchStart) * ownStart -
              (metric === "pnl" ? ownStart : 0)
            : null,
      }));
      const markers = trades.flatMap((t) =>
        [
          [t.entry_day, "entry"],
          [t.exit_day, "exit"],
        ]
          .filter(([d]) => d)
          .map(([d, kind]) => ({
            x: Date.parse(d + "T12:00:00Z"),
            kind,
            label: `${kind}: ${t.symbol} · ${d}`,
          })),
      );
      chart.update({
        points: rows,
        viewKey: `${metric}:${state.full ? "Max" : count}`,
        label:
          metric === "equity" ? "Simulated account value" : "Net trading P/L",
        baseline: metric === "pnl" ? 0 : ownStart,
        legend: benchmark.length > 0,
        dots: rows.length <= 40,
        markers,
        markerClass: "paper-trade-marker",
        series: [
          {
            id: "value",
            label: "Strategy",
            className: "paper-equity-line",
            value: (p) => p.value,
          },
          ...(benchmark.length
            ? [
                {
                  id: "benchmark",
                  label: "SPY · same starting capital",
                  color: "var(--text-3)",
                  dashed: true,
                  className: "paper-benchmark-line",
                  value: (p) => p.benchmark,
                },
              ]
            : []),
        ],
        tooltipRows: (p) => {
          const i = curves.findIndex((r) => r.day === p.day),
            previous = curves[i - 1];
          return [
            ["Equity", money(p.equity)],
            ["Net P/L", KestrelCharts.signed(p.equity - ownStart)],
            [
              "Daily change",
              previous
                ? `${KestrelCharts.signed(p.equity - previous.equity)} vs previous stored session`
                : "First stored observation",
            ],
            ...(p.benchmark != null
              ? [["SPY reference", money(p.benchmark)]]
              : []),
          ];
        },
        timeFormat: (p) => p.day,
        xFormat: (p) => KestrelCharts.date(p.day),
        empty:
          "Waiting for the first processed session. No prices or returns are invented.",
        coverage: rows.length
          ? `${rows.length} actual daily observations · ${rows[0].day}–${rows.at(-1).day}. ${record === "live" ? "Recorded forward PAPER; updates after a processed daily close." : "Chronological historical simulation; review stops at the selected session."}${curves.length < 21 ? " Longer time ranges appear as real observations accumulate; the dots are the stored marks." : ""}`
          : "No processed sessions yet. No prices or returns are invented.",
      });
      chart.querySelector(".kc-readout").classList.add("paper-chart-readout");
    }
    box.append(
      el("h3", "Simulated account performance"),
      tools,
      plot,
      KestrelCharts.ranges(windows, String(count), (v) => {
        count = +v;
        state.full = count === curves.length;
        draw();
      }),
    );
    plot.append(chart);
    if (trades.length)
      box.append(
        el(
          "p",
          "Entry and exit markers show the trades loaded in this view. Dashed SPY comparison uses the same starting capital.",
          "caption muted",
        ),
      );
    draw();
    return box;
  }
  return { render };
})();
