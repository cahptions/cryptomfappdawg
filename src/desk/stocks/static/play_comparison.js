"use strict";

// Shortlisting is browser-local. Opening the comparison only reads frozen ideas and current PAPER cash.
const KestrelPlayComparison = (() => {
  const key = "kestrel-play-shortlist-v1",
    limit = 3;
  let picks = [],
    move = 0,
    dialog;
  try {
    const saved = JSON.parse(localStorage.getItem(key));
    if (Array.isArray(saved))
      picks = saved
        .filter(
          (x) => /^[a-f0-9]{32}$/.test(x?.id) && typeof x.label === "string",
        )
        .slice(0, limit);
  } catch {
    /* unavailable browser storage */
  }

  const label = (p) =>
    `${p.symbol} ${p.type} · ${[...new Set(p.legs.map((leg) => leg.strike))].join("/")} · ${p.expiration}`;
  const pct = (v) =>
    v == null ? "unavailable" : `${Number((v * 100).toFixed(1))}%`;
  const time = (v) =>
    v
      ? new Date(v).toLocaleString("en-US", {
          timeZone: "America/New_York",
          month: "short",
          day: "numeric",
          year: "numeric",
          hour: "numeric",
          minute: "2-digit",
        }) + " ET"
      : "unavailable";
  const signedMoney = (v) => (v > 0 ? "+" : "") + money(v);

  function updateControls(root = document) {
    const count = picks.length;
    for (const b of root.querySelectorAll("[data-shortlist-id]")) {
      const added = picks.some((p) => p.id === b.dataset.shortlistId);
      b.setAttribute("aria-pressed", String(added));
      b.disabled = !added && count >= limit;
      b.textContent = added
        ? "Shortlisted"
        : count >= limit
          ? "Shortlist full"
          : "Shortlist";
      b.setAttribute(
        "aria-label",
        `${added ? "Remove" : "Shortlist"} ${b.dataset.playLabel}`,
      );
    }
    for (const b of root.querySelectorAll("[data-open-shortlist]")) {
      b.textContent = `Compare ideas (${count}/${limit})`;
      b.hidden = !count;
    }
  }

  function save() {
    try {
      localStorage.setItem(key, JSON.stringify(picks));
    } catch {
      /* keep in memory */
    }
    updateControls();
  }

  function openButton() {
    const b = el(
      "button",
      `Compare ideas (${picks.length}/${limit})`,
      "shortlist-open",
    );
    b.type = "button";
    b.dataset.openShortlist = "";
    b.hidden = !picks.length;
    b.onclick = open;
    return b;
  }

  function controls(play) {
    const wrap = el("span", undefined, "shortlist-actions");
    if (!play.idea_id || !play.legs?.length) return wrap;
    const b = el("button", undefined, "shortlist-pin");
    b.type = "button";
    b.dataset.shortlistId = play.idea_id;
    b.dataset.playLabel = label(play);
    b.onclick = () => {
      if (picks.some((p) => p.id === play.idea_id))
        picks = picks.filter((p) => p.id !== play.idea_id);
      else if (picks.length < limit)
        picks.push({ id: play.idea_id, label: b.dataset.playLabel });
      save();
    };
    wrap.append(b, openButton());
    updateControls(wrap);
    return wrap;
  }

  function accountFit(a) {
    if (a.pending) return `${money(a.cash)} cash · another entry pending`;
    if (!a.cash_units) return `${money(a.cash)} cash · one unit exceeds cash`;
    const depth =
      a.quoted_units == null
        ? "quote capacity unknown"
        : `${a.quoted_units} supported by the saved quote`;
    return `${money(a.cash)} cash · ${a.cash_units} whole under sizing · ${depth}${a.over_target ? " · one exceeds the 25% target" : ""}`;
  }

  function evidence(card, data) {
    const view = data.comparison,
      history = view.prior_outcomes;
    const info = el("dl", undefined, "shortlist-facts");
    const fact = (name, value) => info.append(el("dt", name), el("dd", value));
    fact(
      "Saved entry",
      `${money(view.entry)} ${view.action === "SELL" ? "credit" : "debit"} per share · ${view.source || "source unknown"} · ${time(view.identified_at)}`,
    );
    fact(
      "Quote evidence",
      data.status === "supported"
        ? `${data.capacity} whole at the recorded price · quoted ${time(data.quoted_at)}`
        : `${data.status === "unsupported" ? "Unsupported" : "Unknown"} · ${data.why}`,
    );
    fact("Maximum loss", `${money(view.max_loss)} per unit`);
    for (const a of data.accounts || []) fact(a.label, accountFit(a));
    fact("Estimated take-profit chance", pct(view.model_take_profit));
    fact(
      "Observed take-profit record",
      history?.plays
        ? `${pct(history.reached)} of ${number(history.plays)} graded ideas reached their target · ${history.level}, ${history.bucket} estimate band`
        : history
          ? "No graded ideas in this estimate band"
          : "Unavailable at identification",
    );
    card.append(info);
  }

  function open() {
    if (!picks.length || dialog?.open) return;
    const box = el("dialog", undefined, "shortlist-dialog");
    dialog = box;
    const heading = el("h2", "Compare shortlisted ideas"),
      close = el("button", "Back to ideas"),
      head = el("div", undefined, "shortlist-heading"),
      slider = el("input"),
      sliderLabel = el("label"),
      range = el("div", undefined, "shortlist-scenario"),
      grid = el("div", undefined, "shortlist-grid"),
      value = el("output");
    heading.id = "shortlist-heading";
    box.setAttribute("aria-labelledby", heading.id);
    close.type = "button";
    close.onclick = () => box.close();
    head.append(heading, close);
    Object.assign(slider, {
      id: "shortlist-move",
      type: "range",
      min: "-20",
      max: "20",
      step: "1",
      value: String(move),
    });
    sliderLabel.htmlFor = slider.id;
    sliderLabel.textContent = "Stock move at expiry";
    value.setAttribute("for", slider.id);
    const paint = new Map();
    function updateMove() {
      move = Number(slider.value);
      value.textContent = `${move > 0 ? "+" : ""}${move}%`;
      slider.setAttribute("aria-valuetext", value.textContent);
      for (const draw of paint.values()) draw();
    }
    slider.oninput = updateMove;
    updateMove();
    range.append(sliderLabel, value, slider);
    box.append(
      head,
      range,
      el(
        "p",
        "Hold cash: $0.00 change. Each stock moves from its own recorded price by the selected percentage at its own expiry. Each result assumes one unit at the saved entry, intrinsic settlement and entry fees. Early exits and assignment can differ.",
        "shortlist-assumptions",
      ),
      grid,
    );
    box.onclose = () => {
      box.remove();
      if (dialog === box) dialog = null;
    };
    document.body.append(box);
    box.showModal();

    for (const pick of picks) {
      const card = el("article", undefined, "shortlist-card"),
        title = el("h3", pick.label),
        body = el("div"),
        remove = el("button", "Remove"),
        footer = el("div", undefined, "shortlist-footer");
      remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${pick.label} from comparison`);
      remove.onclick = () => {
        picks = picks.filter((p) => p.id !== pick.id);
        save();
        paint.delete(pick.id);
        card.remove();
        if (!picks.length) box.close();
        else close.focus();
      };
      footer.append(link("Idea details ↗", `#plays/idea/${pick.id}`), remove);
      footer.firstChild.onclick = () => box.close();
      body.setAttribute("aria-busy", "true");
      card.append(title, body, footer);
      grid.append(card);
      async function load() {
        body.setAttribute("aria-busy", "true");
        body.textContent = "Reading saved evidence…";
        try {
          const data = await api(
            `plays/idea/${encodeURIComponent(pick.id)}/execution?compare=true`,
          );
          if (!card.isConnected) return;
          const v = data.comparison,
            s = v.scenario;
          body.replaceChildren();
          if (s) {
            const pnl = el("strong", undefined, "shortlist-pnl"),
              price = el("p");
            const draw = () => {
              const amount = s.pnl[move - s.min_move];
              pnl.textContent = `${signedMoney(amount)} at expiry`;
              price.textContent = `${money(v.spot * (1 + move / 100))} stock · ${pct(amount / v.max_loss)} of risk`;
            };
            paint.set(pick.id, draw);
            draw();
            body.append(
              pnl,
              price,
              el(
                "p",
                s.breakevens.length
                  ? `Break-even at expiry: ${s.breakevens.map(money).join(" / ")}`
                  : "No break-even within positive stock prices.",
              ),
            );
          } else
            body.append(
              el("strong", "Expiry scenario unavailable for these legs"),
            );
          evidence(body, data);
        } catch (error) {
          const retry = el("button", "Retry");
          retry.type = "button";
          retry.onclick = load;
          body.replaceChildren(el("p", error.message), retry);
        } finally {
          body.setAttribute("aria-busy", "false");
        }
      }
      void load();
    }
  }
  return { controls, openButton };
})();
