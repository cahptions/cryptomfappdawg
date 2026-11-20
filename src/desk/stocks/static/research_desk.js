"use strict";

const KestrelResearchDesk = (() => {
  function books(values) {
    const grid = el("div", undefined, "paper-metrics learning-hero-metrics");
    for (const book of values) {
      const card = el("article", undefined, "paper-status");
      card.append(
        el("h2", book.title),
        el("p", book.phase),
        link("Open PAPER account →", `#learning/${book.id}`),
      );
      if (book.decision_id)
        card.append(
          el("p", undefined, "caption"),
          link(
            "Read the stored decision →",
            `#learning/${book.id}/${book.decision_id}`,
          ),
        );
      grid.append(card);
    }
    return grid;
  }

  function showStudy(study) {
    const box = el("section", undefined, "paper-status");
    box.append(
      el("h2", study.title),
      el("p", study.hypothesis),
      el("strong", study.status),
      el(
        "p",
        `${study.recorded} of ${study.scheduled} scheduled blocks recorded · ${study.counts.graded || 0} graded · ${study.counts.pending || 0} pending · ${study.counts.abstained || 0} abstained, including ${study.counts.missed || 0} missed windows · ${study.counts.inconclusive || 0} inconclusive.`,
      ),
    );
    if (study.comparison.mean_excess_bps != null)
      box.append(
        el(
          "p",
          `${study.comparison.mean_excess_bps.toFixed(1)} bp mean paired stock price return difference across ${study.comparison.graded_pairs} graded blocks.`,
        ),
      );
    if (study.comparison.one_sided_p != null)
      box.append(
        el(
          "p",
          `Preregistered one-sided p-value ${study.comparison.one_sided_p.toFixed(4)}.`,
        ),
      );
    const cases = el("ul");
    for (const item of study.recent) {
      const result = item.result || item.decision;
      cases.append(
        el(
          "li",
          [
            item.session,
            item.challenger && item.comparator
              ? `${item.challenger} versus ${item.comparator}`
              : null,
            result,
            item.outcome_note || item.reason,
          ]
            .filter(Boolean)
            .join(" · "),
        ),
      );
    }
    if (cases.children.length) box.append(el("h3", "Recent cases"), cases);
    const rules = el("details");
    rules.append(
      el("summary", "Protocol and limitations"),
      el("p", `Protocol ${study.version}`),
    );
    for (const label of [
      "counterhypothesis",
      "window",
      "universe",
      "signal",
      "matched",
      "choice",
      "outcome",
      "adjudication",
      "inference",
    ])
      rules.append(el("p", study.protocol[label]));
    for (const limit of study.limitations)
      rules.append(el("p", limit, "muted"));
    box.append(rules);
    return box;
  }

  function showQuoteTrial(trial) {
    const box = el("section", undefined, "paper-status");
    box.append(el("h2", "Where should KESTREL collect next?"));
    box.append(
      el(
        "p",
        trial.enabled
          ? "The worker can use one existing rotation slot to check a play near the cash limit. On comparable sessions it keeps ordinary rotation as the control."
          : "The discretionary quote comparison is paused.",
      ),
    );
    const rows = Object.entries(trial.arms).map(([name, arm]) => [
      name === "follow_up" ? "Cash-limit follow-up" : "Ordinary rotation",
      arm.assigned,
      arm.usable,
      arm.unknown,
      arm.provider_calls,
      arm.seconds.toFixed(1),
      Math.round(arm.retained_bytes / 1024),
    ]);
    box.append(
      table(
        [
          "Allocation",
          "Assigned",
          "Usable",
          "Interrupted",
          "Provider calls",
          "Seconds",
          "Stored KiB",
        ],
        rows,
      ),
    );
    box.append(
      el(
        "p",
        "Usable means at least four eligible same-session option quotes. Provider calls count the app's operations. Stored bytes describe retained contracts from that collection window.",
        "muted",
      ),
    );
    if (trial.latest) {
      const t = trial.latest;
      box.append(
        el(
          "p",
          `${t.session} · ${t.assigned === "follow_up" ? t.follow_up : t.rotation} · ${t.result?.status || "Awaiting a recorded result"}`,
        ),
        link(
          "What prompted this follow-up? →",
          `#learning/${t.account}/${t.decision_id}`,
        ),
      );
    }
    return box;
  }

  async function show(ticket) {
    const data = await api("research/desk");
    if (ticket !== generation) return;
    app.className = "paper-page learning-page";
    const iv = showStudy(data.iv_study),
      quotes = showQuoteTrial(data.quote_trial);
    function control(section, study, enabled, subject) {
      const button = el("button", `${enabled ? "Pause" : "Start"} ${subject}`);
      const message = el("p", undefined, "muted");
      message.setAttribute("role", "status");
      button.onclick = async () => {
        button.disabled = true;
        try {
          await api(`research/studies/${study}`, { enabled: !enabled });
          if (ticket === generation) await show(ticket);
        } catch (error) {
          message.textContent = error.message;
          button.disabled = false;
        }
      };
      section.append(button, message);
    }
    control(
      iv,
      "iv_forward_study",
      data.iv_study.enabled,
      "forward enrollment",
    );
    control(
      quotes,
      "quote_research_trial",
      data.quote_trial.enabled,
      "quote comparison",
    );
    app.replaceChildren(
      el("h1", "Research desk"),
      books(data.books),
      iv,
      quotes,
    );
  }
  return { show };
})();
