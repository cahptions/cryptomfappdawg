// Disposable ui-workflows fixture; injected crash context tests presentation, not a real provider failure.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8899";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 950 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    await page.route("**/api/stocks/proof/health", async (route) => {
      const response = await route.fetch();
      const data = await response.json();
      data.worker.running = false;
      data.worker.last_failure = {
        at: "2026-11-05T18:43:10+00:00",
        pid: 123,
        exception_type: "AttributeError",
        frames: [{ file: "stocks/runner.py", line: 300 }],
      };
      data.databases["stocks-market.db"] = {
        integrity: "not_checked",
        status: "integrity_check_time_budget_exceeded",
      };
      await route.fulfill({ response, json: data });
    });
    await page.goto(`${base}/#proof`);
    await page
      .getByRole("button", { name: "Check data health", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "Last recorded PAPER failure" })
      .waitFor();
    const text = await page.locator("#evidence-health").innerText();
    assert.match(text, /PAPER worker is stopped/);
    assert.match(text, /AttributeError/);
    assert.match(text, /stocks\/runner.py:300/);
    assert.match(text, /exceeded its time budget; not checked/);
    assert.ok(!text.includes("uv run"));
    const downloaded = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Download health report", exact: true })
      .click();
    const stream = await (await downloaded).createReadStream();
    let raw = "";
    for await (const chunk of stream) raw += chunk.toString();
    const report = JSON.parse(raw);
    assert.equal(
      report.health.worker.last_failure.exception_type,
      "AttributeError",
    );
    assert.equal(report.health.worker.last_failure.frames[0].line, 300);
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
    "PAPER failure details, incomplete integrity check, and downloadable diagnostics pass at desktop/375px.",
  );
} finally {
  await browser.close();
}
