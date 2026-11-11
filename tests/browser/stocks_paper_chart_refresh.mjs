// Paper's 10-second refresh must not disturb a reader: against --scenario paper-workflows, with open positions' P/L
// changing on every refresh (as in market hours), a clicked and hovered chart keeps its readout and the page its
// scroll position; after the pointer leaves, the refresh rebuilds the page in place.
//   uv run python -m tests.browser.stocks_fixture_server --scenario paper-workflows --port 8899
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8899";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  const page = await browser.newPage({
    viewport: { width: 1920, height: 960 },
  });
  let fetches = 0;
  await page.route(/\/api\/stocks\/paper(\?|$)/, async (route) => {
    const response = await route.fetch(),
      json = await response.json();
    fetches += 1;
    for (const t of json.trades || [])
      t.unrealized_pnl = (t.unrealized_pnl || 0) + fetches;
    await route.fulfill({ response, json });
  });
  await page.goto(`${base}/#paper`);
  const chart = page.locator(".paper-account-chart").first();
  await chart.waitFor();
  await chart.scrollIntoViewIfNeeded();
  const box = await chart.boundingBox();
  const state = () =>
    page.evaluate(() => ({
      tip: !!document.querySelector(".kc-tooltip:not([hidden])"),
      y: scrollY,
    }));
  await page.mouse.click(box.x + 300, box.y + box.height / 2);
  const before = await state();
  assert.equal(before.tip, true);
  await page.waitForTimeout(12000); // past a refresh
  assert.deepEqual(await state(), before, "hovered chart kept its readout");
  await page.mouse.move(box.x + 300, box.y + box.height + 120);
  const rebuilt = page.waitForFunction((n) => window.__rebuilds > n, 0, {
    timeout: 25000,
  });
  await page.evaluate(() => {
    window.__rebuilds = 0;
    new MutationObserver(() => (window.__rebuilds += 1)).observe(
      document.getElementById("app"),
      { childList: true },
    );
  });
  await rebuilt;
  assert.equal((await state()).y, before.y, "refresh kept the scroll position");
  console.log(
    "Paper chart refresh: hovered readout kept, rebuild after leaving keeps the scroll position passed.",
  );
} finally {
  await browser.close();
}
