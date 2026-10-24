"use strict";
const KestrelPaperChart = (() => {
  const svg = (tag, attrs) => {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs))
      node.setAttribute(key, value);
    return node;
  };
  function render(
    account,
    { record = "live", benchmark = [], trades = [] } = {},
  ) {
    const box = el("section", undefined, "paper-equity"),
      curves = account.curve || [];
    const controls = el("div", undefined, "research-tabs"),
      plot = el("div", undefined, "paper-chart-plot"),
      readout = el("p", "", "caption paper-chart-readout"),
      coverage = el("p", "", "muted caption");
    box.append(
      el("h3", "Simulated account value"),
      coverage,
      controls,
      plot,
      readout,
    );
    const windows = [],
      seen = new Set();
    for (const [label, count] of [
      ["1M", 21],
      ["3M", 63],
      ["6M", 126],
      ["1Y", 252],
      ["5Y", 1260],
      ["Max", curves.length],
    ]) {
      if (count >= curves.length && label !== "Max") continue;
      const actual = Math.min(curves.length, count);
      if (seen.has(actual)) continue;
      seen.add(actual);
      windows.push([label, count]);
    }
    if (windows.length === 1)
      windows[0][0] = `Available (${curves.length} sessions)`;
    function draw(count) {
      const rows = curves.slice(-count),
        reference = new Map(benchmark.map((r) => [r.day, r.equity]));
      plot.replaceChildren();
      coverage.textContent = rows.length
        ? `${rows.length} actual daily observations · ${rows[0].day}–${rows.at(-1).day}. ${record === "live" ? "Recorded forward PAPER; updates after a processed daily close." : "Chronological historical simulation; review stops at the selected session."}`
        : "No processed sessions yet. No prices or returns are invented.";
      if (!rows.length) {
        plot.append(
          el("p", "Waiting for the first processed session.", "muted"),
        );
        return;
      }
      if (curves.length < 21)
        coverage.textContent +=
          " Longer time ranges appear as real observations accumulate; the dots are the stored marks.";
      const benchStart = benchmark[0]?.equity,
        ownStart = account.deposits || curves[0].equity;
      const bench = rows.map((r) => ({
        day: r.day,
        equity:
          reference.has(r.day) && benchStart > 0
            ? (reference.get(r.day) / benchStart) * ownStart
            : null,
      }));
      const values = [
        ...rows.map((r) => r.equity),
        ...bench.filter((r) => r.equity != null).map((r) => r.equity),
      ];
      const low = Math.min(...values),
        high = Math.max(...values),
        margin = Math.max((high - low) * 0.12, high * 0.002, 1),
        lo = low - margin,
        hi = high + margin,
        span = hi - lo;
      const width = window.innerWidth < 600 ? 500 : 1000,
        left = width < 600 ? 110 : 90,
        plotWidth = width - left - 30;
      const x = (i) => left + (i / Math.max(1, rows.length - 1)) * plotWidth,
        y = (v) => 225 - ((v - lo) / span) * 195;
      const chart = svg("svg", {
        viewBox: `0 0 ${width} 270`,
        preserveAspectRatio: "none",
        role: "img",
        tabindex: "0",
        "aria-label": `${rows.length} actual daily equity observations. Arrow keys inspect account values and changes.`,
      });
      for (let i = 0; i < 4; i++) {
        const v = lo + (span * i) / 3,
          lineY = y(v);
        chart.append(
          svg("line", {
            x1: left,
            x2: width - 30,
            y1: lineY,
            y2: lineY,
            class: "chart-guide",
          }),
        );
        const label = svg("text", {
          x: left - 10,
          y: lineY + 4,
          "text-anchor": "end",
          class: "paper-value-axis",
        });
        label.textContent = money(v);
        chart.append(label);
      }
      const path = (points) =>
        points
          .map((r, i) =>
            r.equity == null
              ? ""
              : `${!i || points[i - 1].equity == null ? "M" : "L"}${x(i)},${y(r.equity)}`,
          )
          .join("");
      if (bench.some((r) => r.equity != null))
        chart.append(
          svg("path", { d: path(bench), class: "paper-benchmark-line" }),
        );
      chart.append(svg("path", { d: path(rows), class: "paper-equity-line" }));
      if (rows.length <= 40)
        for (let i = 0; i < rows.length; i++)
          chart.append(
            svg("circle", {
              cx: x(i),
              cy: y(rows[i].equity),
              r: 3.5,
              class: "paper-equity-point",
            }),
          );
      const indexByDay = new Map(rows.map((r, i) => [r.day, i]));
      for (const t of trades.slice(0, 100))
        for (const [day, kind] of [
          [t.entry_day, "entry"],
          [t.exit_day, "exit"],
        ]) {
          const i = indexByDay.get(day);
          if (i == null) continue;
          const marker = svg("circle", {
            cx: x(i),
            cy: y(rows[i].equity),
            r: 5.5,
            class: `paper-trade-marker ${kind}`,
          });
          const title = svg("title", {});
          title.textContent = `${kind}: ${t.symbol} · ${day}`;
          marker.append(title);
          chart.append(marker);
        }
      const cursor = svg("circle", {
          cx: x(rows.length - 1),
          cy: y(rows.at(-1).equity),
          r: 5,
          class: "paper-equity-point",
        }),
        tooltip = el("div", undefined, "paper-chart-tooltip");
      tooltip.setAttribute("role", "tooltip");
      tooltip.hidden = true;
      chart.append(cursor);
      let index = rows.length - 1;
      function inspect(i, showTip = true) {
        index = Math.max(0, Math.min(rows.length - 1, i));
        const row = rows[index],
          previous = curves[curves.findIndex((r) => r.day === row.day) - 1];
        const change = previous ? row.equity - previous.equity : null;
        cursor.setAttribute("cx", x(index));
        cursor.setAttribute("cy", y(row.equity));
        readout.textContent = `${row.day} · ${money(row.equity)} · ${change == null ? "First stored observation" : `${change >= 0 ? "+" : ""}${money(change)} vs previous stored session`} · ${record === "live" ? "Recorded forward PAPER" : "Historical reconstruction"}`;
        tooltip.replaceChildren(
          el("strong", row.day),
          el("p", `Equity ${money(row.equity)}`),
          el(
            "p",
            change == null
              ? "First observation"
              : `Daily change ${change >= 0 ? "+" : ""}${money(change)}`,
          ),
        );
        if (reference.has(row.day))
          tooltip.append(
            el("p", `SPY reference ${money(bench[index].equity)}`),
          );
        tooltip.hidden = !showTip;
        const w = plot.clientWidth;
        tooltip.style.left = `${Math.max(0, Math.min(w - 220, (x(index) / width) * w + 12))}px`;
        tooltip.style.top = "12px";
      }
      chart.onpointermove = (e) => {
        const b = chart.getBoundingClientRect();
        inspect(
          Math.round(
            ((((e.clientX - b.left) / b.width) * width - left) / plotWidth) *
              (rows.length - 1),
          ),
        );
      };
      chart.onpointerleave = () => inspect(rows.length - 1, false);
      chart.onkeydown = (e) => {
        if (
          !["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(e.key)
        )
          return;
        e.preventDefault();
        inspect(
          e.key === "Home"
            ? 0
            : ["End", "Escape"].includes(e.key)
              ? rows.length - 1
              : index + (e.key === "ArrowLeft" ? -1 : 1),
          e.key !== "Escape",
        );
      };
      const axis = el("div", undefined, "chart-axis");
      axis.append(el("span", rows[0].day), el("span", rows.at(-1).day));
      plot.append(chart, tooltip, axis);
      inspect(index, false);
      for (const b of controls.children)
        b.setAttribute("aria-selected", String(+b.dataset.count === count));
    }
    for (const [label, count] of windows) {
      const b = el("button", label);
      b.dataset.count = count;
      b.onclick = () => draw(count);
      controls.append(b);
    }
    draw(curves.length);
    if (benchmark.length)
      box.append(
        el(
          "p",
          "Blue: strategy · Dashed: SPY buy-and-hold, normalized to the same starting capital. Entry/exit markers represent trades loaded in this view.",
          "caption",
        ),
      );
    return box;
  }
  return { render };
})();
