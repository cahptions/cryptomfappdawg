// The options profit calculator against --scenario ui-workflows: templates, strategy naming, stats, the price × date
// table in $ and %, the price range and the strike stepper. All quotes are synthetic fixtures.
//   uv run python -m tests.browser.stocks_fixture_server --scenario ui-workflows --port 8879
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8879";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
const calculated = (page) =>
  page.waitForResponse(
    (r) => r.url().endsWith("/calculator") && r.status() === 200,
  );
try {
  for (const width of [1440, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() === "error")
        errors.push(`${m.text()} (${m.location().url})`);
    });
    await page.goto(`${base}/#stock/SPY`);
    await page.locator(".strike-button").first().click();
    const calc = page.getByRole("dialog");
    await calc.getByText("Net entry debit", { exact: true }).waitFor();

    // Stats strip: the popular calculators' summary, with the options-implied chance of profit.
    const stats = calc.locator(".calc-stats");
    for (const label of ["Max profit", "Max loss", "Chance of profit"])
      await stats.getByText(label, { exact: true }).waitFor();
    assert.match(
      await stats.locator(".calc-stat.chance strong").innerText(),
      /^\d{1,3}%$/,
    );
    assert.equal(await calc.locator(".calc-strategy").innerText(), "Long call");

    // A template builds the legs from saved strikes and names the strategy.
    await calc.getByText("Strategies", { exact: true }).click();
    const answer = calculated(page);
    await calc
      .getByRole("button", { name: "Long straddle", exact: true })
      .click();
    await answer;
    assert.equal(await calc.locator(".calc-leg").count(), 2);
    assert.equal(
      await calc.locator(".calc-strategy").innerText(),
      "Long straddle",
    );
    assert.equal(await calc.locator(".calc-pill").count(), 2);
    await stats.getByText("Break-evens", { exact: true }).waitFor();
    assert.equal(
      await stats.locator(".calc-stat.up strong").innerText(),
      "Unlimited",
    );

    // The strike stepper moves one saved strike at a time.
    const strike = calc.getByLabel("Strike", { exact: true }).first(),
      before = await strike.inputValue();
    const stepped = calculated(page);
    await calc
      .getByRole("button", { name: "Higher strike, leg 1", exact: true })
      .click();
    await stepped;
    assert.notEqual(await strike.inputValue(), before);
    assert.match(await calc.locator(".calc-strategy").innerText(), /strangle/);

    // Price × date table: $ by default, % of the amount at risk on request; cells set the scenario.
    await calc
      .getByRole("button", { name: "Price & time", exact: true })
      .click();
    const cell = calc.locator(".calc-heat-cell:not(:disabled)").first();
    await cell.waitFor();
    assert.match(await cell.innerText(), /\$/);
    await calc.getByRole("button", { name: "%", exact: true }).click();
    assert.match(
      await calc.locator(".calc-heat-cell:not(:disabled)").first().innerText(),
      /%$/,
    );
    const narrower = calculated(page);
    await calc.getByLabel("Price range around today's price (%)").fill("10");
    await narrower;
    await page.waitForFunction(() =>
      /\+10\.0%/.test(
        document.querySelectorAll(".calc-heat-price small")[0]?.textContent,
      ),
    );
    const target = calc.locator(".calc-heat-cell:not(:disabled)").nth(3),
      label = await target.getAttribute("aria-label");
    const chosen = calculated(page);
    await target.click();
    await chosen;
    assert.equal(
      await calc
        .getByLabel("Scenario date (at close)", { exact: true })
        .inputValue(),
      label.slice(0, 10),
    );
    await page.waitForFunction(() =>
      document
        .querySelector(".calc-live")
        .textContent.includes("Scenario P/L after fees"),
    );

    // Greeks for the position, and no sideways scrolling.
    await calc.getByRole("heading", { name: "Greeks", exact: true }).waitFor();
    assert.equal(
      await page.evaluate(() => {
        const d = document.querySelector("dialog");
        return d.scrollWidth <= d.clientWidth;
      }),
      true,
    );
    await page.screenshot({ path: `/tmp/kestrel-calculator-v2-${width}.png` });
    await calc.getByRole("button", { name: "Close", exact: true }).click();
    assert.equal(await page.locator("dialog[open]").count(), 0);
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Calculator desktop/phone: stats with chance of profit, templates and strategy names, strike stepper, $/% price × date table, range and scenario cells, Greeks and no overflow passed.",
  );
} finally {
  await browser.close();
}
