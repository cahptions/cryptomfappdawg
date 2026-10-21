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
    await page.goto(`${base}/#lists`);
    await page.getByRole("heading", { name: "Find the evidence." }).waitFor();
    if (scenario === "populated") {
      await page.goto(`${base}/#stock/CHEAP`);
      await page
        .getByRole("heading", { name: "CHEAP Corp Common Stock" })
        .waitFor();
      await page.locator(".chart polyline").waitFor();
      assert.equal(await page.locator(".grid .panel").count(), 11);
    }
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
