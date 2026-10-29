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
      (await page.locator(".pl-plot .pl-line").count()) > 10,
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
    assert.match(await page.locator(".pl-axis").first().textContent(), /\$/);
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
      await page.locator(".pf-chart .pl-readout").first().innerText(),
      /1 settled/,
    );
    await page.getByLabel("Category").selectOption("likely_itm");
    await page.getByText("No play in this view has settled yet.").waitFor();
    await page.getByLabel("Category").selectOption("lottos");
    assert.match(
      await page.locator(".pf-chart .pl-readout").first().innerText(),
      /settled .* picks/,
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
      (await page.locator(".pf-calibration .pf-dot").count()) > 0,
      "calibration dots are drawn",
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
      (await page.locator(".pl-plot .pl-line").count()) >= 3,
      "one month preserves the spike even when the play expired in an earlier calendar month",
    );
    await page.getByRole("button", { name: "All", exact: true }).click();
    assert.equal(
      await page.getByLabel("Include estimated gaps").isChecked(),
      false,
    );
    assert.equal(await page.locator(".pl-line.dashed").count(), 0);
    assert.equal(
      await page.locator(".pl-chart .chart-axis span").nth(1).innerText(),
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
