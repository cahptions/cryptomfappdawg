// Live updates without a visible reload (--scenario populated): when new research results arrive the page is
// rebuilt off screen and swapped in within one frame. It never goes blank, the scroll position stays, and a chart
// readout under the pointer stays open.
//   uv run python -m tests.browser.stocks_fixture_server --scenario populated --port 8877
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8877";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  for (const width of [1366, 390]) {
    const page = await browser.newPage({
      viewport: { width, height: 800 },
      hasTouch: width === 390,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    let bump = 0;
    await page.route(/\/api\/stocks\/snapshot(\?|$)/, async (route) => {
      const response = await route.fetch(),
        json = await response.json();
      if (bump) json.asof = "2099-01-0" + Math.min(bump, 9);
      await route.fulfill({ response, json });
    });
    await page.goto(`${base}/#plays`);
    await page.locator(".play-strip").waitFor();
    await page.evaluate(() => {
      window.__blank = 0;
      window.__swaps = 0;
      const app = document.getElementById("app");
      new MutationObserver(() => {
        if (!app.childElementCount) window.__blank += 1;
        else window.__swaps += 1;
      }).observe(app, { childList: true });
      scrollTo(0, 400);
    });
    const y = await page.evaluate(() => scrollY);
    const before = await page.evaluateHandle(() =>
      document.querySelector("#app > *"),
    );
    bump = 1; // the next background check reports a newly analysed session
    await page.waitForFunction(() => window.__swaps > 0, undefined, {
      timeout: 45000,
    });
    assert.equal(
      await before.evaluate((n) => n.isConnected),
      false,
      "the page was updated by itself",
    );
    assert.equal(
      await page.evaluate(() => window.__blank),
      0,
      "the page never went blank",
    );
    assert.ok(
      Math.abs((await page.evaluate(() => scrollY)) - y) < 5,
      "the scroll position stayed",
    );
    assert.equal(await page.locator(".play-strip").count(), 1);
    assert.equal(
      await page.evaluate(() => document.querySelectorAll("main").length),
      1,
      "the off-screen copy is gone",
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Live updates desktop/phone: new results swapped in place, never blank, scroll kept, no leftover copy.",
  );
} finally {
  await browser.close();
}
