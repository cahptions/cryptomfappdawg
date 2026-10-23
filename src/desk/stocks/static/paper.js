"use strict";
const KestrelPaper = (() => {
  let record = "live",
    selected = "",
    tradeTab = "open",
    filter = "",
    tradeOffset = 0,
    showAll = false;
  const names = (accounts) =>
    Object.fromEntries(accounts.map((a) => [a.id, a.title]));
  const signed = (n) =>
    n == null ? "—" : `${n >= 0 ? "+" : "−"}${money(Math.abs(n))}`;
  function metric(label, value, cls = "") {
    const item = el("div", undefined, "paper-metric");
    item.append(el("small", label), el("strong", value, cls));
    return item;
  }
  function svg(tag, attrs) {
    const node = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [key, value] of Object.entries(attrs))
      node.setAttribute(key, value);
    return node;
  }
  function equityChart(account) {
    const box = el("section", undefined, "paper-equity"),
      curves = account.curve || [],
      controls = el("div", undefined, "research-tabs");
    const plot = el("div"),
      readout = el("p", "", "caption");
    box.append(el("h3", "Simulated account value"), controls, plot, readout);
    function draw(count) {
      const rows = curves.slice(-count);
      plot.replaceChildren();
      if (!rows.length) {
        plot.append(
          el(
            "p",
            "The equity curve appears after the first processed session.",
            "muted",
          ),
        );
        return;
      }
      const lo = Math.min(...rows.map((r) => r.equity)),
        hi = Math.max(...rows.map((r) => r.equity)),
        span = hi - lo || Math.max(1, hi * 0.01);
      const x = (i) => 20 + (i / Math.max(1, rows.length - 1)) * 960,
        y = (v) => 215 - ((v - lo) / span) * 185;
      const chart = svg("svg", {
        viewBox: "0 0 1000 260",
        role: "img",
        tabindex: "0",
        "aria-label":
          "Simulated equity chart. Use arrow keys to inspect daily account values.",
      });
      chart.append(
        svg("line", {
          x1: 20,
          x2: 980,
          y1: 215,
          y2: 215,
          class: "chart-guide",
        }),
      );
      chart.append(
        svg("path", {
          d: rows
            .map((r, i) => `${i ? "L" : "M"}${x(i)},${y(r.equity)}`)
            .join(""),
          class: "paper-equity-line",
        }),
      );
      const cursor = svg("circle", {
        cx: x(rows.length - 1),
        cy: y(rows.at(-1).equity),
        r: 5,
        class: "paper-equity-point",
      });
      chart.append(cursor);
      let index = rows.length - 1;
      function inspect(i) {
        index = Math.max(0, Math.min(rows.length - 1, i));
        const row = rows[index];
        cursor.setAttribute("cx", x(index));
        cursor.setAttribute("cy", y(row.equity));
        readout.textContent = `${row.day} · ${money(row.equity)} · ${record === "live" ? "Recorded forward PAPER" : "Historical reconstruction"}`;
      }
      chart.onpointermove = (e) => {
        const r = chart.getBoundingClientRect();
        inspect(
          Math.round(
            ((((e.clientX - r.left) / r.width) * 1000 - 20) / 960) *
              (rows.length - 1),
          ),
        );
      };
      chart.onpointerleave = () => inspect(rows.length - 1);
      chart.onkeydown = (e) => {
        if (
          ["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(e.key)
        ) {
          e.preventDefault();
          inspect(
            e.key === "Home"
              ? 0
              : ["End", "Escape"].includes(e.key)
                ? rows.length - 1
                : index + (e.key === "ArrowLeft" ? -1 : 1),
          );
        }
      };
      const axis = el("div", undefined, "chart-axis");
      axis.append(el("span", rows[0].day), el("span", rows.at(-1).day));
      plot.append(chart, axis);
      inspect(index);
      for (const b of controls.children)
        b.setAttribute("aria-selected", String(+b.dataset.count === count));
    }
    for (const [label, count] of [
      ["1M", 21],
      ["3M", 63],
      ["Max", 10000],
    ]) {
      const b = el("button", label);
      b.dataset.count = count;
      b.onclick = () => draw(count);
      controls.append(b);
    }
    draw(10000);
    return box;
  }
  function tradeDialog(position, decisions, titles) {
    const dialog = el("dialog", undefined, "paper-trade-dialog"),
      head = el("div", undefined, "calc-heading"),
      close = el("button", "Close");
    const title = el(
      "h2",
      `${position.symbol} · ${position.structure.replaceAll("_", " ")}`,
    );
    title.id = "paper-trade-title";
    dialog.setAttribute("aria-labelledby", title.id);
    head.append(title, close);
    close.onclick = () => dialog.close();
    dialog.append(
      head,
      el(
        "p",
        `${titles[position.account] || position.account} · ${position.state === "CLOSED" ? "Closed simulated trade" : "Open simulated position"}`,
        "muted",
      ),
    );
    const metrics = el("div", undefined, "paper-metrics");
    metrics.append(
      metric(
        "Entry including fees",
        money(position.entry_cost + position.entry_fees),
      ),
      metric(
        position.state === "CLOSED" ? "Realized P/L" : "Unrealized P/L",
        signed(
          position.state === "CLOSED" ? position.pnl : position.unrealized_pnl,
        ),
      ),
      metric("Entry date", position.entry_day),
    );
    dialog.append(metrics);
    const legs = el("div", undefined, "paper-leg-list");
    for (const leg of position.legs || []) {
      const row = el("div");
      row.append(
        el(
          "strong",
          `${leg.quantity > 0 ? "Long" : "Short"} ${number(Math.abs(leg.quantity))} ${leg.kind === "stock" ? "shares" : `${leg.kind} contracts`}`,
        ),
        el(
          "span",
          `${leg.strike ? money(leg.strike) + " strike · " : ""}${leg.expiration || "Shares"} · Entry ${money(leg.entry_price)}`,
        ),
      );
      legs.append(row);
    }
    dialog.append(el("h3", "Position details"), legs);
    if (position.stop_price != null)
      dialog.append(
        el(
          "p",
          `Modeled stop: ${money(position.stop_price)}. Gaps can exceed it.`,
          "muted",
        ),
      );
    if (position.exit_day)
      dialog.append(
        el(
          "p",
          `Closed ${position.exit_day} · ${position.exit_reason || "Strategy exit"} · Proceeds ${money(position.exit_credit)} · Exit fees ${money(position.exit_fees)}`,
        ),
      );
    const decision = decisions.find((d) => d.id === position.decision_id);
    if (decision || position.entry_reason)
      dialog.append(
        el("h3", "Why it was selected"),
        el("p", position.entry_reason || decision.reasons),
      );
    const research = link(
      "Open stock research ↗",
      `#stock/${encodeURIComponent(position.symbol)}`,
    );
    research.onclick = () => dialog.close();
    dialog.append(
      research,
      el(
        "p",
        "Simulated fills and marks, not brokerage transactions. Open P/L excludes any future closing costs.",
        "muted caption",
      ),
    );
    document.body.append(dialog);
    dialog.showModal();
    dialog.onclose = () => {
      dialog.remove();
      if (location.hash === "#paper") refresh(true);
    };
  }
  async function show(ticket) {
    let [live, backtest] = await Promise.all([api("paper"), api("backtest")]);
    if (ticket !== generation) return;
    app.className = "paper-page";
    let timer;
    function draw() {
      if (ticket !== generation) return;
      const report = record === "live" ? live.report : backtest.report,
        accounts = report?.accounts || [],
        titles = names(accounts);
      if (!accounts.some((a) => a.id === selected))
        selected =
          accounts.find((a) => !a.baseline && a.open_positions)?.id ||
          accounts.find((a) => !a.baseline)?.id ||
          accounts[0]?.id ||
          "";
      const account = accounts.find((a) => a.id === selected),
        hero = el("div", undefined, "explorer-heading"),
        heading = el("div"),
        tabs = el("div", undefined, "research-tabs paper-record-tabs");
      heading.append(
        el("div", "YOUR SIMULATED TRADING DESK", "eyebrow"),
        el("h1", "Paper trading"),
        el(
          "p",
          "Inspect the strategy, the position and the reason behind each simulated trade.",
          "muted",
        ),
      );
      hero.append(heading, collectionControls());
      app.replaceChildren(hero);
      for (const [id, label] of [
        ["live", "Live PAPER"],
        ["historical", "Historical backtests"],
      ]) {
        const b = el("button", label);
        b.setAttribute("role", "tab");
        b.setAttribute("aria-selected", String(record === id));
        b.onclick = () => {
          record = id;
          selected = "";
          tradeOffset = 0;
          tradeTab = "open";
          draw();
        };
        tabs.append(b);
      }
      tabs.setAttribute("role", "tablist");
      tabs.setAttribute("aria-label", "Trading record");
      app.append(tabs);
      if (record === "live") {
        const s = live.session_status || {},
          note = el("section", undefined, "paper-status");
        note.append(
          el(
            "strong",
            s.waiting_for_prices
              ? "Waiting for the next complete daily close"
              : "Forward PAPER is recording each market session once",
          ),
          el(
            "p",
            `Recorded through ${s.last_processed || "not started"} · Latest bulk close ${s.latest_prices || "not downloaded"} · ${s.pending_orders || 0} pending orders. ${s.explanation || ""}`,
            "muted caption",
          ),
          link("Check data health ↗", "#proof"),
        );
        app.append(note);
      } else {
        const job = backtest.job || {},
          section = el("section", undefined, "paper-backtest-controls"),
          controls = el("div", undefined, "evidence-actions"),
          window = el("select"),
          run = el(
            "button",
            job.running ? "Replaying history…" : "Run historical backtest",
            "primary",
          ),
          auto = el("input"),
          autoLabel = el("label", undefined, "paper-auto");
        for (const n of [63, 126, 252]) {
          const o = el("option", `${n} trading sessions`);
          o.value = n;
          window.append(o);
        }
        window.value = job.requested_sessions || 63;
        window.setAttribute("aria-label", "Backtest window");
        window.disabled = !!job.running;
        run.disabled = !!job.running;
        controls.append(window, run);
        const message = el(
          "p",
          job.preference_error ||
            job.phase ||
            "Replay past stock strategies with a separate simulated ledger.",
          "muted",
        );
        message.setAttribute("role", "status");
        const update = async (path, payload) => {
          try {
            backtest.job = await api(path, payload);
            draw();
          } catch (e) {
            message.textContent = e.message;
            run.disabled = false;
          }
        };
        run.onclick = () => {
          run.disabled = true;
          update("backtest/start", { sessions: +window.value });
        };
        if (job.running && job.owned) {
          const cancel = el(
            "button",
            job.stopping ? "Stopping…" : "Cancel backtest",
          );
          cancel.disabled = !!job.stopping;
          cancel.onclick = () => update("backtest/cancel", {});
          controls.append(cancel);
        }
        auto.type = "checkbox";
        auto.checked = !!job.auto_enabled;
        auto.onchange = () =>
          update("backtest/automatic", {
            enabled: auto.checked,
            sessions: +window.value,
          });
        autoLabel.append(
          auto,
          el(
            "span",
            "Automatically refresh this backtest when a new daily close is saved",
          ),
        );
        window.onchange = () =>
          update("backtest/automatic", {
            enabled: auto.checked,
            sessions: +window.value,
          });
        section.append(
          el("h2", "Historical strategy replay"),
          el(
            "p",
            "Signals are reconstructed in date order; entries fill at a later open. Historical results stay separate from the forward PAPER record.",
            "muted",
          ),
          controls,
          autoLabel,
          message,
        );
        if (job.running) {
          const progress = el("progress");
          if (job.total) {
            progress.max = job.total;
            progress.value = job.completed;
          }
          progress.setAttribute("aria-label", "Historical replay progress");
          section.append(progress);
        }
        const c = job.coverage;
        if (c)
          section.append(
            el(
              "p",
              `${c.sessions} stored sessions · ${c.warmup_sessions} earlier sessions reserved for warm-up · latest close ${c.latest || "unavailable"}. Automatic replay follows PAPER Pause/STOP.`,
              "muted caption",
            ),
          );
        if (backtest.report && job.running)
          section.append(
            el(
              "p",
              `Showing the previous completed run through ${backtest.report.last_day} while the new replay runs.`,
              "notice",
            ),
          );
        app.append(section);
      }
      if (!account) {
        const empty = el("section", undefined, "screen-card");
        empty.append(
          el(
            "h2",
            record === "live"
              ? "The forward record starts here"
              : "Ready for your first historical replay",
          ),
          el(
            "p",
            record === "live"
              ? "PAPER starts with the app unless paused. Eligible strategies begin when their data is available; 63/126-session evidence thresholds do not block entries."
              : "Replay starts automatically when enough bulk history and warm-up sessions are saved. You can also start it above. Option-chain histories cannot be reconstructed from stock prices.",
            "muted",
          ),
          link("Review data connections ↗", "#settings"),
        );
        app.append(empty);
        poll();
        return;
      }
      const strategyHeading = el("div", undefined, "section-heading"),
        strategyCards = el("div", undefined, "paper-strategies"),
        toggle = el("button", showAll ? "Show fewer" : "All strategies");
      toggle.onclick = () => {
        showAll = !showAll;
        draw();
      };
      strategyHeading.append(el("h2", "Choose a strategy to inspect"), toggle);
      app.append(strategyHeading);
      const featured = showAll
        ? accounts
        : [account, ...accounts.filter((a) => a.id !== selected)].slice(0, 4);
      for (const a of featured) {
        const b = el("button", undefined, "paper-strategy");
        b.setAttribute("aria-pressed", String(a.id === selected));
        b.append(
          el("strong", a.title),
          el("span", `${money(a.equity)} · ${percent(a.return)}`),
          el(
            "small",
            `${a.open_positions || 0} open · ${a.trades || 0} closed · ${a.sessions} sessions`,
          ),
        );
        b.onclick = () => {
          selected = a.id;
          tradeOffset = 0;
          draw();
        };
        strategyCards.append(b);
      }
      app.append(strategyCards);
      const selectedTitle = el("div", undefined, "paper-account-title");
      selectedTitle.append(
        el("h2", account.title),
        el("p", account.hypothesis || "", "muted"),
      );
      app.append(selectedTitle);
      const metrics = el("div", undefined, "paper-metrics");
      metrics.append(
        metric("Simulated equity", money(account.equity)),
        metric(
          "Net P/L vs starting capital",
          signed(
            account.equity == null ? null : account.equity - account.deposits,
          ),
        ),
        metric("Net return", percent(account.return)),
        metric("Cash available", money(account.cash)),
        metric("Largest drawdown", percent(account.max_drawdown)),
        metric("Closed-trade win rate", percent(account.win_rate)),
      );
      const curveHost = equityChart(account);
      app.append(metrics, curveHost);
      if (record === "live")
        api(`paper/equity?account=${encodeURIComponent(account.id)}`)
          .then((data) => {
            if (
              ticket === generation &&
              curveHost.isConnected &&
              data.curve.length > (account.curve || []).length
            ) {
              account.curve = data.curve;
              curveHost.replaceWith(equityChart(account));
            }
          })
          .catch(() => {
            if (ticket === generation && curveHost.isConnected)
              curveHost.append(
                el(
                  "p",
                  "Showing the saved curve. The complete ledger curve could not be refreshed; reopen Paper to retry.",
                  "muted caption",
                ),
              );
          });
      app.append(
        el(
          "p",
          `Each strategy uses its own simulated capital. ${record === "live" ? (typeof account.grade === "object" ? account.grade.text : account.grade) || "Forward evidence is still accumulating." : `Historical reconstruction: ${report.first_day} through ${report.last_day}.`}`,
          "muted caption",
        ),
      );
      const section = el("section", undefined, "paper-trades"),
        sectionHead = el("div", undefined, "section-heading"),
        search = el("input");
      search.type = "search";
      search.maxLength = 12;
      search.placeholder = "Filter by symbol";
      search.value = filter;
      search.setAttribute("aria-label", "Filter simulated trades by symbol");
      sectionHead.append(el("h2", "Your simulated trades"), search);
      section.append(sectionHead);
      const views = el("div", undefined, "research-tabs"),
        content = el("div");
      const source = record === "live" ? live : report;
      let trades = (source.trades || []).filter((p) => p.account === selected),
        decisions = (source.decisions || []).filter(
          (d) => d.account === selected,
        ),
        pending = (source.pending || []).filter((o) => o.account === selected);
      let tradeRequest = 0;
      async function tradeContent() {
        const request = ++tradeRequest;
        filter = search.value
          .toUpperCase()
          .trim()
          .replace(/[^A-Z0-9.-]/g, "");
        content.replaceChildren();
        let pageTotal;
        if (record === "live") {
          try {
            const page = await api(
              `paper/trades?account=${encodeURIComponent(selected)}&state=${tradeTab === "closed" ? "CLOSED" : "OPEN"}&limit=12&offset=${tradeOffset}&symbol=${encodeURIComponent(filter)}`,
            );
            if (
              request !== tradeRequest ||
              ticket !== generation ||
              !content.isConnected
            )
              return;
            trades = page.trades;
            decisions = page.decisions;
            pending = page.pending;
            pageTotal = page.total;
          } catch (error) {
            if (request === tradeRequest && content.isConnected)
              content.append(el("p", error.message, "error"));
            return;
          }
        }
        for (const b of views.children)
          b.setAttribute("aria-selected", String(b.dataset.id === tradeTab));
        function paging(total) {
          if (total <= 12) return;
          const controls = el("div", undefined, "evidence-actions"),
            previous = el("button", "Previous results"),
            next = el("button", "Next results");
          previous.disabled = tradeOffset === 0;
          next.disabled = tradeOffset + 12 >= total;
          previous.onclick = () => {
            tradeOffset = Math.max(0, tradeOffset - 12);
            tradeContent();
          };
          next.onclick = () => {
            tradeOffset += 12;
            tradeContent();
          };
          controls.append(
            previous,
            el(
              "span",
              `${tradeOffset + 1}–${Math.min(total, tradeOffset + 12)} of ${total}`,
            ),
            next,
          );
          content.append(controls);
        }
        if (tradeTab === "decisions") {
          const list = el("div", undefined, "paper-timeline");
          const matching = decisions.filter((d) => d.symbol.includes(filter));
          for (const d of matching.slice(tradeOffset, tradeOffset + 12)) {
            const row = el("article");
            row.append(
              el(
                "strong",
                `${d.action === "ENTER" ? "Entry planned" : d.action === "EXIT" ? "Exit planned" : d.action} · ${d.symbol}`,
              ),
              el("small", d.asof),
              el("p", d.reasons || "No additional reason stored."),
            );
            list.append(row);
          }
          content.append(list);
          paging(matching.length);
          if (!list.children.length)
            content.append(
              el(
                "p",
                "No matching decisions recorded for this strategy.",
                "muted",
              ),
            );
          return;
        }
        if (tradeTab === "pending") {
          for (const o of pending.filter((o) =>
            (o.order.symbol || "").includes(filter),
          )) {
            const card = el("article", undefined, "paper-pending");
            card.append(
              el(
                "strong",
                `${o.order.action === "ENTER" ? "Planned entry" : "Planned exit"} · ${o.order.symbol}`,
              ),
              el(
                "p",
                `Waiting for eligible fill data after ${o.fill_after}. ${o.order.notional ? `Target size ${money(o.order.notional)}.` : ""}`,
                "muted",
              ),
            );
            content.append(card);
          }
          if (!content.children.length)
            content.append(
              el("p", "No pending orders for this strategy.", "muted"),
            );
          return;
        }
        const matched = trades.filter(
          (p) =>
            p.state === (tradeTab === "open" ? "OPEN" : "CLOSED") &&
            p.symbol.includes(filter),
        );
        pageTotal ??= matched.length;
        const rows =
          record === "live"
            ? matched
            : matched.slice(tradeOffset, tradeOffset + 12);
        const grid = el("div", undefined, "paper-trade-grid");
        for (const p of rows) {
          const b = el("button", undefined, "paper-trade-card"),
            pl = p.state === "CLOSED" ? p.pnl : p.unrealized_pnl;
          b.append(
            el("span", p.symbol, "paper-symbol"),
            el("small", p.structure.replaceAll("_", " ")),
            el(
              "strong",
              signed(pl),
              pl == null ? "" : pl >= 0 ? "positive" : "negative",
            ),
            el(
              "span",
              `${p.state === "CLOSED" ? "Realized" : "Unrealized"} P/L`,
            ),
            el(
              "small",
              `Opened ${p.entry_day}${p.exit_day ? ` · Closed ${p.exit_day}` : ""}`,
            ),
            el(
              "small",
              `Entry ${money(p.entry_cost + p.entry_fees)} · View trade details ↗`,
            ),
          );
          b.onclick = () => tradeDialog(p, decisions, titles);
          grid.append(b);
        }
        content.append(grid);
        paging(pageTotal);
        if (!rows.length)
          content.append(
            el(
              "p",
              tradeTab === "open"
                ? "No matching open positions. Check pending orders and decisions to see what the strategy is waiting for."
                : "No matching closed trades yet. Open positions remain visible in their own view.",
              "muted",
            ),
          );
      }
      for (const [id, label] of [
        ["open", `Open positions (${account.open_positions || 0})`],
        ["closed", `Closed trades (${account.trades || 0})`],
        ["pending", `Pending (${pending.length})`],
        ["decisions", "Decision timeline"],
      ]) {
        const b = el("button", label);
        b.dataset.id = id;
        b.setAttribute("role", "tab");
        b.onclick = () => {
          tradeTab = id;
          tradeOffset = 0;
          tradeContent();
        };
        views.append(b);
      }
      views.setAttribute("role", "tablist");
      views.setAttribute("aria-label", "Trade view");
      search.oninput = () => {
        tradeOffset = 0;
        tradeContent();
      };
      section.append(views, content);
      tradeContent();
      app.append(section);
      if (record === "historical") {
        const wf = el("section", undefined, "screen-card");
        wf.append(
          el("h2", "Walk-forward strategy checks"),
          el("p", report.walk_forward?.message || "", "muted"),
        );
        for (const w of report.walk_forward?.windows || []) {
          const row = el("article", undefined, "paper-walk");
          row.append(
            el("strong", w.title),
            el(
              "p",
              `Selected using data through ${w.train_through}. Held-out check: ${w.test_start} to ${w.test_end}.`,
            ),
            el(
              "p",
              `Past training return ${percent(w.training_return)} · Held-out return ${percent(w.test_return)} · SPY baseline ${percent(w.spy_return)} · Difference ${percent(w.excess_return)}`,
              "muted",
            ),
          );
          wf.append(row);
        }
        const limits = el("details");
        limits.append(el("summary", "Data coverage and replay assumptions"));
        limits.append(
          el(
            "p",
            `Replay requested ${report.requested_sessions} sessions; ${report.skipped_sessions?.length || 0} sessions skipped. Account statistics include all trades; the detail view retains the latest ${report.trade_detail_limit || 500} positions across strategies and the latest 200 decisions.`,
            "muted caption",
          ),
        );
        for (const skipped of report.skipped_sessions || [])
          limits.append(
            el("p", `${skipped.day}: ${skipped.reason}`, "muted caption"),
          );
        for (const note of report.limitations || [])
          limits.append(el("p", note, "muted caption"));
        wf.append(limits);
        app.append(wf);
      }
      poll();
    }
    function poll() {
      clearTimeout(timer);
      if (backtest.job?.running || record === "historical")
        timer = setTimeout(
          async () => {
            if (ticket !== generation) return;
            if (document.querySelector("dialog[open]")) {
              poll();
              return;
            }
            try {
              const next = await api("backtest");
              const changed =
                JSON.stringify([
                  backtest.job?.running,
                  backtest.job?.auto_enabled,
                  backtest.job?.requested_sessions,
                  backtest.report?.generated_at,
                ]) !==
                JSON.stringify([
                  next.job?.running,
                  next.job?.auto_enabled,
                  next.job?.requested_sessions,
                  next.report?.generated_at,
                ]);
              backtest = next;
              if (changed && record === "historical") draw();
              else {
                if (record === "historical") {
                  const phase = app.querySelector(
                      ".paper-backtest-controls [role='status']",
                    ),
                    progress = app.querySelector(
                      ".paper-backtest-controls progress",
                    );
                  if (phase) phase.textContent = next.job?.phase || "Ready";
                  if (progress && next.job?.total) {
                    progress.max = next.job.total;
                    progress.value = next.job.completed;
                  }
                }
                poll();
              }
            } catch (e) {
              statusLine.textContent = e.message;
            }
          },
          backtest.job?.running ? 2000 : 5000,
        );
    }
    draw();
  }
  return { show };
})();
