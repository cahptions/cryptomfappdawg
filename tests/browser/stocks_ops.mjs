// Run only against stocks_fixture_server --scenario ops: portfolio risk, phone alerts and the watchdog.
// Alert delivery in that fixture is a recorder; nothing is sent anywhere.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8878";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));

    // Risk: totals, scenarios and positions; unknown greeks say unknown.
    await page.goto(`${base}/#risk`);
    await page.getByRole("heading", { name: "Portfolio risk" }).waitFor();
    const totals = page.locator("#risk-totals");
    await totals.getByText("Vega", { exact: true }).waitFor();
    assert.match(await totals.innerText(), /per 1% SPY move/);
    assert.match(await totals.innerText(), /per 1 volatility point/);
    const scenarios = await page.locator("#risk-scenarios").innerText();
    assert.match(scenarios, /SPY −5%/);
    assert.match(scenarios, /Volatility \+5 points/);
    assert.match(scenarios, /\$[0-9,]+\.\d\d/);
    const positions = page.locator("table").last();
    assert.match(await positions.innerText(), /unknown/);
    assert.match(await positions.innerText(), /modelled/);
    assert.match(await positions.innerText(), /quotes/);
    assert.match(
      await page.locator("body").innerText(),
      /1 with unknown greeks/,
    );
    if (width === 375) {
      const tiles = await page
        .locator("#risk-totals .risk-tile")
        .evaluateAll((nodes) =>
          nodes.map((n) => Math.round(n.getBoundingClientRect().left)),
        );
      assert.equal(new Set(tiles).size, 1, "one column on a phone");
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      assert.ok(overflow <= 1, `no sideways scrolling (${overflow}px)`);
    }

    // Home links to it.
    await page.goto(`${base}/#dashboard`);
    await page
      .getByRole("link", { name: "Portfolio risk →" })
      .first()
      .waitFor();

    // Settings → Phone alerts: channel presence only, switches saved with the session token, a test alert.
    await page.goto(`${base}/#settings`);
    const alerts = page.locator("#alerts");
    await alerts.getByText("Set up: ntfy.").waitFor();
    assert.doesNotMatch(await alerts.innerText(), /ntfy\.example\.test/);
    const enabled = alerts.locator("#alerts-enabled");
    assert.equal(await enabled.isChecked(), false, "off by default");
    if (width === 1366) {
      await enabled.check();
      await alerts.getByRole("button", { name: "Save alert settings" }).click();
      await alerts.getByText("Saved. Alerts are on.").waitFor();
      await alerts.getByRole("button", { name: "Send test alert" }).click();
      await alerts.getByText("Sent to ntfy.").waitFor();
      await alerts.getByText("Recent alerts").waitFor();
      await enabled.uncheck();
      await alerts.getByRole("button", { name: "Save alert settings" }).click();
      await alerts.getByText("Saved. Alerts are off.").waitFor();
    }

    // Evidence → Data health shows the watchdog's last check.
    await page.goto(`${base}/#proof`);
    await page.getByRole("button", { name: "Check data health" }).click();
    const watch = page.locator("#evidence-watchdog");
    await watch.getByText(/at the last check/).waitFor();
    // ... and the accuracy checks' counts (this fixture's hand-made PAPER book breaks two rules on purpose).
    const accuracy = page.locator("#evidence-accuracy");
    await accuracy.getByText(/broke a rule/).waitFor();
    assert.match(await accuracy.innerText(), /Cash never goes negative/);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("stocks ops: risk, phone alerts and watchdog passed");
} finally {
  await browser.close();
}
