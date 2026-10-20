// Robinhood dashboard smoke test against tests/browser/rh_fixture_server.py (populated scenario by default).
//   uv run python -m tests.browser.rh_fixture_server --scenario populated --port 8878
//   KESTREL_RH_SCENARIO=populated node tests/browser/rh_smoke.mjs
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_RH_URL ?? "http://127.0.0.1:8878";
const scenario = process.env.KESTREL_RH_SCENARIO ?? "populated";
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || undefined });
const errors = [];
try {
  for (const scheme of ["light", "dark"]) {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, colorScheme: scheme });
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    page.on("pageerror", (e) => errors.push(String(e)));
    await page.goto(`${base}/#/`);
    await page.getByText("Your $100 account · paper").waitFor();
    if (scenario === "populated") {
      await page.locator(".rh-rows").first().getByText("ETH").waitFor();
      await page.locator(".rh-chart-line").waitFor();                 // the account chart loads after the snapshot
      await page.locator(".rh-chart svg").hover({ position: { x: 300, y: 100 } });
      assert.ok(await page.locator(".rh-chart-dot").count(), "hover cursor shown");
      await page.goto(`${base}/#/coin/ETH-USD`);
      await page.getByText("Why the desk would or wouldn't buy").waitFor();
      assert.equal(await page.locator(".rh-why .rh-card").count(), 3);
      await page.getByText("Your paper position").waitFor();
      await page.getByRole("tab", { name: "1W" }).click();
      await page.locator(".rh-chart-line").waitFor();
      for (const interval of ["15m", "4h", "1d", "1h"]) {
        const loaded = page.waitForResponse((r) => r.url().includes(`/api/rh/coin/ETH-USD?span=1W&interval=${interval}`)
          && r.ok());
        await page.getByRole("tab", { name: interval, exact: true }).click();
        const detail = await (await loaded).json();
        assert.equal(detail.interval, interval);
        assert.ok(detail.points.length > 0, `${interval} history present`);
        assert.equal(Object.keys(detail.frames).length, 4);
        await page.locator(".rh-chart-line").waitFor();
      }
      await page.getByRole("heading", { name: "Data for the current check" }).waitFor();
    }
    await page.goto(`${base}/#/strategies`);
    await page.locator(".rh-strategy").first().waitFor();
    assert.equal(await page.locator(".rh-strategy").count(), 5);
    await page.goto(`${base}/#/system`);
    await page.getByText(scenario === "populated" ? /^live · last/ : "Connect Robinhood prices (read-only)").waitFor();
    await page.getByText("Next 15-minute candle close + publication grace", { exact: true }).waitFor();
    await page.goto(`${base}/#/learning`);
    await page.getByRole("heading", { name: "Learning", exact: true }).waitFor();
    await page.getByText("The learned strategy's own paper record").waitFor();
    await page.goto(`${base}/#/activity`);
    await page.getByRole("heading", { name: "Activity" }).waitFor();
    await page.close();
  }
  const phone = await browser.newPage({ viewport: { width: 375, height: 812 } });
  await phone.goto(`${base}/#/`);
  await phone.getByText("Your $100 account · paper").waitFor();
  assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true,
    "no horizontal scroll at phone width");
  for (const label of ["Investing", "Strategies", "Activity", "System"]) {
    assert.ok(await phone.getByRole("link", { name: label, exact: true }).isVisible(), `${label} visible on a phone`);
  }
  if (scenario === "populated") {
    await phone.goto(`${base}/#/coin/ETH-USD`);
    await phone.getByRole("heading", { name: "Data for the current check" }).waitFor();
    assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true,
      "timeframe readiness fits a phone");
  }
  assert.deepEqual(errors, []);
  console.log(`rh smoke ok (${scenario})`);
} finally {
  await browser.close();
}
