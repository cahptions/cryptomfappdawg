"use strict";
const KestrelReplay = (() => {
  function panel(report, account, ticket, titles, openTrade) {
    const host = el("section", undefined, "paper-replay"),
      controls = el("div", undefined, "paper-playback"),
      dayLabel = el("strong"),
      slider = el("input"),
      play = el("button", "Play from start", "primary"),
      first = el("button", "First session"),
      back = el("button", "Previous session"),
      next = el("button", "Next session"),
      last = el("button", "Latest session"),
      speed = el("select"),
      content = el("div"),
      error = el("p", "", "error"),
      viewControls = el("div", undefined, "research-tabs"),
      search = el("input");
    const days = report.days || account.curve.map((r) => r.day);
    let index = days.length - 1,
      playing = false,
      timer,
      request = 0,
      view = "open",
      offset = 0;
    const cache = new Map();
    const active = () => ticket === generation && host.isConnected;
    slider.type = "range";
    slider.min = 0;
    slider.max = days.length - 1;
    slider.value = index;
    slider.setAttribute("aria-label", "Replay session");
    speed.setAttribute("aria-label", "Replay playback speed");
    for (const n of [1, 5, 21]) {
      const o = el("option", `${n} session${n > 1 ? "s" : ""} per step`);
      o.value = n;
      speed.append(o);
    }
    function stop() {
      playing = false;
      clearTimeout(timer);
      play.textContent = "Play from start";
    }
    function step(i) {
      index = Math.max(0, Math.min(days.length - 1, i));
      offset = 0;
      slider.value = index;
      load();
    }
    first.onclick = () => {
      stop();
      step(0);
    };
    back.onclick = () => {
      stop();
      step(index - 1);
    };
    next.onclick = () => {
      stop();
      step(index + 1);
    };
    last.onclick = () => {
      stop();
      step(days.length - 1);
    };
    slider.oninput = () => {
      stop();
      step(+slider.value);
    };
    play.onclick = () => {
      if (playing) {
        stop();
        return;
      }
      playing = true;
      play.textContent = "Pause playback";
      step(index === days.length - 1 ? 0 : index);
    };
    search.type = "search";
    search.placeholder = "Filter replay trades by symbol";
    search.maxLength = 12;
    search.setAttribute("aria-label", "Filter replay trades by symbol");
    let debounce;
    search.oninput = () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        offset = 0;
        load();
      }, 150);
    };
    controls.append(first, back, play, next, last, speed);
    host.append(
      el("h2", "Session-by-session strategy replay"),
      el(
        "p",
        "Review the actual computed sequence. Each simulated decision used data available through that date; later outcomes were withheld from the strategy. Playback reviews the saved run, rather than training a model during the animation.",
        "muted",
      ),
      controls,
      dayLabel,
      slider,
      viewControls,
      search,
      error,
      content,
    );
    for (const [id, label] of [
      ["open", "Open positions"],
      ["closed", "Closed trades"],
      ["activity", "Session activity"],
    ]) {
      const b = el("button", label);
      b.dataset.view = id;
      b.setAttribute("role", "tab");
      b.onclick = () => {
        view = id;
        offset = 0;
        load();
      };
      viewControls.append(b);
    }
    viewControls.setAttribute("role", "tablist");
    viewControls.setAttribute("aria-label", "Replay trades");
    async function load() {
      if (!active()) return;
      const token = ++request,
        day = days[index],
        symbol = search.value.trim().toUpperCase(),
        key = `${day}:${view}:${offset}:${symbol}`;
      dayLabel.textContent = `Session ${index + 1} of ${days.length} · ${day}`;
      first.disabled = back.disabled = index === 0;
      next.disabled = last.disabled = index === days.length - 1;
      for (const b of viewControls.children)
        b.setAttribute("aria-selected", String(b.dataset.view === view));
      try {
        let data = cache.get(key);
        if (!data) {
          data = await api(
            `backtest/session?run_id=${report.run_id}&account=${encodeURIComponent(account.id)}&day=${day}&offset=${offset}&closed=${view === "closed"}&symbol=${encodeURIComponent(symbol)}`,
          );
          cache.set(key, data);
          if (cache.size > 30) cache.delete(cache.keys().next().value);
        }
        if (!active() || request !== token) return;
        error.textContent = "";
        content.replaceChildren();
        const metrics = el("div", undefined, "paper-metrics");
        for (const [label, value] of [
          ["Equity on selected date", money(data.equity)],
          [
            "Trading P/L through selected date",
            money(data.equity - data.deposits),
          ],
          ["Cash on selected date", money(data.cash)],
          ["Realized P/L through this date", money(data.realized_pnl)],
          ["Unrealized P/L on this date", money(data.unrealized_pnl)],
          ["Drawdown through selected date", percent(data.max_drawdown)],
        ]) {
          const m = el("div", undefined, "paper-metric");
          m.append(el("small", label), el("strong", value));
          metrics.append(m);
        }
        const summary = el("div", undefined, "paper-status");
        summary.append(
          el(
            "strong",
            `${data.opened_today} entries · ${data.closed_today} exits · ${data.open_positions} open positions · ${data.pending.length} pending orders`,
          ),
          el(
            "p",
            `${data.trades} closed trades through this date. ${data.opened_today || data.closed_today ? "Inspect the recorded trades and reasons below." : "No fills this session. Existing holdings were marked; decisions and pending orders explain what the strategy was waiting for."}`,
            "muted",
          ),
        );
        content.append(
          metrics,
          summary,
          KestrelPaperChart.render(
            { ...account, curve: account.curve.filter((r) => r.day <= day) },
            {
              record: "historical",
              benchmark:
                report.accounts.find((a) => a.id === "spy_hold")?.curve || [],
              trades: data.visible_trades,
            },
          ),
        );
        if (view === "activity") {
          content.append(el("h3", `Decisions on ${day}`));
          content.append(
            el(
              "p",
              data.decision_counts
                .map((d) => `${d.count} ${d.action.toLowerCase()}`)
                .join(" · ") || "No new decisions recorded on this session.",
              "muted",
            ),
          );
          for (const d of data.decisions.filter((d) =>
            d.symbol.startsWith(symbol),
          )) {
            const row = el("article", undefined, "paper-pending");
            row.append(
              el("strong", `${d.action} · ${d.symbol}`),
              el("p", d.reasons),
            );
            content.append(row);
          }
          for (const order of data.pending.filter((o) =>
            o.order.symbol.startsWith(symbol),
          )) {
            const row = el("article", undefined, "paper-pending");
            row.append(
              el(
                "strong",
                `Pending ${order.order.action.toLowerCase()} · ${order.order.symbol}`,
              ),
              el(
                "p",
                `Decided ${order.fill_after}; waits for a later eligible opening price.`,
              ),
            );
            content.append(row);
          }
        } else {
          const grid = el("div", undefined, "paper-trade-grid");
          for (const p of data.visible_trades) {
            const b = el("button", undefined, "paper-trade-card");
            b.append(
              el("span", p.symbol, "paper-symbol"),
              el("strong", money(view === "closed" ? p.pnl : p.unrealized_pnl)),
              el(
                "small",
                `${view === "closed" ? "Realized" : "Unrealized"} P/L as of ${day}`,
              ),
              el("p", p.entry_reason || "Original decision retained"),
              el(
                "small",
                `Opened ${p.entry_day}${p.exit_day ? ` · closed ${p.exit_day}` : ""}`,
              ),
            );
            b.onclick = () => openTrade(p, data.decisions, titles);
            grid.append(b);
          }
          content.append(grid);
          if (!data.visible_trades.length)
            content.append(
              el(
                "p",
                view === "closed"
                  ? "No closed trades by this date. Future exits are not shown."
                  : "No open positions on this date. Inspect Session activity for decisions and pending orders.",
                "muted",
              ),
            );
          if (data.total > 12) {
            const prev = el("button", "Previous replay trades"),
              more = el("button", "Next replay trades");
            prev.disabled = offset === 0;
            more.disabled = offset + 12 >= data.total;
            prev.onclick = () => {
              offset = Math.max(0, offset - 12);
              load();
            };
            more.onclick = () => {
              offset += 12;
              load();
            };
            content.append(
              prev,
              el(
                "span",
                `${offset + 1}–${Math.min(offset + 12, data.total)} of ${data.total}`,
              ),
              more,
            );
          }
        }
        clearTimeout(timer);
        if (playing && index < days.length - 1)
          timer = setTimeout(() => {
            if (active()) step(index + Number(speed.value));
            else stop();
          }, 650);
        else if (playing) stop();
      } catch (e) {
        if (active() && request === token) {
          error.textContent = e.message;
          stop();
        }
      }
    }
    setTimeout(load, 0);
    return host;
  }
  return { panel };
})();
