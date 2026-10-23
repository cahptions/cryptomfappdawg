// Isolated populated fixture only. Simulate the old server contract without touching an operator's data.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8882";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 950 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    let unsupportedRequests = 0;
    page.on("request", (request) => {
      if (/\/api\/stocks\/(explore|proof\/)/.test(request.url()))
        unsupportedRequests++;
    });
    await page.route("**/api/stocks/session", async (route) => {
      const response = await route.fetch(),
        session = await response.json();
      delete session.ui_protocol;
      await route.fulfill({ response, json: session });
    });
    for (const route of ["stocks", "proof", "paper"]) {
      await page.goto(`${base}/#${route}`);
      await page
        .getByRole("heading", {
          name: "Restart Stocks to finish updating",
          exact: true,
        })
        .waitFor();
      assert.match(await page.locator("#app").innerText(), /application menu/);
      assert.equal(
        await page.getByText("Not Found", { exact: true }).count(),
        0,
      );
    }
    assert.equal(
      unsupportedRequests,
      0,
      "Old servers must be identified before issuing unsupported page actions",
    );
    await page.screenshot({
      path: `/tmp/kestrel-update-required-${width}.png`,
      fullPage: true,
    });
    await page.unroute("**/api/stocks/session");
    await page
      .getByRole("button", { name: "Check again", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Paper trading", exact: true })
      .waitFor();
    await page.goto(`${base}/#stocks`);
    await page.getByRole("heading", { name: "Stocks", exact: true }).waitFor();
    await page.goto(`${base}/#proof`);
    await page
      .getByRole("button", { name: "Check data health", exact: true })
      .click();
    await page
      .getByRole("columnheader", { name: "Database", exact: true })
      .waitFor();
    // A partially updated installation must still explain raw route-level 404s instead of printing Not Found.
    await page.route("**/api/stocks/proof/health", (route) =>
      route.fulfill({ status: 404, json: { detail: "Not Found" } }),
    );
    await page
      .getByRole("button", { name: "Check data health", exact: true })
      .click();
    await page
      .getByText(/This running Stocks server does not have the updated feature/)
      .waitFor();
    await page.route("**/api/stocks/proof/start", (route) =>
      route.fulfill({ status: 404, json: { detail: "Not Found" } }),
    );
    await page
      .getByRole("button", { name: "Refresh evaluation", exact: true })
      .click();
    await page
      .locator(".evidence-job")
      .getByText(/This running Stocks server does not have the updated feature/)
      .waitFor();
    assert.equal(
      await page
        .getByText(
          /This running Stocks server does not have the updated feature/,
        )
        .count(),
      2,
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
    "Desktop/mobile: old server detected before missing routes; restart guidance, current Stocks/Evidence and both missing-action errors passed.",
  );
} finally {
  await browser.close();
}
