"use strict";
const KestrelLearning = (() => {
  let account = "uncapped_research",
    view = "open",
    offset = 0;
  const signed = (value) =>
    `${value >= 0 ? "+" : "−"}${money(Math.abs(value))}`;
  const stamp = (value) =>
    value
      ? new Date(value).toLocaleString("en-US", {
          timeZone: "America/New_York",
        }) + " ET"
      : "Not yet";
  function metric(label, value) {
    const n = el("div", undefined, "paper-metric");
    n.append(el("small", label), el("strong", value));
    return n;
  }
  const policyName = (mode) =>
    ({
      starter: "Starter policy · research still gathering outcomes",
      provisional: "Provisional research findings · still being tested",
      research_champion: "Promoted research structure · small-account test",
      waiting: "Holding cash · waiting for supported findings",
    })[mode] || "Waiting for a policy decision";
  function comparisons(accounts, select) {
    const section = el("section", undefined, "learning-small-accounts"),
      grid = el("div", undefined, "learning-comparison-grid");
    section.append(
      el("h2", "Small-account tests"),
      el(
        "p",
        "These accounts choose affordable trades from the research available at each decision. Each keeps its own cash and results, with no automatic top-ups. They start when this update first runs.",
        "muted",
      ),
    );
    for (const a of accounts.filter((a) => a.small_account)) {
      const card = el("article", undefined, "learning-account-card"),
        stats = el("dl"),
        open = el("button", `View ${a.title}`);
      open.onclick = () => select(a.id);
      card.append(
        el("h3", a.title),
        el("strong", money(a.equity), "learning-account-equity"),
      );
      for (const [title, value] of [
        ["Net trading P/L", signed(a.trading_pnl)],
        ["Return on starting balance", percent(a.return_on_start)],
        ["Cash available", money(a.cash)],
        ["Trades", `${a.open_positions} open · ${a.closed_trades} closed`],
      ])
        stats.append(el("dt", title), el("dd", value));
      card.append(
        stats,
        el("p", policyName(a.policy?.mode), "caption muted"),
        open,
      );
      grid.append(card);
    }
    section.append(grid);
    return section;
  }
  function profitChart(curve) {
    const box = el("section", undefined, "paper-equity"),
      readout = el("p", "", "caption");
    box.append(el("h3", "Trading P/L history — virtual funding excluded"));
    if (!curve.length) {
      box.append(
        el(
          "p",
          "The curve begins with the first observed trading tick.",
          "muted",
        ),
      );
      return box;
    }
    const low = Math.min(0, ...curve.map((r) => r.pnl)),
      high = Math.max(0, ...curve.map((r) => r.pnl)),
      span = high - low || 1;
    const x = (i) => 20 + (i / Math.max(1, curve.length - 1)) * 960,
      y = (v) => 220 - ((v - low) / span) * 180;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 1000 260");
    svg.setAttribute("tabindex", "0");
    svg.setAttribute("role", "img");
    svg.setAttribute(
      "aria-label",
      "Learning trading P/L excluding funding. Arrow keys inspect observations.",
    );
    const path = document.createElementNS(svg.namespaceURI, "path");
    path.setAttribute(
      "d",
      curve.map((r, i) => `${i ? "L" : "M"}${x(i)},${y(r.pnl)}`).join(""),
    );
    path.setAttribute("class", "paper-equity-line");
    svg.append(path);
    if (curve.length === 1) {
      const dot = document.createElementNS(svg.namespaceURI, "circle");
      for (const [key, value] of Object.entries({
        cx: x(0),
        cy: y(curve[0].pnl),
        r: 4,
        fill: "#abc6ff",
      }))
        dot.setAttribute(key, value);
      svg.append(dot);
      box.append(
        el(
          "p",
          "One recorded starting point; the path grows as fresh quotes and trades are observed.",
          "caption muted",
        ),
      );
    }
    let selected = curve.length - 1;
    const inspect = (i) => {
      selected = Math.max(0, Math.min(curve.length - 1, i));
      readout.textContent = `${stamp(curve[selected].at)} · ${signed(curve[selected].pnl)} trading P/L`;
    };
    svg.onpointermove = (e) => {
      const b = svg.getBoundingClientRect();
      inspect(
        Math.round(
          ((((e.clientX - b.left) / b.width) * 1000 - 20) / 960) *
            (curve.length - 1),
        ),
      );
    };
    svg.onpointerleave = () => inspect(curve.length - 1);
    svg.onkeydown = (e) => {
      if (
        ["Home", "End", "ArrowLeft", "ArrowRight", "Escape"].includes(e.key)
      ) {
        e.preventDefault();
        inspect(
          e.key === "Home"
            ? 0
            : ["End", "Escape"].includes(e.key)
              ? curve.length - 1
              : selected + (e.key === "ArrowLeft" ? -1 : 1),
        );
      }
    };
    inspect(selected);
    box.append(svg, readout);
    return box;
  }
  async function show(ticket) {
    let data = await api("learning");
    if (ticket !== generation) return;
    let timer;
    function draw() {
      if (ticket !== generation) return;
      app.replaceChildren();
      app.className = "paper-page learning-page";
      const hero = el("div", undefined, "explorer-heading"),
        text = el("div"),
        controls = el("div", undefined, "evidence-actions");
      text.append(
        el("div", "VIRTUAL CAPITAL · FORWARD EXPERIMENTS", "eyebrow"),
        el("h1", "PAPER Learning"),
        el(
          "p",
          "Research tests sizing, structures and holding periods. The $1,000 and $5,000 accounts test whether its findings work with smaller balances.",
          "muted",
        ),
      );
      const pause = el(
          "button",
          data.control.enabled ? "Pause Learning" : "Start Learning",
          "primary",
        ),
        message = el("p", "", "muted");
      pause.onclick = async () => {
        pause.disabled = true;
        try {
          await api("learning/control", { enabled: !data.control.enabled });
          data = await api("learning");
          draw();
        } catch (e) {
          message.textContent = e.message;
          pause.disabled = false;
        }
      };
      controls.append(pause);
      hero.append(text, controls);
      app.append(hero, message);
      const status = el("section", undefined, "paper-status");
      status.setAttribute("role", "status");
      status.append(
        el(
          "strong",
          !data.control.enabled || !data.paper_enabled || data.stop
            ? "Learning paused"
            : data.worker.error || data.progress.phase,
        ),
        el(
          "p",
          data.control.error ||
            data.worker.error ||
            data.collection.phase ||
            "The app collects and evaluates observations automatically.",
          "muted",
        ),
        el(
          "p",
          "Research spending, trade count, position count and contract quantity have no policy ceiling. Market-data and estimated execution capacity still apply.",
          "caption",
        ),
      );
      if (!data.paper_enabled) status.append(collectionControls());
      if (data.stop)
        status.append(
          el("p", "Global STOP is active. Learning will respect it.", "notice"),
        );
      status.append(link("Manage market-data connection ↗", "#settings"));
      if (data.collection.checked?.length)
        status.append(
          el(
            "p",
            `Checked ${data.collection.checked.join(", ")} · ${data.collection.queued} other underlyings queued for rotation.`,
            "caption",
          ),
        );
      for (const error of data.collection.errors || [])
        status.append(el("p", error, "error"));
      app.append(status);
      app.append(
        comparisons(data.accounts, (id) => {
          account = id;
          offset = 0;
          draw();
        }),
      );
      const tabs = el("div", undefined, "research-tabs");
      for (const a of data.accounts) {
        const b = el("button", a.title);
        b.setAttribute("aria-pressed", String(a.id === account));
        b.onclick = () => {
          account = a.id;
          offset = 0;
          draw();
        };
        tabs.append(b);
      }
      app.append(tabs);
      const a = data.accounts.find((x) => x.id === account) || data.accounts[0];
      if (!a) return;
      const selected = el("div", undefined, "learning-account-heading");
      selected.append(
        el("h2", a.title),
        el(
          "p",
          `Started ${stamp(a.started_at)} · Last recorded observation ${stamp(a.curve.at(-1)?.at)}`,
          "caption muted",
        ),
      );
      if (a.small_account) {
        const policy = el(
            "section",
            undefined,
            "paper-status learning-transfer-policy",
          ),
          e = a.policy?.forecast;
        policy.append(
          el("h3", policyName(a.policy?.mode)),
          el("p", a.policy?.phase || "Waiting for fresh quotes"),
          el(
            "p",
            a.policy?.note ||
              "Orders wait for a later fresh quote. Fees, spreads and execution capacity apply.",
            "muted",
          ),
          el(
            "p",
            "Sizing targets 25% of current equity. A single whole contract may exceed that target if affordable; total spending never exceeds available cash. The account can hold cash when research is unfavorable or no suitable contract fits.",
            "caption muted",
          ),
        );
        if (e)
          policy.append(
            el(
              "p",
              `${e.blocks} prior underlying/day blocks across ${e.days} days. Research outcomes through ${stamp(e.matured_through)}. Decision ${stamp(a.policy.decision_at)}.`,
              "caption muted",
            ),
          );
        selected.append(policy);
      }
      app.append(selected);
      const metrics = el("div", undefined, "paper-metrics");
      metrics.append(
        metric("Starting virtual capital", money(a.starting_capital)),
        metric("Additional virtual funding", money(a.additional_funding)),
        metric("Total contributed", money(a.contributed)),
        metric("Equity including funding", money(a.equity)),
        metric("Trading P/L — funding excluded", signed(a.trading_pnl)),
        metric("Cash available", money(a.cash)),
        metric(
          "Capital employed — marked positions",
          money(a.capital_employed),
        ),
        metric("Realized trading P/L", signed(a.realized_pnl)),
        metric("Unrealized P/L", signed(a.unrealized_pnl)),
        metric("Trading fees", money(a.fees)),
        metric("Trading P/L drawdown", money(a.trading_pnl_drawdown)),
      );
      if (a.small_account)
        metrics.append(
          metric("Return on starting balance", percent(a.return_on_start)),
        );
      if (a.unresolved_expiries)
        app.append(
          el(
            "p",
            `${a.unresolved_expiries} expired positions await verified settlement data. Their marks may be stale; they do not become training outcomes.`,
            "notice",
          ),
        );
      app.append(
        metrics,
        el(
          "p",
          `${a.open_positions} open positions · ${a.closed_trades} closed trades · ${a.pending_orders} pending orders in this account. P/L / contributed capital: ${percent(a.pnl_per_contributed_dollar)}.`,
          "muted",
        ),
        profitChart(a.curve),
      );
      const model = el("section", undefined, "paper-backtest-controls");
      model.append(
        el("h2", "What the system has learned"),
        el("strong", data.model.status),
        el(
          "p",
          `Model version ${data.model.version}. ${data.model.training_outcomes || 0} training outcomes · ${data.model.test_outcomes || 0} held-out outcomes · ${data.model.test_blocks || 0} underlying/day test blocks.`,
          "muted",
        ),
        el(
          "p",
          data.model.candidate
            ? `Prior-data candidate: ${data.model.candidate}. Held-out mean net return: ${percent(data.model.test_net_return)}. ${data.model.promoted ? "PAPER policy promoted." : "No promotion; forward experimentation continues."}`
            : "Predictions begin as registered hypotheses. Matured outcomes update estimates; funding and repeated fills do not count as independent evidence.",
          "muted",
        ),
      );
      app.append(model);
      const views = el("div", undefined, "research-tabs"),
        content = el("section", undefined, "paper-trades");
      for (const [id, title] of [
        ["open", "Open positions"],
        ["closed", "Closed trades"],
        ["orders", "Orders & fills"],
        ["funding", "Virtual funding"],
        ["experiments", "Experiments"],
        ["checks", "Check-ins"],
      ]) {
        const b = el("button", title);
        b.setAttribute("aria-pressed", String(view === id));
        b.onclick = () => {
          view = id;
          offset = 0;
          draw();
        };
        views.append(b);
      }
      app.append(views, content);
      if (view === "open" || view === "closed") {
        content.append(
          el("h2", view === "open" ? "Account positions" : "Completed trades"),
        );
        api(
          `learning/trades?account=${account}&state=${view === "open" ? "OPEN" : "CLOSED"}&offset=${offset}&limit=12`,
        )
          .then((page) => {
            if (ticket !== generation || !content.isConnected) return;
            const grid = el("div", undefined, "paper-trade-grid");
            for (const p of page.positions) {
              const b = el("button", undefined, "paper-trade-card");
              b.append(
                el("span", p.symbol, "paper-symbol"),
                el(
                  "small",
                  `${p.structure} · ${p.plan.filled_units} units filled of ${p.plan.desired_units} requested`,
                ),
                el(
                  "strong",
                  signed(p.state === "CLOSED" ? p.pnl : p.unrealized_pnl || 0),
                ),
                el(
                  "small",
                  `Entry ${money(p.entry_cost + p.entry_fees)} · ${p.plan.hold_minutes}-minute hypothesis`,
                ),
              );
              if (p.plan.transfer_policy)
                b.append(el("small", policyName(p.plan.transfer_policy.mode)));
              b.onclick = () =>
                KestrelPaper.tradeDialog(
                  p,
                  page.decisions,
                  Object.fromEntries(data.accounts.map((x) => [x.id, x.title])),
                );
              grid.append(b);
            }
            content.append(grid);
            if (!page.positions.length)
              content.append(
                el(
                  "p",
                  "No positions in this view yet. Experiments wait for a later fresh quote before filling.",
                  "muted",
                ),
              );
            const prev = el("button", "Previous trades"),
              next = el("button", "Next trades");
            prev.disabled = offset === 0;
            next.disabled = offset + 12 >= page.total;
            prev.onclick = () => {
              offset = Math.max(0, offset - 12);
              draw();
            };
            next.onclick = () => {
              offset += 12;
              draw();
            };
            const nav = el("div", undefined, "evidence-actions");
            nav.append(
              prev,
              el("span", `${page.total} stored positions in this view`),
              next,
            );
            content.append(nav);
          })
          .catch((e) => {
            if (content.isConnected)
              content.append(el("p", e.message, "error"));
          });
      } else if (view === "orders") {
        content.append(
          el("h2", "Requested, filled and unfilled quantities"),
          el(
            "p",
            "A partial fill remains a real simulated position; unfilled contracts receive no invented returns. Browse every stored order in this account.",
            "muted",
          ),
        );
        api(`learning/orders?account=${account}&offset=${offset}&limit=12`)
          .then((page) => {
            if (ticket !== generation || !content.isConnected) return;
            const grid = el("div", undefined, "paper-trade-grid");
            for (const order of page.orders) {
              const n = el("article", undefined, "paper-trade-card");
              n.append(
                el("strong", `${order.symbol} · ${order.state.toLowerCase()}`),
                el(
                  "p",
                  `${order.desired_units.toLocaleString()} requested · ${order.filled_units.toLocaleString()} filled · ${(order.desired_units - order.filled_units).toLocaleString()} unfilled`,
                ),
                el("small", stamp(order.created_at)),
                el(
                  "p",
                  order.note ||
                    "Waiting for a later fresh quote and modeled capacity",
                  "muted",
                ),
              );
              grid.append(n);
            }
            if (!grid.children.length)
              grid.append(
                el(
                  "p",
                  "No orders yet. The system proposes experiments when fresh source quotes arrive.",
                  "muted",
                ),
              );
            content.append(grid);
            const nav = el("div", undefined, "evidence-actions"),
              prev = el("button", "Previous orders"),
              next = el("button", "Next orders");
            prev.disabled = offset === 0;
            next.disabled = offset + 12 >= page.total;
            prev.onclick = () => {
              offset = Math.max(0, offset - 12);
              draw();
            };
            next.onclick = () => {
              offset += 12;
              draw();
            };
            nav.append(prev, el("span", `${page.total} stored orders`), next);
            content.append(nav);
          })
          .catch((error) => {
            if (content.isConnected)
              content.append(el("p", error.message, "error"));
          });
      } else if (view === "funding") {
        content.append(
          el("h2", "Virtual capital is not trading profit"),
          el(
            "p",
            "The starting deposit and every automatic top-up remain in the funding ledger. Failed trades remain in trading P/L.",
            "muted",
          ),
        );
        if (!a.funding_allowed)
          content.append(
            el(
              "p",
              "This finite account receives its starting balance once. Losses are retained and no automatic funding is added.",
              "notice",
            ),
          );
        for (const e of (a.funding_allowed ? data.funding : []).filter(
          (e) => e.kind === "VIRTUAL_FUNDING",
        )) {
          const n = el("article", undefined, "paper-pending");
          n.append(
            el("strong", money(e.amount)),
            el("p", `${stamp(e.at)} · ${e.reason}`),
          );
          content.append(n);
        }
      } else if (view === "experiments") {
        content.append(
          el("h2", `${data.experiment_total} registered hypotheses`),
          el(
            "p",
            "Seed experiments run automatically. Optionally register another size or holding-period hypothesis; the reference account never restricts research capital.",
            "muted",
          ),
        );
        const form = el("form", undefined, "evidence-actions"),
          structure = el("select"),
          budget = el("input"),
          hold = el("input"),
          cadence = el("input"),
          submit = el("button", "Register experiment", "primary"),
          result = el("p", "", "muted");
        structure.setAttribute("aria-label", "Experiment structure");
        for (const s of ["long_call", "long_put", "long_straddle"]) {
          const o = el("option", s.replaceAll("_", " "));
          o.value = s;
          structure.append(o);
        }
        budget.type = "number";
        budget.min = "0.01";
        budget.step = "any";
        budget.value = "1000000";
        budget.setAttribute("aria-label", "Target virtual debit");
        hold.type = "number";
        hold.min = "1";
        hold.value = "60";
        hold.setAttribute("aria-label", "Holding minutes");
        cadence.type = "number";
        cadence.min = "0";
        cadence.value = "30";
        cadence.setAttribute("aria-label", "Minutes between entries");
        submit.type = "submit";
        for (const [label, field] of [
          ["Structure", structure],
          ["Target virtual debit ($)", budget],
          ["Holding minutes", hold],
          ["Minutes between entries (0 = every new eligible quote)", cadence],
        ]) {
          const wrapper = el("label", label);
          wrapper.append(field);
          form.append(wrapper);
        }
        form.append(submit);
        form.onsubmit = async (e) => {
          e.preventDefault();
          submit.disabled = true;
          try {
            await api("learning/experiments", {
              structure: structure.value,
              budget: +budget.value,
              hold_minutes: +hold.value,
              repeat_minutes: +cadence.value,
            });
            data = await api("learning");
            draw();
          } catch (error) {
            result.textContent = error.message;
            submit.disabled = false;
          }
        };
        content.append(form, result);
        const grid = el("div", undefined, "paper-trade-grid");
        for (const e of data.experiments) {
          const n = el("article", undefined, "paper-trade-card");
          n.append(
            el("strong", e.configuration.structure.replaceAll("_", " ")),
            el(
              "p",
              `${money(e.configuration.target_debit)} target · ${e.configuration.hold_minutes}-minute hold · ${e.configuration.repeat_minutes || 0}-minute entry spacing`,
            ),
            el(
              "small",
              `${e.outcomes} matured outcomes · Mean net return ${percent(e.mean_net_return)}`,
            ),
          );
          if (e.configuration.parent_experiment)
            n.append(
              el(
                "small",
                `Follow-up to ${e.configuration.parent_experiment}: ${e.configuration.change}`,
              ),
            );
          grid.append(n);
        }
        content.append(grid);
        if (data.experiment_total > data.experiments.length)
          content.append(
            el(
              "p",
              `Showing ${data.experiments.length} most recent hypotheses. Older versions and results remain in the ledger.`,
              "caption",
            ),
          );
      } else {
        content.append(el("h2", "Learning check-ins"));
        if (!data.check_ins.length)
          content.append(
            el(
              "p",
              "The first check-in appears when market observations are processed. The account does not wait for an evidence grade to experiment.",
              "muted",
            ),
          );
        for (const c of data.check_ins) {
          const n = el("article", undefined, "paper-walk");
          n.append(
            el("strong", `${c.kind} · ${stamp(c.at)}`),
            el(
              "p",
              `${c.report.new_outcomes} new matured outcomes · ${c.report.underlying_day_blocks} underlying/day blocks`,
            ),
            el("p", c.report.model.status, "muted"),
            el("p", c.report.next),
            el("p", c.report.baseline || "", "muted"),
            el("p", c.report.attribution || "", "muted"),
          );
          content.append(n);
        }
        for (const type of ["Markdown", "JSON"]) {
          const b = el("button", `Download ${type} check-in`);
          b.onclick = () => {
            const text =
              type === "JSON"
                ? JSON.stringify(data, null, 2)
                : `# KESTREL PAPER Learning\n\n${stamp(new Date())}\n\n` +
                  data.accounts
                    .map(
                      (a) =>
                        `## ${a.title}\nContributed: ${money(a.contributed)}\nAdditional funding: ${money(a.additional_funding)}\nTrading P/L excluding funding: ${signed(a.trading_pnl)}\nFees: ${money(a.fees)}\n`,
                    )
                    .join("\n") +
                  `\nModel: ${data.model.status}\n${data.execution_note}\n\n` +
                  data.check_ins
                    .map(
                      (c) =>
                        `${stamp(c.at)}: ${c.report.new_outcomes} new outcomes; ${c.report.next}`,
                    )
                    .join("\n");
            const url = URL.createObjectURL(
                new Blob([text], {
                  type: type === "JSON" ? "application/json" : "text/markdown",
                }),
              ),
              a = el("a");
            a.href = url;
            a.download = `kestrel-learning.${type === "JSON" ? "json" : "md"}`;
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
          };
          content.append(b);
        }
      }
      app.append(el("p", data.execution_note, "muted caption"));
      poll();
    }
    function poll() {
      clearTimeout(timer);
      timer = setTimeout(async () => {
        if (ticket !== generation) return;
        if (
          document.querySelector("dialog[open]") ||
          ["INPUT", "SELECT", "SVG"].includes(
            document.activeElement?.tagName?.toUpperCase(),
          )
        ) {
          poll();
          return;
        }
        try {
          const next = await api("learning"),
            signature = (d) =>
              JSON.stringify([
                d.accounts.map((a) => [
                  a.trading_pnl,
                  a.contributed,
                  a.open_positions,
                  a.closed_trades,
                  a.pending_orders,
                  a.policy?.mode,
                  a.policy?.phase,
                  a.policy?.decision_at,
                  a.policy?.forecast?.policy_hash,
                ]),
                d.model.version,
                d.progress.phase,
                d.control.enabled,
                d.paper_enabled,
                d.stop,
                d.worker.error,
                d.collection,
                d.experiment_total,
                d.pending,
              ]);
          const changed = signature(data) !== signature(next);
          data = next;
          if (changed) draw();
          else poll();
        } catch (e) {
          statusLine.textContent = e.message;
          poll();
        }
      }, 15000);
    }
    draw();
  }
  async function findings(ticket) {
    const section = el("section", undefined, "paper-status learning-findings");
    section.append(
      el("h2", "Findings from PAPER Learning"),
      el(
        "p",
        "Forward experiment results are separate from unusual-activity and social-attention discovery.",
        "muted",
      ),
    );
    try {
      const data = await api("learning");
      if (ticket !== generation) return section;
      section.append(el("strong", data.model.status));
      if (data.model.champion)
        section.append(
          el(
            "p",
            `${data.model.champion.replaceAll("_", " ")} · ${data.model.test_blocks || 0} underlying/day held-out blocks · ${percent(data.model.test_net_return)} held-out mean net return. Capacity remains estimated; this does not establish a market edge.`,
            "muted",
          ),
        );
      else
        section.append(
          el(
            "p",
            "No supported learning finding yet. The separate account keeps testing with virtual capital while outcomes mature.",
            "muted",
          ),
        );
    } catch (error) {
      section.append(el("p", error.message, "error"));
    }
    section.append(
      link("Inspect experiments, funding and check-ins ↗", "#learning"),
    );
    return section;
  }
  return { show, findings };
})();
