// Disposable learning-workflows + ui-workflows fixtures. All market observations are synthetic; no real accounts.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const learningBase = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8898";
const stockBase =
  process.env.KESTREL_CHART_STOCK_URL || "http://127.0.0.1:8899";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 1000 },
      hasTouch: width === 375,
    });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await page.goto(`${learningBase}/#learning`);
    const chart = page.locator(".learning-chart");
    await chart.locator(".kc-svg").waitFor();
    assert.ok(await chart.locator(".kc-axis").count());
    await chart.locator(".kc-svg").focus();
    await chart.locator(".kc-svg").press("Home");
    const tip = chart.getByRole("tooltip");
    assert.match(
      await tip.innerText(),
      /Cash[\s\S]*Total contributed[\s\S]*Realized \/ unrealized[\s\S]*Trading fees/,
    );
    // The fixture's research book holds $2,000,000 contributed before the cap (2026-10-30).
    assert.match(await tip.innerText(), /Total contributed\s+\$2,000,000\.00/);
    const bounds = await tip.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1);
    await chart.getByLabel("Learning chart metric").selectOption("equity");
    await chart.getByRole("button", { name: "1D", exact: true }).click();
    await page
      .getByRole("button", { name: "Closed trades", exact: true })
      .click();
    assert.equal(
      await chart.getByLabel("Learning chart metric").inputValue(),
      "equity",
    );
    assert.equal(
      await chart
        .getByRole("button", { name: "1D", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    await chart.getByLabel("Trades & funding").uncheck();
    assert.equal(await chart.locator(".kc-marker").count(), 0);
    await chart.getByLabel("Trades & funding").check();
    await chart.locator(".kc-marker").first().focus();
    await chart.locator(".kc-marker").first().press("Enter");
    assert.match(
      await chart.locator(".learning-chart-event").innerText(),
      /virtual funding|opened|closed/i,
    );
    await chart.scrollIntoViewIfNeeded();
    await page.screenshot({ path: `/tmp/kestrel-chart-learning-${width}.png` });
    // Edge cases shared by every large chart: real gaps, negative values, one point, zoom and refresh inspection.
    const shared = await page.evaluate(() => {
      const points = Array.from({ length: 16 }, (_, i) => ({
        x: Date.UTC(2026, 9, 1 + i),
        at: `2026-10-${String(i + 1).padStart(2, "0")}`,
        value: i === 5 ? null : i - 8,
      }));
      const c = KestrelCharts.create({
        id: "edge-cases",
        label: "Synthetic chart verification",
        points,
        baseline: 0,
      });
      document.getElementById("app").append(c);
      return c.dataset.chart;
    });
    const edge = page.locator(`[data-chart="${shared}"]`);
    await edge.locator("svg").waitFor();
    assert.equal(
      (await edge.locator(".kc-line").getAttribute("d")).split("M").length - 1,
      2,
      "a missing value breaks the path",
    );
    await edge.locator("svg").focus();
    await edge.locator("svg").press("Home");
    assert.match(await edge.locator(".kc-value").innerText(), /-\$8\.00/);
    await edge.getByRole("button", { name: "Zoom in", exact: true }).click();
    assert.match(await edge.locator(".kc-coverage").innerText(), /Zoomed view/);
    await edge
      .getByRole("button", { name: "Reset chart view", exact: true })
      .click();
    await page.evaluate(() => {
      const c = document.querySelector('[data-chart="edge-cases"]');
      c.update({ points: [{ x: 1, at: "2026-10-01", value: 0 }] });
    });
    assert.equal(await edge.locator(".kc-observation").count(), 1);
    assert.equal(await edge.locator(".kc-value").innerText(), "$0.00");
    assert.equal(
      await edge
        .getByRole("button", { name: "Zoom in", exact: true })
        .isVisible(),
      false,
    );
    assert.doesNotMatch(await edge.innerText(), /NaN|Infinity/);
    await page.goto(`${stockBase}/#stock/SPY`);
    await page.locator(".strike-button").first().click();
    const calc = page.getByRole("dialog");
    await calc.getByText("Net entry debit", { exact: true }).waitFor();
    await calc
      .getByRole("button", { name: "Price & time", exact: true })
      .click();
    await calc.locator(".calc-heat-cell").first().waitFor();
    const cell = calc.locator(".calc-heat-cell:not(:disabled)").first(),
      label = await cell.getAttribute("aria-label");
    await cell.click();
    await page.waitForResponse(
      (r) => r.url().endsWith("/calculator") && r.status() === 200,
    );
    assert.equal(
      await calc
        .getByLabel("Scenario date (at close)", { exact: true })
        .inputValue(),
      label.slice(0, 10),
    );
    assert.equal(
      await calc
        .getByRole("button", { name: "Price & time", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    await calc.getByRole("button", { name: "P/L curve", exact: true }).click();
    await calc.locator(".payoff-chart .kc-svg").waitFor();
    await calc.locator(".payoff-chart .kc-svg").focus();
    await calc.locator(".payoff-chart .kc-svg").press("Home");
    assert.match(
      await calc.locator(".payoff-chart .kc-tooltip").innerText(),
      /Underlying[\s\S]*Scenario P\/L[\s\S]*Expiration P\/L/,
    );
    await page.screenshot({
      path: `/tmp/kestrel-chart-calculator-${width}.png`,
    });
    assert.equal(
      await page.evaluate(
        () =>
          document.querySelector("dialog").scrollWidth <=
          document.querySelector("dialog").clientWidth,
      ),
      true,
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log(
    "Charting desktop/mobile: dated Learning context, retained controls, real gaps, single point, signed values, zoom, keyboard markers, calculator price/time grid, tooltip bounds and no console errors passed.",
  );
} finally {
  await browser.close();
}
