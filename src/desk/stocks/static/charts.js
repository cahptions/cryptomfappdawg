"use strict";
// Shared, offline chart rendering. Observations remain observations; missing values are never interpolated.
const KestrelCharts = (() => {
  const ns = "http://www.w3.org/2000/svg";
  let serial = 0;
  const active = new Set();
  let cleanup;
  const node = (tag, attrs = {}, text) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === "style")
        for (const [property, value] of Object.entries(v))
          n.style.setProperty(property, value);
      else n.setAttribute(k, String(v));
    }
    if (text != null) n.textContent = text;
    return n;
  };
  const finite = Number.isFinite;
  const usd = (v) =>
    finite(v)
      ? new Intl.NumberFormat("en-US", {
          style: "currency",
          currency: "USD",
          maximumFractionDigits: 2,
        }).format(v)
      : "—";
  const compact = (v) =>
    finite(v)
      ? new Intl.NumberFormat("en-US", {
          notation: "compact",
          maximumFractionDigits: 1,
        }).format(v)
      : "—";
  const signed = (v) => (finite(v) ? `${v > 0 ? "+" : ""}${usd(v)}` : "—");
  const date = (value, full = false) => {
    if (value == null) return "—";
    const d = new Date(
      typeof value === "string" && value.length === 10
        ? value + "T12:00:00Z"
        : value,
    );
    if (!finite(d.getTime())) return String(value);
    return (
      d.toLocaleString("en-US", {
        timeZone: "America/New_York",
        month: "short",
        day: "numeric",
        ...(full
          ? { year: "numeric", hour: "numeric", minute: "2-digit" }
          : {}),
      }) + (full ? " ET" : "")
    );
  };
  const nearest = (rows, x, get = (p) => p.x) => {
    let lo = 0,
      hi = rows.length - 1;
    while (lo < hi) {
      const mid = Math.floor((lo + hi) / 2);
      if (get(rows[mid]) < x) lo = mid + 1;
      else hi = mid;
    }
    return lo > 0 &&
      Math.abs(get(rows[lo - 1]) - x) < Math.abs(get(rows[lo]) - x)
      ? lo - 1
      : lo;
  };
  const ticks = (low, high, count = 4) => {
    const raw = (high - low || Math.abs(high) * 0.02 || 1) / count,
      power = 10 ** Math.floor(Math.log10(raw));
    const step = [1, 2, 2.5, 5, 10].map((v) => v * power).find((v) => v >= raw);
    const out = [];
    for (
      let v = Math.ceil(low / step) * step;
      v <= high + step * 0.001;
      v += step
    )
      out.push(Math.abs(v) < step / 1000 ? 0 : v);
    return out;
  };
  function spark(
    values,
    { color = "var(--up)", label = "Recorded price history" } = {},
  ) {
    const rows = values.map((v) => (typeof v === "number" ? { value: v } : v)),
      valid = rows.filter((p) => finite(p.value));
    const svg = node("svg", {
      viewBox: "0 0 120 34",
      class: "kc-spark",
      role: "img",
      "aria-label": label,
    });
    if (!valid.length) return svg;
    const low = Math.min(...valid.map((p) => p.value)),
      span = Math.max(...valid.map((p) => p.value)) - low || 1;
    const x = (i) => (i / Math.max(1, rows.length - 1)) * 118 + 1,
      y = (v) => 31 - ((v - low) / span) * 28;
    const paths = { solid: "", estimated: "" };
    for (let i = 1; i < rows.length; i++) {
      const a = rows[i - 1],
        b = rows[i];
      if (!finite(a.value) || !finite(b.value) || b.gap_before) continue;
      paths[a.estimated || b.estimated ? "estimated" : "solid"] +=
        `M${x(i - 1)},${y(a.value)}L${x(i)},${y(b.value)}`;
    }
    for (const [kind, path] of Object.entries(paths))
      if (path)
        svg.append(
          node("path", {
            d: path,
            fill: "none",
            stroke: color,
            style: { stroke: color, fill: "none" },
            "stroke-width": 1.6,
            "stroke-dasharray": kind === "estimated" ? "3 3" : "",
            "vector-effect": "non-scaling-stroke",
          }),
        );
    if (valid.length === 1)
      svg.append(node("circle", { cx: 60, cy: 17, r: 2.5, fill: color }));
    return svg;
  }
  function create(initial = {}) {
    let config = initial,
      width = 0,
      selected = null,
      zoom = null,
      pointer = null,
      connected = false,
      disposed = false;
    const root = el("div", undefined, "kc-chart"),
      head = el("div", undefined, "kc-head"),
      heading = el("div"),
      label = el("div", "", "kc-label"),
      value = el("strong", "—", "kc-value"),
      change = el("div", "", "kc-change"),
      stamp = el("div", "", "kc-stamp"),
      actions = el("div", undefined, "kc-actions"),
      plot = el("div", undefined, "kc-plot"),
      readout = el("p", "", "kc-readout caption"),
      legend = el("div", undefined, "kc-legend"),
      tip = el("div", undefined, "kc-tooltip"),
      coverage = el("p", "", "kc-coverage caption muted");
    tip.setAttribute("role", "tooltip");
    tip.className += ` ${config.tooltipClass || ""}`;
    if (config.tooltipTestid) tip.dataset.testid = config.tooltipTestid;
    tip.hidden = true;
    heading.append(label, value, change, stamp);
    head.append(heading, actions);
    root.append(head, plot, readout, legend, coverage);
    const hidden = new Set();
    const zoomButton = (text, name, fn) => {
      const b = el("button", text);
      b.type = "button";
      b.setAttribute("aria-label", name);
      b.onclick = fn;
      actions.append(b);
      return b;
    };
    zoomButton("−", "Zoom out", () => adjustZoom(2));
    zoomButton("+", "Zoom in", () => adjustZoom(0.5));
    zoomButton("↺", "Reset chart view", () => {
      zoom = null;
      selected = null;
      render();
    });
    const values = () => (config.points || []).filter((p) => finite(p.x));
    function adjustZoom(factor) {
      const all = values();
      if (all.length < 3) return;
      const start = zoom?.[0] ?? 0,
        end = zoom?.[1] ?? all.length - 1,
        span = Math.max(
          2,
          Math.min(all.length - 1, Math.round((end - start) * factor)),
        );
      const center = selected ?? end,
        from = Math.max(
          0,
          Math.min(all.length - 1 - span, center - Math.round(span * 0.7)),
        );
      zoom = span >= all.length - 1 ? null : [from, from + span];
      render();
    }
    function render() {
      if (disposed) return;
      const measured =
        root.clientWidth || Math.max(280, Math.min(innerWidth - 48, 1000));
      width = measured;
      root.className = `kc-chart ${config.className || ""}`;
      root.dataset.chart = config.id || "chart";
      label.textContent = config.label || "";
      heading.hidden = config.headline === false;
      actions.hidden =
        config.zoom === false || (config.points?.length || 0) < 6;
      const all = values(),
        rows = zoom ? all.slice(zoom[0], zoom[1] + 1) : all;
      const series = config.series || [
        { id: "value", label: config.label || "Value", value: (p) => p.value },
      ];
      const shown = series.filter((s) => !hidden.has(s.id)),
        main = series[0],
        get = (s) => s.value || ((p) => p[s.id]);
      plot.replaceChildren();
      legend.replaceChildren();
      tip.hidden = true;
      root.style.setProperty(
        "--kc-height",
        `${config.height || (innerWidth < 600 ? 280 : 330)}px`,
      );
      coverage.textContent = [
        config.coverage,
        zoom ? "Zoomed view · Reset chart view restores the full range" : "",
      ]
        .filter(Boolean)
        .join(" · ");
      if (
        !rows.length ||
        !rows.some((p) => shown.some((s) => finite(get(s)(p))))
      ) {
        const empty = el("div", undefined, "kc-empty");
        if (config.emptyTitle) empty.append(el("h3", config.emptyTitle));
        empty.append(
          el(
            "p",
            config.empty || "No recorded observations in this range.",
            "muted",
          ),
        );
        if (config.emptyAction) {
          const action = el("a", config.emptyAction.label);
          action.href = config.emptyAction.href;
          empty.append(action);
        }
        plot.append(empty);
        value.textContent = "—";
        change.textContent = "";
        readout.textContent = "";
        stamp.textContent = "";
        return;
      }
      const height = config.height || (innerWidth < 600 ? 280 : 330),
        left = 8,
        right = config.axes === false ? 10 : 70,
        top = 18,
        bottom = config.volume ? height - 68 : height - 32,
        w = Math.max(1, width - left - right),
        h = bottom - top;
      const minX = config.xDomain?.[0] ?? rows[0].x,
        maxX = config.xDomain?.[1] ?? rows.at(-1).x,
        spanX = maxX - minX || 1;
      const position = new Map(rows.map((p, i) => [p, i]));
      const x = (p) =>
        rows.length === 1
          ? left + w / 2
          : left +
            (config.xMode === "time" || config.xKind === "number"
              ? (p.x - minX) / spanX
              : position.get(p) / Math.max(1, rows.length - 1)) *
              w;
      const ys = rows
        .flatMap((p) =>
          config.mode === "candles"
            ? [p.low, p.high]
            : shown.map((s) => get(s)(p)),
        )
        .filter(finite);
      if (finite(config.baseline)) ys.push(config.baseline);
      const minimum = Math.min(...ys),
        maximum = Math.max(...ys),
        padding = Math.max(
          (maximum - minimum) * 0.12,
          Math.abs(maximum) * 0.0005,
          0.01,
        );
      const lo = config.yDomain?.[0] ?? minimum - padding,
        hi = config.yDomain?.[1] ?? maximum + padding,
        y = (v) => bottom - ((v - lo) / (hi - lo)) * h;
      const format = config.format || usd,
        axisFormat =
          config.axisFormat ||
          ((v) => (Math.abs(v) >= 10000 ? `$${compact(v)}` : usd(v)));
      const chart = node("svg", {
        viewBox: `0 0 ${width} ${height}`,
        preserveAspectRatio: "none",
        role: "img",
        tabindex: 0,
        class: `kc-svg ${config.svgClass || ""}`,
        "aria-label":
          config.ariaLabel ||
          `${config.label || "Recorded values"}. Use arrow keys to inspect, plus or minus to zoom, and Escape to return to latest.`,
      });
      const clip = `kc-clip-${++serial}`,
        defs = node("defs"),
        cp = node("clipPath", { id: clip });
      cp.append(
        node("rect", { x: left, y: top, width: w, height: bottom - top + 2 }),
      );
      defs.append(cp);
      chart.append(defs);
      const drawing = node("g", { "clip-path": `url(#${clip})` });
      for (const band of config.bands || [])
        drawing.append(
          node("rect", {
            x: left,
            y: y(band.to),
            width: w,
            height: y(band.from) - y(band.to),
            fill: band.color || "var(--text-3)",
            opacity: 0.12,
          }),
        );
      const color =
        main.color ||
        ((config.value ? config.value(rows.at(-1)) : get(main)(rows.at(-1))) -
          (finite(config.baseline) ? config.baseline : get(main)(rows[0])) >=
        0
          ? "var(--up)"
          : "var(--down)");
      root.style.setProperty("--kc-color", color);
      if (config.axes !== false)
        for (const v of ticks(lo, hi)) {
          chart.append(
            node("line", {
              x1: left,
              x2: width - right,
              y1: y(v),
              y2: y(v),
              class: "kc-grid",
            }),
            node(
              "text",
              {
                x: width - 2,
                y: y(v) + 4,
                "text-anchor": "end",
                class: "kc-axis",
              },
              axisFormat(v),
            ),
          );
        }
      if (finite(config.baseline))
        chart.append(
          node("line", {
            x1: left,
            x2: width - right,
            y1: y(config.baseline),
            y2: y(config.baseline),
            class: "kc-baseline",
          }),
        );
      for (const ref of config.references || [])
        if (ref.x >= minX && ref.x <= maxX) {
          const px =
            config.xKind === "number" || config.xMode === "time"
              ? left + ((ref.x - minX) / spanX) * w
              : x(rows[nearest(rows, ref.x)]);
          drawing.append(
            node("line", {
              x1: px,
              x2: px,
              y1: top,
              y2: bottom,
              class: "kc-reference",
            }),
          );
        }
      const referenceLabels = [];
      for (const ref of (config.references || [])
        .filter((r) => r.label && r.x >= minX && r.x <= maxX)
        .sort((a, b) => a.x - b.x)) {
        const px = left + ((ref.x - minX) / spanX) * w,
          prior = referenceLabels.at(-1);
        if (prior && px - prior.x < 85) {
          prior.labels.add(ref.label);
          prior.x = (prior.x + px) / 2;
        } else referenceLabels.push({ x: px, labels: new Set([ref.label]) });
      }
      for (const item of referenceLabels)
        chart.append(
          node(
            "text",
            {
              x: Math.max(left + 30, Math.min(width - right - 30, item.x)),
              y: 12,
              "text-anchor": "middle",
              class: "kc-axis kc-reference-label",
            },
            [...item.labels].join(" · "),
          ),
        );
      if (config.mode === "candles") {
        const candleW = Math.max(1, Math.min(10, (w / rows.length) * 0.62));
        for (const p of rows) {
          const positive = p.close >= p.open,
            cls = positive ? "positive" : "negative";
          drawing.append(
            node("line", {
              x1: x(p),
              x2: x(p),
              y1: y(p.high),
              y2: y(p.low),
              class: `kc-wick ${cls} candle-wick ${cls}`,
            }),
            node("rect", {
              x: x(p) - candleW / 2,
              y: Math.min(y(p.open), y(p.close)),
              width: candleW,
              height: Math.max(1, Math.abs(y(p.open) - y(p.close))),
              class: `kc-candle ${cls} candle-body ${cls}`,
            }),
          );
        }
      } else
        for (const s of shown) {
          let path = "",
            gap = true;
          const val = get(s);
          rows.forEach((p) => {
            const v = val(p);
            if (!finite(v)) {
              gap = true;
              return;
            }
            path += `${gap || p.gap_before ? "M" : "L"}${x(p).toFixed(2)},${y(v).toFixed(2)}`;
            gap = false;
          });
          const appendLine = (stroke, clipId) =>
            drawing.append(
              node("path", {
                d: s.scatter ? "" : path,
                fill: "none",
                stroke,
                style: { stroke, fill: "none" },
                "stroke-width": s.width || 2,
                "stroke-dasharray": s.dashed ? "5 5" : "",
                class: `kc-line ${s.className || ""}`,
                ...(clipId ? { "clip-path": `url(#${clipId})` } : {}),
                "vector-effect": "non-scaling-stroke",
              }),
            );
          if (s.signed) {
            for (const [id, from, to, stroke] of [
              [
                "profit",
                top,
                Math.max(top, Math.min(bottom, y(0))),
                "var(--up)",
              ],
              [
                "loss",
                Math.max(top, Math.min(bottom, y(0))),
                bottom,
                "var(--down)",
              ],
            ]) {
              const c = node("clipPath", { id: `${clip}-${id}` });
              c.append(
                node("rect", {
                  x: left,
                  y: from,
                  width: w,
                  height: Math.max(0, to - from),
                }),
              );
              defs.append(c);
              appendLine(stroke, `${clip}-${id}`);
            }
          } else appendLine(s.color || color);
          if (rows.length === 1 || config.dots || s.scatter)
            for (const p of rows)
              if (finite(val(p)))
                drawing.append(
                  node("circle", {
                    cx: x(p),
                    cy: y(val(p)),
                    r: 3,
                    fill: s.color || color,
                    class: "kc-observation",
                  }),
                );
        }
      chart.append(drawing);
      if (config.volume) {
        const vmax = Math.max(1, ...rows.map((p) => p.volume || 0)),
          vh = 32;
        for (const p of rows)
          chart.append(
            node("rect", {
              x: x(p) - Math.max(1, (w / rows.length) * 0.25),
              y: height - 28 - ((p.volume || 0) / vmax) * vh,
              width: Math.max(1, (w / rows.length) * 0.5),
              height: ((p.volume || 0) / vmax) * vh,
              class: `kc-volume volume-bar ${p.close >= p.open ? "positive" : "negative"}`,
            }),
          );
      }
      const markers = (config.markers || []).filter(
        (m) => m.x >= minX && m.x <= maxX,
      );
      for (const m of markers.slice(0, 160)) {
        const i = nearest(rows, m.x),
          p = rows[i],
          v = get(main)(p);
        if (!finite(v)) continue;
        const marker = node(
          m.kind === "funding" ? "rect" : "circle",
          m.kind === "funding"
            ? { x: x(p) - 4, y: y(v) - 4, width: 8, height: 8 }
            : { cx: x(p), cy: y(v), r: 4 },
        );
        marker.setAttribute(
          "class",
          `kc-marker ${m.kind || "trade"} ${config.markerClass || ""} ${m.className || ""}`,
        );
        marker.append(node("title", {}, m.label || m.kind));
        if (config.onMarker) {
          marker.setAttribute("tabindex", "0");
          marker.setAttribute("role", "button");
          marker.setAttribute("aria-label", m.label || m.kind);
          marker.onkeydown = (e) => {
            if (["Enter", " "].includes(e.key)) {
              e.preventDefault();
              config.onMarker(m);
            }
          };
        }
        marker.addEventListener("click", (e) => {
          e.stopPropagation();
          config.onMarker?.(m);
        });
        chart.append(marker);
        if (m.caption)
          chart.append(
            node(
              "text",
              {
                x: x(p),
                y: Math.max(top + 12, y(v) - 12),
                "text-anchor": x(p) > width / 2 ? "end" : "start",
                class: "kc-marker-label pl-peak-label",
              },
              m.caption,
            ),
          );
      }
      const cross = node("line", {
          x1: 0,
          x2: 0,
          y1: top,
          y2: bottom,
          class: "kc-crosshair",
          visibility: "hidden",
        }),
        dot = node("circle", {
          cx: 0,
          cy: 0,
          r: 4,
          class: "kc-cursor",
          visibility: "hidden",
        });
      chart.append(cross, dot);
      const fmtX = config.xFormat || ((p) => date(p.at || p.x)),
        fmtTime = config.timeFormat || ((p) => date(p.at || p.x, true));
      if (config.axes !== false) {
        const count = Math.min(3, rows.length);
        for (let t = 0; t < count; t++) {
          const ratio = t / Math.max(1, count - 1),
            continuous = config.xMode === "time" || config.xKind === "number";
          const at = minX + ratio * spanX,
            p = rows[Math.floor(ratio * (rows.length - 1))];
          const text = continuous
            ? config.xTickFormat
              ? config.xTickFormat(at)
              : config.xKind === "number"
                ? usd(at)
                : date(at)
            : fmtX(p);
          chart.append(
            node(
              "text",
              {
                x: continuous ? left + ratio * w : x(p),
                y: height - 6,
                "text-anchor":
                  t === 0 ? "start" : t === count - 1 ? "end" : "middle",
                class: "kc-axis kc-time-axis",
              },
              text,
            ),
          );
        }
      }
      plot.append(chart, tip);
      const paint = (index, showTip = false, event = null) => {
        const p = rows[index];
        if (!p) return;
        const v = config.value ? config.value(p) : get(main)(p);
        if (!p || !finite(v)) return;
        selected = showTip ? all.indexOf(p) : null;
        value.textContent = format(v);
        value.style.color = config.valueColor ? config.valueColor(p) : color;
        const start = finite(config.baseline)
            ? config.baseline
            : get(main)(rows[0]),
          delta = v - start;
        change.textContent = config.changeText
          ? config.changeText(p, rows[0])
          : `${signed(delta)}${start ? ` (${delta >= 0 ? "+" : ""}${((delta / Math.abs(start)) * 100).toFixed(2)}%)` : ""} ${config.deltaLabel || "in this range"}`;
        stamp.textContent = fmtTime(p);
        const rowValues = config.tooltipRows
          ? config.tooltipRows(p, index)
          : shown.map((s) => [s.label, (s.format || format)(get(s)(p))]);
        readout.textContent = [
          fmtTime(p),
          ...(config.readoutRows ? config.readoutRows(p) : rowValues).map(
            ([a, b]) => `${a} ${b}`,
          ),
        ].join(" · ");
        tip.replaceChildren(
          el("strong", fmtTime(p)),
          ...rowValues.map(([a, b]) => {
            const r = el("div", undefined, "kc-tip-row");
            r.append(el("span", a), el("strong", b));
            return r;
          }),
        );
        if (config.tooltipFooter)
          tip.append(el("small", config.tooltipFooter, "kc-tip-event"));
        const events = markers.filter((m) => nearest(rows, m.x) === index);
        for (const m of events.slice(0, 4))
          tip.append(el("small", m.label, "kc-tip-event"));
        cross.setAttribute("x1", x(p));
        cross.setAttribute("x2", x(p));
        cross.setAttribute("visibility", showTip ? "visible" : "hidden");
        dot.setAttribute("cx", x(p));
        dot.setAttribute("cy", y(v));
        dot.setAttribute("visibility", showTip ? "visible" : "hidden");
        tip.hidden = !showTip;
        if (showTip) {
          const bounds = plot.getBoundingClientRect(),
            px = event ? event.clientX - bounds.left : x(p),
            py = event ? event.clientY - bounds.top : y(v),
            tw = tip.offsetWidth,
            th = tip.offsetHeight;
          tip.style.left = `${Math.max(0, Math.min(bounds.width - tw, px + tw + 18 > bounds.width ? px - tw - 12 : px + 14))}px`;
          tip.style.top = `${Math.max(0, Math.min(height - th, py > height * 0.5 ? py - th - 12 : py + 16))}px`;
        }
        config.onInspect?.(showTip ? p : null, all.indexOf(p), rows[0]);
      };
      const reset = () => {
        pointer = null;
        selected = null;
        paint(rows.length - 1, false);
      };
      const inspectEvent = (e) => {
        const b = chart.getBoundingClientRect(),
          r = Math.max(0, Math.min(1, (e.clientX - b.left - left) / w));
        const i =
          config.xMode === "time" || config.xKind === "number"
            ? nearest(rows, minX + r * spanX)
            : Math.round(r * (rows.length - 1));
        pointer = e;
        paint(i, true, e);
      };
      chart.onpointermove = inspectEvent;
      chart.onpointerdown = (e) => {
        chart.setPointerCapture(e.pointerId);
        inspectEvent(e);
      };
      chart.onpointerup = (e) => {
        if (chart.hasPointerCapture(e.pointerId))
          chart.releasePointerCapture(e.pointerId);
        if (config.onSelect && selected != null) config.onSelect(all[selected]);
        if (e.pointerType === "touch" && !config.pinTouch) reset();
      };
      chart.onpointerleave = reset;
      chart.onpointercancel = reset;
      chart.onblur = reset;
      chart.onkeydown = (e) => {
        if (
          [
            "ArrowLeft",
            "ArrowRight",
            "Home",
            "End",
            "Escape",
            "+",
            "=",
            "-",
          ].includes(e.key)
        )
          e.preventDefault();
        else return;
        if (e.key === "Escape") return reset();
        if (["+", "=", "-"].includes(e.key))
          return adjustZoom(e.key === "-" ? 2 : 0.5);
        const i =
          selected == null
            ? rows.length - 1
            : Math.max(0, rows.indexOf(all[selected]));
        paint(
          e.key === "Home"
            ? 0
            : e.key === "End"
              ? rows.length - 1
              : Math.max(
                  0,
                  Math.min(
                    rows.length - 1,
                    i +
                      (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 10 : 1),
                  ),
                ),
          true,
        );
      };
      for (const s of series) {
        const key = el("button", s.label, "kc-key");
        key.type = "button";
        key.style.setProperty("--key-color", s.color || color);
        key.setAttribute("aria-pressed", String(!hidden.has(s.id)));
        key.disabled = s === series[0] || s.toggle === false;
        key.onclick = () => {
          if (hidden.has(s.id)) hidden.delete(s.id);
          else if (series.length - hidden.size > 1) hidden.add(s.id);
          render();
        };
        legend.append(key);
      }
      legend.hidden = config.legend === false;
      if (selected != null && all[selected] && rows.includes(all[selected]))
        paint(rows.indexOf(all[selected]), true, pointer);
      else paint(rows.length - 1, false);
    }
    const observer = new ResizeObserver(() => {
      if (Math.abs(root.clientWidth - width) > 1 && root.clientWidth) render();
    });
    observer.observe(root);
    root.update = (next) => {
      const prior = values(),
        selectedX = selected != null ? prior[selected]?.x : null;
      const zoomX = zoom ? [prior[zoom[0]]?.x, prior[zoom[1]]?.x] : null;
      const changed =
        next.viewKey !== undefined && next.viewKey !== config.viewKey;
      config = { ...config, ...next };
      const after = values();
      selected =
        changed || selectedX == null
          ? null
          : after.findIndex((p) => p.x === selectedX);
      if (selected < 0) selected = null;
      zoom =
        changed || !zoomX || !after.length
          ? null
          : [nearest(after, zoomX[0]), nearest(after, zoomX[1])];
      if (zoom && zoom[1] - zoom[0] < 2) zoom = null;
      render();
    };
    root.inspect = (i) => {
      selected = i;
      render();
    };
    root.dispose = () => {
      disposed = true;
      root.disposed = true;
      observer.disconnect();
      active.delete(root);
    };
    root._connected = () => {
      if (root.isConnected) connected = true;
      else if (connected) root.dispose();
    };
    active.add(root);
    if (!cleanup) {
      cleanup = new MutationObserver(() => {
        for (const c of active) c._connected();
      });
      cleanup.observe(document.body, { childList: true, subtree: true });
    }
    render();
    return root;
  }
  function ranges(
    labels,
    current,
    onChange,
    { label = "Time range", className = "" } = {},
  ) {
    const bar = el("div", undefined, `kc-ranges ${className}`);
    bar.setAttribute("role", "group");
    bar.setAttribute("aria-label", label);
    for (const value of labels) {
      const b = el("button", typeof value === "string" ? value : value.label);
      b.type = "button";
      b.value = typeof value === "string" ? value : value.id;
      b.setAttribute("aria-pressed", String(b.value === current));
      b.onclick = () => {
        for (const c of bar.children)
          c.setAttribute("aria-pressed", String(c === b));
        onChange(b.value);
      };
      bar.append(b);
    }
    return bar;
  }
  return { create, spark, ranges, usd, signed, compact, date, nearest };
})();
