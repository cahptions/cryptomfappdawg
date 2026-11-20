// Use --scenario portfolio. The report contains invented data for the real read-only endpoint.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8892";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});

try {
  for (const width of [375, 768, 1366]) {
    const page = await browser.newPage({ viewport: { width, height: 850 } });
    const faults = [];
    page.on("pageerror", (error) => faults.push(String(error)));
    await page.goto(`${base}/#dashboard`);
    if (width < 960) {
      await page.getByRole("button", { name: "More", exact: true }).click();
      await page
        .getByRole("navigation", { name: "More sections" })
        .getByRole("link", { name: "Portfolio lab" })
        .click();
    } else {
      await page
        .getByRole("navigation", { name: "Main" })
        .getByRole("link", { name: "Portfolio lab" })
        .click();
    }
    await page.getByRole("heading", { name: "Portfolio lab" }).waitFor();
    await page
      .getByText("PAPER strategy ledgers will appear", { exact: false })
      .waitFor();
    await page.getByLabel("Evidence source").selectOption("replay");
    await page.getByRole("checkbox", { name: /Momentum/ }).waitFor();
    await page.getByRole("button", { name: "Evaluate allocation" }).click();
    await page.getByRole("heading", { name: "Held-out comparison" }).waitFor();
    assert.ok(
      !(await page.locator(".capital-chart").textContent()).includes(
        "[object Object]",
      ),
      "time axis receives dates",
    );
    assert.ok(
      (await page.locator(".capital-chart svg text").allTextContents()).some(
        (tick) => tick.endsWith("%"),
      ),
      "return axis uses percentages",
    );
    assert.match(
      await page.locator(".capital-results").innerText(),
      /Paired 90% block-bootstrap/,
    );
    await page.getByText("Inspect comparison inputs", { exact: true }).click();
    assert.match(
      await page.locator(".capital-provenance").innerText(),
      /Frozen with replay/,
    );
    assert.match(
      await page.locator(".capital-provenance code").innerText(),
      /^[a-f0-9]{64}$/,
    );
    await page.route("**/api/stocks/snapshot", async (route) => {
      const response = await route.fetch();
      const json = await response.json();
      json.worker = { ...json.worker, finished_at: "portfolio-refresh" };
      await route.fulfill({ response, json });
    });
    await page.evaluate(() => refresh());
    assert.equal(
      await page.getByRole("heading", { name: "Held-out comparison" }).count(),
      1,
    );
    await page.unrouteAll({ behavior: "wait" });
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - innerWidth,
    );
    assert.ok(overflow <= 1, `no page overflow at ${width}px (${overflow}px)`);
    await page.getByRole("link", { name: "Inspect open exposure" }).click();
    await page.getByLabel("Book", { exact: true }).selectOption("reference_1k");
    await page.waitForURL("**/#risk/reference_1k");
    await page.getByRole("link", { name: "Open PAPER book" }).waitFor();
    await page.getByLabel("SPY move (%)", { exact: true }).fill("-12");
    await page.getByLabel("IV change (points)", { exact: true }).fill("15");
    await page.getByRole("button", { name: "Apply stress" }).click();
    await page
      .getByText("SPY −12% and volatility +15 points.", { exact: true })
      .waitFor();
    await page.getByLabel("Book", { exact: true }).selectOption("reference_5k");
    await page.waitForURL("**/#risk/reference_5k");
    await page.getByRole("link", { name: "Open PAPER book" }).waitFor();
    assert.equal(
      await page
        .getByRole("link", { name: "Open PAPER book" })
        .getAttribute("href"),
      "#learning/reference_5k",
    );
    assert.deepEqual(faults, []);
    await page.close();
  }
  console.log(
    "Portfolio lab and scoped risk paths passed at 375px, 768px and 1366px.",
  );
} finally {
  await browser.close();
}
