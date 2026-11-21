// Run against stocks_fixture_server --scenario populated.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8899";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});

try {
  for (const width of [375, 1366]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const list = page.getByLabel("Research list", { exact: true });
    const waitForList = async (id) => {
      await page.waitForURL(`**/#lists/${id}`);
      await list.waitFor();
      await page.waitForFunction(
        (expected) =>
          document.querySelector("#app .lists-controls select")?.value ===
          expected,
        id,
      );
    };

    await page.goto(`${base}/#dashboard`);
    await page
      .getByRole("navigation", {
        name: width === 375 ? "Sections" : "Main",
        exact: true,
      })
      .getByRole("link", { name: "Stocks", exact: true })
      .click();
    await page.locator('#app a[href="#lists/momentum"]').first().click();
    await waitForList("momentum");
    await list.selectOption("undervalued");
    await waitForList("undervalued");
    await page.goBack();
    await waitForList("momentum");
    await page.goForward();
    await waitForList("undervalued");

    await page.getByLabel("Stock symbol", { exact: true }).fill("SPY");
    await page.getByLabel("Stock symbol", { exact: true }).press("Enter");
    await page.waitForURL("**/#stock/SPY");
    await page.goBack();
    await waitForList("undervalued");
    await page.goBack();
    await waitForList("momentum");
    await page.goBack();
    await page.waitForURL("**/#stocks");
    await page.goBack();
    await page.waitForURL("**/#dashboard");
    await page.getByRole("heading", { name: "Market overview" }).waitFor();
    assert.equal(new URL(page.url()).hash, "#dashboard");
    await page.close();
  }
  console.log(
    "Stocks Back and Forward keep both research lists and adjacent routes at desktop and phone widths.",
  );
} finally {
  await browser.close();
}
