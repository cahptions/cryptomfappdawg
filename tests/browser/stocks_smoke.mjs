// Run against stocks_fixture_server, never an operator's Settings/credentials.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8877";
const scenario = process.env.KESTREL_STOCKS_SCENARIO ?? "populated";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await page.goto(base);
    await page.getByRole("heading", { name: "Market overview" }).waitFor();
    await page.getByRole("heading", { name: "Trending top 10" }).waitFor();
    await page.getByRole("heading", { name: "Options intelligence" }).waitFor();
    await page.getByRole("tab", { name: "Rising trends" }).click();
    await page
      .getByRole("heading", { name: "Rising trends", exact: true })
      .waitFor();
    await page.getByRole("tab", { name: "Top gainers" }).click();
    await page.getByRole("heading", { name: "Top 10 gainers" }).waitFor();
    assert.equal(await page.locator(".benchmark-card").count(), 6);
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
    );
    await page.screenshot({
      path: `/tmp/kestrel-hub-${scenario}-${width}.png`,
      fullPage: true,
    });
    if (scenario === "populated") {
      await page.goto(`${base}/#stock/CHEAP`);
      await page
        .getByRole("heading", { name: "CHEAP Corp Common Stock" })
        .waitFor();
      await page.locator(".price-path").waitFor();
      assert.equal(await page.locator(".desk-card").count(), 11);
      await page.getByRole("button", { name: "Candles", exact: true }).click();
      assert.ok((await page.locator(".candle-body").count()) > 0);
      await page.getByRole("tab", { name: "Puts", exact: true }).click();
      await page
        .getByRole("button", { name: "+ Add to watchlist", exact: true })
        .click();
      await page
        .getByRole("button", { name: "✓ In your watchlist", exact: true })
        .waitFor();
      await page.screenshot({
        path: `/tmp/kestrel-stock-${scenario}-${width}.png`,
        fullPage: true,
      });
      await page.goto(`${base}/#dashboard`);
      await page
        .locator(".watch-row")
        .getByRole("link", { name: "CHEAP", exact: true })
        .waitFor();
      await page.goto(`${base}/#stock/CHEAP`);
      await page
        .getByRole("button", { name: "✓ In your watchlist", exact: true })
        .click();
      await page
        .getByRole("button", { name: "+ Add to watchlist", exact: true })
        .waitFor();
    }
    await page.goto(`${base}/#options`);
    await page.getByRole("heading", { name: "Options overview" }).waitFor();
    await page.goto(`${base}/#lists/gainers`);
    await page.getByRole("heading", { name: "Find the evidence." }).waitFor();
    assert.equal(
      await page.getByRole("combobox", { name: "Research list" }).inputValue(),
      "gainers",
    );
    await page.goto(`${base}/#paper`);
    await page.getByRole("heading", { name: "Paper portfolios." }).waitFor();
    if (scenario === "populated")
      await page.getByRole("cell", { name: "Hold SPY", exact: true }).waitFor();
    else
      await page
        .getByRole("heading", { name: "The forward record starts here" })
        .waitFor();
    await page.goto(`${base}/#proof`);
    await page
      .getByRole("heading", { name: "Evidence before confidence." })
      .waitFor();
    await page.goto(`${base}/#system`);
    await page.getByRole("heading", { name: "System.", exact: true }).waitFor();
    await page.goto(`${base}/#settings`);
    await page.getByRole("heading", { name: "Connect your data." }).waitFor();
    await page.getByRole("heading", { name: "Authorization status" }).waitFor();
    assert.equal(
      await page.locator("#app-key").getAttribute("type"),
      "password",
    );
    assert.equal(
      await page.locator("#app-secret").getAttribute("type"),
      "password",
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
      "no horizontal page overflow, including Settings on a phone",
    );
    await page.screenshot({
      path: `/tmp/kestrel-stocks-${scenario}-${width}.png`,
      fullPage: true,
    });
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    `Stocks ${scenario}: desktop/phone navigation, chart, desk coverage, PAPER, Settings and CSP passed.`,
  );
} finally {
  await browser.close();
}
