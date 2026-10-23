// Isolated ui-workflows fixture only. All quotes/history are synthetic; no broker or account is contacted.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8880";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 950 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(`${base}/#stock/SPY`);
    await page.getByRole("button", { name: "Candles", exact: true }).click();
    const initial = await page.locator(".candle-body").count();
    await page.getByRole("button", { name: "5Y", exact: true }).click();
    const fiveYears = await page.locator(".candle-body").count();
    assert.ok(fiveYears > 240 && fiveYears < 270 && fiveYears > initial);
    assert.match(
      await page.locator(".chart-readout").innerText(),
      /Weekly candles/,
    );
    await page.getByRole("button", { name: "Max", exact: true }).click();
    assert.ok((await page.locator(".candle-body").count()) > 230);
    assert.match(
      await page.locator(".chart-readout").innerText(),
      /Monthly candles/,
    );
    await page
      .getByText("Chart history and quote coverage", { exact: true })
      .click();
    assert.match(await page.locator("#app").innerText(), /up to 20 years/);
    const chart = page.locator("svg.price-chart");
    await chart.focus();
    await chart.press("Home");
    assert.match(await page.getByTestId("candle-tooltip").innerText(), /2006/);
    await chart.press("Escape");
    await page.locator(".strike-button").first().click();
    const calc = page.getByRole("dialog");
    await calc.waitFor();
    await calc.getByText("Net entry debit", { exact: true }).waitFor();
    await calc
      .getByLabel("Underlying scenario price ($)", { exact: true })
      .fill("120");
    await calc
      .getByLabel("Scenario date (at close)", { exact: true })
      .fill("2026-10-30");
    await page.waitForResponse(
      (r) => r.url().endsWith("/calculator") && r.status() === 200,
    );
    assert.ok(await calc.locator(".payoff-scenario").count());
    await calc.getByRole("button", { name: "+ Add leg", exact: true }).click();
    assert.equal(await calc.locator(".calc-leg").count(), 2);
    await calc
      .getByLabel("Action", { exact: true })
      .nth(1)
      .selectOption("sell");
    await calc
      .getByLabel("Strike", { exact: true })
      .nth(1)
      .selectOption({ index: 2 });
    await calc
      .getByLabel("Expiration", { exact: true })
      .nth(1)
      .selectOption("2026-11-20");
    await calc.getByText(/Mixed expirations: maximum risk/).waitFor();
    assert.equal(
      await page.evaluate(
        () =>
          document.querySelector("dialog").scrollWidth <=
          document.querySelector("dialog").clientWidth,
      ),
      true,
    );
    await page.screenshot({
      path: `/tmp/kestrel-calculator-${width}.png`,
      fullPage: true,
    });
    await calc.getByRole("button", { name: "Close", exact: true }).click();
    assert.equal(await page.locator("dialog[open]").count(), 0);
    await page.goto(`${base}/#proof`);
    await page
      .getByRole("heading", { name: "Evidence", exact: true })
      .waitFor();
    await page
      .getByRole("button", { name: "Check data health", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Download health report", exact: true })
      .waitFor();
    assert.ok(!(await page.locator("#app").innerText()).includes("uv run"));
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
    );
    assert.deepEqual(errors, []);
    await page.close();
  }
  // Actual owned evaluator subprocess: start/cancel, then complete without changing forward PAPER records.
  const request = await browser.newContext();
  const client = request.request;
  const csrf = (await (await client.get(`${base}/api/stocks/session`)).json())
    .csrf_token;
  const headers = { "X-CSRF-Token": csrf, Origin: base };
  const before = await (await client.get(`${base}/api/stocks/paper`)).json();
  assert.equal(
    (
      await client.post(`${base}/api/stocks/proof/start`, { headers, data: {} })
    ).status(),
    200,
  );
  assert.equal(
    (
      await client.post(`${base}/api/stocks/proof/cancel`, {
        headers,
        data: {},
      })
    ).status(),
    200,
  );
  await assertEventually(
    async () =>
      !(await (await client.get(`${base}/api/stocks/proof`)).json()).job
        .running,
  );
  assert.equal(
    (
      await client.post(`${base}/api/stocks/proof/start`, { headers, data: {} })
    ).status(),
    200,
  );
  let report;
  await assertEventually(async () => {
    report = await (await client.get(`${base}/api/stocks/proof`)).json();
    assert.notEqual(report.job.status, "failed", report.job.phase);
    return report.job.status === "completed";
  });
  assert.equal(report.validation.advisory_only, true);
  const after = await (await client.get(`${base}/api/stocks/paper`)).json();
  assert.deepEqual(after.positions, before.positions);
  assert.deepEqual(after.events, before.events);
  await request.close();
  console.log(
    "Desktop/mobile long-history ranges, strike calculator, multi-leg mixed expiry, Evidence health and real evaluation run/cancel passed.",
  );
} finally {
  await browser.close();
}
async function assertEventually(check) {
  for (let i = 0; i < 60; i++) {
    if (await check()) return;
    await new Promise((r) => setTimeout(r, 500));
  }
  assert.fail("Timed out waiting for evaluation");
}
