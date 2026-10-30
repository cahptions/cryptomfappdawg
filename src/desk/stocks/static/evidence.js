"use strict";
const KestrelEvidence = (() => {
  async function show(ticket) {
    let data = await api("proof"),
      source = "live",
      horizon = "1m",
      healthData = null;
    if (ticket !== generation) return;
    app.className = "evidence-page";
    let timer;
    function drawHealth(target) {
      target.replaceChildren();
      if (!healthData) {
        target.append(
          el(
            "p",
            "Check saved databases and source status without stopping collection.",
            "muted",
          ),
        );
        return;
      }
      target.append(
        table(
          ["Database", "Health"],
          Object.entries(healthData.databases).map(([name, value]) => [
            name.replace("stocks-", "").replace(".db", ""),
            value.integrity === "ok"
              ? "Healthy"
              : value.status || "Needs attention",
          ]),
        ),
      );
      target.append(
        table(
          ["Source", "Status", "Last success"],
          Object.entries(healthData.source_health).map(([name, value]) => [
            name.replaceAll("_", " "),
            value.error_category
              ? value.error_category.replaceAll("_", " ")
              : "No recorded error",
            value.last_success
              ? new Date(value.last_success).toLocaleString("en-US", {
                  timeZone: "America/New_York",
                }) + " ET"
              : "Not yet collected",
          ]),
        ),
      );
      const fixes = el("div", undefined, "evidence-actions");
      fixes.append(
        link("Manage data connections ↗", "#settings"),
        link("View collection & retry controls ↗", "#system"),
      );
      target.append(fixes);
      const download = el("button", "Download health report");
      download.onclick = () => {
        const url = URL.createObjectURL(
            new Blob(
              [
                JSON.stringify(
                  {
                    health: healthData,
                    options_coverage: snapshot.options_research?.coverage,
                    prices: snapshot.price_progress,
                  },
                  null,
                  2,
                ),
              ],
              { type: "application/json" },
            ),
          ),
          a = el("a");
        a.href = url;
        a.download = "kestrel-data-health.json";
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      };
      fixes.append(download);
    }
    function draw() {
      if (ticket !== generation) return;
      const report = data.validation,
        job = data.job || {},
        hero = el("div", undefined, "explorer-heading"),
        titles = el("div"),
        actions = el("div", undefined, "evidence-actions");
      titles.append(
        el("div", "RESEARCH CHECKUP", "eyebrow"),
        el("h1", "Evidence"),
        el(
          "p",
          "What has been tested, what the results mean, and what needs more time.",
          "muted",
        ),
      );
      const run = el(
        "button",
        job.running ? "Evaluation running…" : "Refresh evaluation",
        "primary",
      );
      run.disabled = Boolean(job.running);
      // The health check lives in the Data health card, beside the results it produces.
      const health = el("button", "Check data health", "primary");
      health.type = "button";
      actions.append(run);
      if (job.running && job.owned) {
        const cancel = el(
          "button",
          job.stopping ? "Stopping…" : "Cancel evaluation",
        );
        cancel.disabled = Boolean(job.stopping);
        cancel.onclick = async () => {
          cancel.disabled = true;
          try {
            data.job = await api("proof/cancel", {});
            draw();
          } catch (error) {
            cancel.textContent = error.message;
          }
        };
        actions.append(cancel);
      }
      hero.append(titles, actions);
      app.replaceChildren(hero);
      const progress = el("section", undefined, "evidence-job");
      progress.setAttribute("role", "status");
      progress.append(
        el("strong", job.phase || "Ready to check your saved research"),
      );
      if (job.running) {
        const bar = el("progress");
        if (job.total) {
          bar.max = job.total;
          bar.value = job.completed;
        }
        bar.setAttribute("aria-label", "Evaluation progress");
        progress.append(bar);
      }
      progress.append(
        el(
          "p",
          "Runs locally on saved observations. Collection can continue. This check does not place trades or adopt new model weights.",
          "muted caption",
        ),
      );
      app.append(progress);
      const stats = el("div", undefined, "evidence-stats");
      for (const [label, value, note] of [
        [
          "Recorded research sessions",
          report?.live?.days ?? "—",
          "Forward records start when collection begins",
        ],
        [
          "Historical comparisons",
          report?.rebuild?.months ?? "—",
          "Reconstructed history is a separate record",
        ],
        [
          "Latest evaluated close",
          report?.asof || "Not evaluated",
          "Results describe stored observations",
        ],
      ]) {
        const card = el("section");
        card.append(
          el("small", label),
          el("strong", String(value)),
          el("p", note, "muted caption"),
        );
        stats.append(card);
      }
      app.append(stats);
      if (!report) {
        const first = el("section", undefined, "screen-card");
        first.append(
          el("h2", "Start with a research checkup"),
          el(
            "p",
            "Refresh evaluation builds a readable report from prices and research already saved on this laptop. If a source is missing, Check data health shows which connection needs attention. Forward results still need time for their outcomes to arrive.",
            "muted",
          ),
        );
        first.append(
          link("Review sources in Settings ↗", "#settings"),
          collectionControls(),
        );
        app.append(first);
      } else {
        const section = el("section", undefined, "screen-card"),
          controls = el("div", undefined, "screen-filters"),
          record = el("select"),
          period = el("select"),
          content = el("div");
        section.append(
          el("h2", "How the research lists performed"),
          el(
            "p",
            "Compare like with like. Recorded forward observations and reconstructed history stay separate.",
            "muted",
          ),
        );
        record.setAttribute("aria-label", "Evidence record");
        period.setAttribute("aria-label", "Evidence holding period");
        for (const [id, label] of [
          ["live", "Recorded forward observations"],
          ["rebuilt", "Historical reconstruction"],
        ]) {
          const o = el("option", label);
          o.value = id;
          record.append(o);
        }
        for (const [id, label] of [
          ["1w", "1 week"],
          ["1m", "1 month"],
          ["3m", "3 months"],
          ["12m", "12 months"],
        ]) {
          const o = el("option", label);
          o.value = id;
          period.append(o);
        }
        record.value = source;
        period.value = horizon;
        controls.append(record, period);
        section.append(controls, content);
        function results() {
          source = record.value;
          horizon = period.value;
          const rows = Object.values(report.lists || {}).map((list) => {
            const s = list[source]?.[horizon] || {};
            return [
              list.title,
              percent(s.mean_excess_spy),
              number(s.n || 0),
              number(s.picks || 0),
              !s.n
                ? "Outcomes not available yet"
                : list.grade?.source === source
                  ? list.grade.text
                  : "This record is not independently established",
            ];
          });
          content.replaceChildren(
            table(
              [
                "Research list",
                "Return vs SPY",
                "Observations",
                "Stock outcomes",
                "Evidence status",
              ],
              rows,
            ),
          );
        }
        record.onchange = results;
        period.onchange = results;
        results();
        app.append(section);
        const explain = el("section", undefined, "screen-card");
        explain.append(el("h2", "Read the result correctly"));
        for (const text of [
          "Recorded forward: lists saved at the time, assessed after their holding period ends.",
          "Historical reconstruction: what the rules would have selected from the available history. It can miss companies delisted before collection began.",
          "A positive comparison is not proof of a lasting edge. Costs, uncertainty, overlapping observations and multiple tests matter.",
          "Option volume does not reveal whether a trade opened a position or predict its direction. Missing history stays visible.",
        ])
          explain.append(el("p", text, "muted"));
        app.append(explain);
      }
      const healthSection = el("section", undefined, "screen-card");
      healthSection.id = "evidence-health";
      const healthHead = el("div", undefined, "section-heading");
      healthHead.append(el("h2", "Data health"), health);
      healthSection.append(healthHead);
      const healthOutput = el("div");
      drawHealth(healthOutput);
      healthSection.append(healthOutput);
      app.append(healthSection);
      health.onclick = async () => {
        health.disabled = true;
        health.textContent = "Checking…";
        try {
          healthData = await api("proof/health");
          drawHealth(healthOutput);
          healthSection.scrollIntoView({ behavior: "smooth" });
        } catch (error) {
          healthOutput.replaceChildren(el("p", error.message, "error"));
        } finally {
          health.disabled = false;
          health.textContent = "Check data health";
        }
      };
      run.onclick = async () => {
        run.disabled = true;
        run.textContent = "Starting…";
        try {
          data.job = await api("proof/start", {});
          draw();
        } catch (error) {
          progress.replaceChildren(el("p", error.message, "error"));
          run.disabled = false;
          run.textContent = "Refresh evaluation";
        }
      };
      if (timer) clearTimeout(timer);
      if (job.running)
        timer = setTimeout(async () => {
          if (ticket !== generation) return;
          try {
            data = await api("proof");
            draw();
          } catch (error) {
            progress.append(el("p", error.message, "error"));
          }
        }, 2000);
    }
    draw();
  }
  return { show };
})();
