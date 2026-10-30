// Disposable paper-workflows fixture. Responses below are explicitly synthetic counterfactual views.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8893";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 950 } }),
      errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    let updated = false,
      target = "";
    await page.route("**/api/stocks/paper", async (route) => {
      const response = await route.fetch(),
        data = await response.json();
      target = data.report.accounts.find(
        (a) => !a.baseline && a.open_positions,
      )?.id;
      for (const a of data.report.accounts) {
        a.curve = a.curve.slice(-3);
        a.sessions = a.curve.length;
        if (a.id === target && updated) {
          a.equity += 50;
          a.curve.at(-1).equity += 50;
        }
      }
      await route.fulfill({ response, json: data });
    });
    await page.route("**/api/stocks/paper/equity?*", async (route) => {
      const response = await route.fetch(),
        data = await response.json();
      data.curve = data.curve.slice(-3);
      if (
        updated &&
        new URL(route.request().url()).searchParams.get("account") === target
      )
        data.curve.at(-1).equity += 50;
      await route.fulfill({ response, json: data });
    });
    await page.goto(`${base}/#paper`);
    await page
      .getByRole("button", { name: "Available (3 sessions)", exact: true })
      .waitFor();
    const chart = page.locator(".paper-equity");
    assert.equal(
      await chart.locator(".kc-ranges").getByRole("button").count(),
      1,
    );
    assert.match(await chart.innerText(), /3 actual daily observations/);
    assert.ok(await chart.locator(".kc-axis:not(.kc-time-axis)").count());
    const equity = page
      .locator(".paper-metric")
      .filter({ hasText: "Simulated equity" })
      .locator("strong");
    const before = await equity.innerText();
    await chart.getByLabel("PAPER chart metric").selectOption("pnl");
    await chart.locator("h3").click();
    updated = true;
    await page.waitForFunction(
      (value) =>
        [...document.querySelectorAll(".paper-metric")].some(
          (n) =>
            n.textContent.includes("Simulated equity") &&
            !n.textContent.includes(value),
        ),
      before,
      { timeout: 20000 },
    );
    assert.notEqual(await equity.innerText(), before);
    assert.equal(
      await chart.getByLabel("PAPER chart metric").inputValue(),
      "pnl",
      "polling retains the selected chart metric",
    );
    await page.screenshot({ path: `/tmp/kestrel-paper-sparse-${width}.png` });
    await page.goto(`${base}/#options`);
    await page
      .getByRole("heading", {
        name: "Calls & puts: contract shortlist",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("heading", {
        name: "Social discovery · separate from market evidence",
        exact: true,
      })
      .waitFor();
    assert.equal(
      await page.getByLabel("Option type", { exact: true }).inputValue(),
      "call",
    );
    await page.getByLabel("Option type", { exact: true }).selectOption("put");
    await page.goto(`${base}/#settings`);
    await page
      .getByRole("heading", {
        name: "What each data connection powers",
        exact: true,
      })
      .waitFor();
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
    "Sparse PAPER desktop/mobile: three real marks, honest ranges, value scale, automatic refresh without navigation, options lanes and source-capability settings passed.",
  );
} finally {
  await browser.close();
}
