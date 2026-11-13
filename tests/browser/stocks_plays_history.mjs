// Plays history and the per-play P/L chart, at desktop and phone widths, against the synthetic `plays-history`
// fixture (six sessions of picks, one laptop-off session repaired). Never an operator's data or credentials.
//   uv run python -m tests.browser.stocks_fixture_server --scenario plays-history --port 8878
//   KESTREL_STOCKS_URL=http://127.0.0.1:8878 node tests/browser/stocks_plays_history.mjs
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8878";
const out =
  process.env.KESTREL_BROWSER_OUTPUT ?? "/tmp/kestrel-browser-results";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    const overflow = () =>
      page.evaluate(
        () => document.scrollingElement.scrollWidth - window.innerWidth,
      );

    // the board links to the history
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    await page
      .getByRole("navigation", { name: "Plays views" })
      .getByRole("link", { name: "History" })
      .click();
    await page.getByRole("heading", { name: /^Plays of / }).waitFor();
    assert.equal(
      await page
        .locator('nav[aria-label="Main"] a[href="#plays"]')
        .getAttribute("aria-current"),
      "page",
      "the main navigation keeps Plays selected",
    );

    // quick buttons, the date picker and the session summary
    for (const label of [
      "Today",
      "Yesterday",
      "1 week",
      "1 month",
      "3 months",
      "1 year",
    ])
      await page.getByRole("link", { name: label, exact: true }).waitFor();
    const date = page.getByLabel("Show the plays of a recorded session");
    assert.match(await date.inputValue(), /^\d{4}-\d{2}-\d{2}$/);
    const summary = page.locator(".pl-summary").first();
    assert.match(await summary.innerText(), /\$100 in each/);
    assert.match(await summary.innerText(), /Random twins/);
    const rows = page.locator("a.pl-row");
    assert.ok((await rows.count()) > 5, "the latest session lists its plays");
    assert.match(await rows.first().innerText(), /[+−]?\d+\.\d%/);
    assert.ok(
      (await page.locator("a.pl-row .pl-spark svg").count()) > 0,
      "rows carry sparklines",
    );
    assert.ok((await overflow()) <= 1, `no horizontal scroll at ${width}px`);
    await page.screenshot({
      path: `${out}/plays-history-${width}.png`,
      fullPage: true,
    });

    // section filter and order: the summary follows the section, and the choice survives a reload
    const sectionPick = page.getByLabel("Section", { exact: true });
    const orderPick = page.getByLabel("Order", { exact: true });
    const sectionTexts = await sectionPick.locator("option").allTextContents();
    assert.match(sectionTexts[0], /^All sections \(\d+\)$/);
    for (const title of [
      "Most likely to finish ITM",
      "Biggest model edge",
      "Unusual activity",
      "Cheap volatility",
      "Earnings plays",
      "Strangles",
      "Trend calls",
      "Bearish puts",
      "Squeeze calls",
      "Debit spreads",
      "Sell rich premium",
      "Iron condors",
      "Iron butterflies",
      "Cash-secured puts",
      "Lottos",
      "Your watchlist",
      "LEAPS (older recorded LEAPS)",
    ])
      assert.ok(
        sectionTexts.some((t) => t.includes(title)),
        `the section list offers ${title}`,
      );
    const allRows = await rows.count();
    const busiest = (
      await sectionPick.locator("option").evaluateAll((os) =>
        os
          .slice(1)
          .map((o) => [o.value, Number(o.textContent.match(/\((\d+)\)$/)[1])])
          .sort((a, b) => b[1] - a[1]),
      )
    )[0];
    const allSummary = await summary.innerText();
    await sectionPick.selectOption(busiest[0]);
    await page
      .locator(".pl-summary h3", { hasText: "This session · " })
      .waitFor();
    assert.equal(await page.locator("section.pl-category").count(), 1);
    assert.ok(
      (await rows.count()) < allRows || busiest[1] === allRows,
      "only the chosen section is listed",
    );
    const counted = (text) => Number(text.match(/% of (\d+)/)[1]);
    assert.ok(
      counted(await summary.innerText()) <= busiest[1] &&
        counted(await summary.innerText()) < counted(allSummary),
      "the summary counts only the chosen section",
    );
    await orderPick.selectOption("best");
    await page.waitForFunction(() => {
      const values = [...document.querySelectorAll("a.pl-row .pl-now strong")]
        .map((n) => n.textContent.replace("−", "-").replace("%", ""))
        .filter((t) => t !== "—")
        .map(Number);
      return values.every((v, i) => i === 0 || values[i - 1] >= v);
    });
    await page.reload();
    await page.getByRole("heading", { name: /^Plays of / }).waitFor();
    assert.equal(await sectionPick.inputValue(), busiest[0]);
    assert.equal(await orderPick.inputValue(), "best");
    assert.equal(await page.locator("section.pl-category").count(), 1);
    for (const range of ["1 week", "1 year"]) {
      await page.getByRole("link", { name: range, exact: true }).click();
      await page.waitForFunction(
        () => document.querySelectorAll("section.pl-category").length <= 1,
      );
      assert.equal(await sectionPick.inputValue(), busiest[0]);
    }
    await page.getByRole("button", { name: "Closing snapshot" }).click();
    await page
      .getByRole("button", { name: "Closing snapshot", pressed: true })
      .waitFor();
    assert.equal(await sectionPick.inputValue(), busiest[0]);
    assert.ok((await page.locator("section.pl-category").count()) <= 1);
    await sectionPick.selectOption("leaps");
    await page.getByText(/No LEAPS \(older recorded LEAPS\) plays/).waitFor();
    assert.ok((await overflow()) <= 1, `section filters fit at ${width}px`);
    if (width === 375) {
      const boxes = await page
        .locator(".pl-history-filter")
        .evaluateAll((ls) => ls.map((l) => l.getBoundingClientRect().left));
      assert.equal(boxes[0], boxes[1], "the phone shows one filter per line");
    }
    await page.screenshot({
      path: `${out}/plays-history-section-${width}.png`,
      fullPage: true,
    });
    // back to the default view for the rest of the walk
    await sectionPick.selectOption("all");
    await page.getByRole("button", { name: "All identified plays" }).click();
    await orderPick.selectOption("rank");
    await page.getByRole("link", { name: "Today", exact: true }).click();
    await page.waitForFunction(
      () => document.querySelectorAll("section.pl-category").length > 1,
    );

    // the oldest session: a pick that lived through the laptop-off session
    await date.fill((await date.getAttribute("min")) || "");
    await date.dispatchEvent("change");
    await page.waitForFunction(() => location.hash.match(/history\/\d{4}/));
    await page.getByRole("heading", { name: /^Plays of / }).waitFor();
    const settled = page.locator("a.pl-row", {
      hasText: /Finished in the money|Expired worthless/,
    });
    assert.ok((await settled.count()) > 0, "an expired pick shows its result");
    const open = page.locator("a.pl-row", { hasText: /Open/ }).first();
    await open.click();
    await page.locator(".pl-plot svg").waitFor();
    const detailUrl = page.url();
    await page.getByRole("button", { name: "All" }).click();
    assert.ok(
      (await page.locator(".pl-plot .pl-line").first().getAttribute("d")).split(
        "L",
      ).length > 10,
      "All retains the intraday path instead of reducing it to a few daily closes",
    );
    await page
      .getByRole("heading", { name: "Best observed exit before expiry" })
      .waitFor();
    await page.getByLabel("Include estimated gaps").check();
    assert.ok(
      (await page.locator(".pl-plot .pl-line.dashed").count()) > 0,
      "the laptop-off session is drawn dashed",
    );
    assert.match(
      await page.locator(".pl-chart > .caption.muted").innerText(),
      /Dashed: modelled or estimated/,
    );
    assert.equal(
      await page.getByRole("button", { name: "When first shown" }).isDisabled(),
      true,
      "no first sighting was recorded for this fixture",
    );
    // the crosshair readout follows the pointer
    const chart = page.locator(".pl-plot svg");
    // the gaps toggle redraws the chart (and a new scrollbar can resize it once): act on the settled chart
    await page.waitForFunction(
      () =>
        new Promise((done) => {
          const svg = document.querySelector(".pl-plot svg");
          setTimeout(
            () => done(svg && svg === document.querySelector(".pl-plot svg")),
            1500,
          );
        }),
    );
    await chart.scrollIntoViewIfNeeded();
    const box = await chart.boundingBox();
    const before = await page.locator(".pl-readout").innerText();
    await page.mouse.move(box.x + box.width * 0.15, box.y + box.height / 2);
    const after = await page.locator(".pl-readout").innerText();
    assert.match(after, /P\/L/);
    assert.notEqual(before, after, "moving across the chart inspects marks");
    // ranges and units
    await page.getByRole("button", { name: "1D" }).click();
    assert.match(await page.locator(".pl-readout").innerText(), /\d:\d\d/);
    await page.getByRole("button", { name: "$", exact: true }).click();
    assert.match(
      await page
        .locator(".pl-chart .kc-axis:not(.kc-time-axis)")
        .first()
        .textContent(),
      /\$/,
    );
    await page.getByRole("button", { name: "1M" }).click();
    await page.getByRole("button", { name: "%", exact: true }).click();
    // greeks then and now, why it was picked, every mark
    await page.getByRole("heading", { name: "Greeks then and now" }).waitFor();
    await page.getByRole("heading", { name: "Why it was picked" }).waitFor();
    await page.getByText(/^Every daily mark/).click();
    assert.ok(
      (await page.locator(".pl-marks tbody tr").count()) >= 5,
      "the daily marks are listed",
    );
    assert.ok((await overflow()) <= 1, `no horizontal scroll at ${width}px`);
    await page.screenshot({
      path: `${out}/plays-play-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("link", { name: /← All plays of/ }).click();
    await page.getByRole("heading", { name: /^Plays of / }).waitFor();

    // performance across sessions
    await page
      .getByRole("navigation", { name: "Plays views" })
      .getByRole("link", { name: "Performance" })
      .click();
    await page.getByRole("heading", { name: "Performance" }).waitFor();
    await page.getByRole("heading", { name: "By holding period" }).waitFor();
    await page.getByRole("heading", { name: "$100 into every play" }).waitFor();
    assert.equal(
      await page.locator(".pf-chart .pf-line").count(),
      3,
      "picks, random twins and SPY",
    );
    assert.match(
      await page.locator(".pf-chart .kc-readout").first().innerText(),
      /Settled plays 1/,
    );
    await page.getByLabel("Category").selectOption("likely_itm");
    await page.getByText("No play in this view has settled yet.").waitFor();
    await page.getByLabel("Category").selectOption("lottos");
    assert.match(
      await page.locator(".pf-chart .kc-readout").first().innerText(),
      /Settled plays .*Picks/,
    );
    await page.getByRole("heading", { name: "By category" }).waitFor();
    assert.match(
      await page.locator(".pf-section table").nth(1).innerText(),
      /current rules/,
    );
    await page
      .getByRole("heading", { name: "Predicted chance vs what happened" })
      .waitFor();
    assert.ok(
      (await page
        .locator('[data-chart="calibration"] .kc-observation')
        .count()) > 0,
      "calibration dots are drawn",
    );
    // the section replay on real past prices: its own section and calibration, labelled replay
    await page
      .getByRole("heading", {
        name: "Replay on real past prices — not forward results",
      })
      .waitFor();
    assert.match(
      await page.locator(".pf-replay table").first().innerText(),
      /Biggest model edge[\s\S]*Iron condors/,
    );
    assert.match(
      await page.locator(".pf-replay").innerText(),
      /Not replayed \(8 sections\)/,
    );
    await page
      .getByRole("heading", { name: /^Replay calibration on past prices/ })
      .waitFor();
    assert.ok(
      (await page
        .locator('[data-chart="replay-calibration"] .kc-observation')
        .count()) > 0,
      "replay calibration dots are drawn",
    );
    await page
      .getByRole("heading", { name: "By days to expiry when picked" })
      .waitFor();
    await page.getByRole("heading", { name: "What it has learned" }).waitFor();
    await page
      .getByRole("heading", { name: "Exit rules (walk-forward)" })
      .waitFor();
    await page
      .getByRole("heading", { name: "Challengers in shadow" })
      .waitFor();
    assert.match(
      await page.locator(".pf-label.unproven").first().innerText(),
      /Not enough evidence yet/,
    );
    assert.ok((await overflow()) <= 1, `no horizontal scroll at ${width}px`);
    await page.screenshot({
      path: `${out}/plays-performance-${width}.png`,
      fullPage: true,
    });

    // Explicit synthetic counterexample: a profitable quoted spike, a higher unobserved estimate, and a worthless
    // expiry. The estimated high and the expiry result must never hide or masquerade as the earlier quoted exit.
    let onlyEstimates = false;
    await page.route("**/api/stocks/plays/play?*", async (route) => {
      const response = await route.fetch(),
        data = await response.json();
      const point = (t, value, status) => {
        const pnl =
          Math.round(
            ((value - 2) * 100 - (status === "settled" ? 0.65 : 1.3)) * 100,
          ) / 100;
        return {
          t,
          at: t.length === 10 ? `${t}T16:00:00-04:00` : t,
          value,
          status,
          underlying: 100,
          close: { pnl, pct: pnl / 200.65 },
        };
      };
      const spike = point("2026-10-13T11:00:00-04:00", 4, "quoted"),
        estimate = point("2026-10-14", 30, "estimated"),
        settled = point("2026-10-16", 0, "settled");
      const snapshot = (p) => ({ ...p, ...p.close });
      data.session = "2026-10-12";
      data.expiration = data.settles = "2026-10-16";
      data.expired = true;
      data.bases = { close: 2 };
      data.first_seen = null;
      data.outcome = {
        hit: false,
        payoff: 0,
        net_return: -1,
        underlying_close: 90,
      };
      data.legs = [{ side: "buy", kind: "call", strike: 100 }];
      data.daily = [point("2026-10-12", 1.8, "quoted"), estimate, settled];
      data.intraday = [
        point("2026-10-13T10:00:00-04:00", 1.5, "quoted"),
        spike,
        point("2026-10-13T15:55:00-04:00", 1, "quoted"),
      ];
      data.exits = {
        close: {
          best: snapshot(spike),
          latest: snapshot(settled),
          best_estimate: snapshot(estimate),
          giveback: 399.35,
          observations: 3,
          estimates: 1,
        },
      };
      if (onlyEstimates) {
        data.expired = false;
        data.outcome = null;
        data.daily = [estimate];
        data.intraday = [];
        data.exits.close.best = null;
        data.exits.close.latest = snapshot(estimate);
        data.exits.close.giveback = null;
      }
      await route.fulfill({ json: data });
    });
    await page.goto(detailUrl);
    const bestCard = page.locator(".pl-best-exit");
    await bestCard.getByText("+$198.70 (+99.0%)", { exact: true }).waitFor();
    await page.getByLabel("Contracts", { exact: true }).fill("3");
    await bestCard.getByText("+$596.10 (+99.0%)", { exact: true }).waitFor();
    assert.match(
      await page.locator(".pl-latest-exit > strong").innerText(),
      /601\.95/,
    );
    assert.match(await page.locator(".pl-peak-label").textContent(), /596\.10/);
    await page.getByRole("button", { name: "Show best exit on chart" }).click();
    assert.match(
      await page.locator(".pl-selected-exit > strong").innerText(),
      /596\.10/,
    );
    assert.match(
      await page.locator(".pl-selected-exit p").innerText(),
      /11:00 AM ET/,
    );
    await page.getByLabel("Contracts", { exact: true }).fill("4");
    assert.match(
      await page.locator(".pl-selected-exit > strong").innerText(),
      /794\.80/,
      "changing quantity preserves the inspected exit time",
    );
    await page.getByLabel("Contracts", { exact: true }).fill("3");
    await page.getByRole("button", { name: "1M", exact: true }).click();
    assert.ok(
      (await page.locator(".pl-plot .pl-line").first().getAttribute("d")).split(
        "L",
      ).length >= 3,
      "one month preserves the spike even when the play expired in an earlier calendar month",
    );
    await page.getByRole("button", { name: "All", exact: true }).click();
    assert.equal(
      await page.getByLabel("Include estimated gaps").isChecked(),
      false,
    );
    assert.equal(await page.locator(".pl-line.dashed").count(), 0);
    assert.equal(
      await page.locator(".pl-chart .kc-time-axis").nth(1).textContent(),
      "Oct 14",
      "the midpoint label follows elapsed time, not the density of captured quotes",
    );
    await page.mouse.move(0, 0);
    await page.locator(".pl-plot svg").hover();
    await page.locator(".pl-tooltip").waitFor({ state: "visible" });
    assert.match(
      await page.locator(".pl-tooltip").innerText(),
      /recorded quote/,
    );
    assert.ok((await overflow()) <= 1, `exit controls fit at ${width}px`);
    await page.screenshot({
      path: `${out}/plays-exit-opportunity-${width}.png`,
      fullPage: true,
    });
    onlyEstimates = true;
    await page.reload();
    await page.getByText(/^Only estimated values are available/).waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Show best exit on chart" })
        .isDisabled(),
      true,
    );
    await page.getByLabel("Include estimated gaps").check();
    await page.locator(".pl-plot svg").waitFor();
    assert.match(
      await page.locator(".pl-selected-exit h3").innerText(),
      /Estimated/,
    );
    assert.ok(
      (await page
        .locator(".pl-selected-exit > .pl-estimated-value")
        .count()) === 1,
    );
    await page.close();
  }
  assert.deepEqual(errors, [], "no page errors");
  console.log("plays history browser checks passed");
} finally {
  await browser.close();
}
