// Background updates never rebuild the page being read (--scenario populated): when new research results arrive the
// page stays exactly as it is and a "New results ready · Show" button appears; showing them keeps the scroll position.
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
      if (bump) {
        json.asof = "2099-01-0" + Math.min(bump, 9);
        json.collection = { ...(json.collection || {}), progress: bump };
      }
      await route.fulfill({ response, json });
    });
    await page.goto(`${base}/#plays`);
    await page.locator(".play-strip").waitFor();
    const marker = await page.evaluateHandle(() =>
      document.querySelector("#app > *"),
    );
    await page.evaluate(() => scrollTo(0, 400));
    const y = await page.evaluate(() => scrollY);
    bump = 1; // the next background refresh reports a newly analysed session
    await page.evaluate(() => window.dispatchEvent(new Event("focus")));
    await page.waitForFunction(
      () => !document.querySelector(".fresh-results")?.hidden,
      undefined,
      { timeout: 45000 },
    );
    assert.equal(
      await marker.evaluate((node) => node.isConnected),
      true,
      "the page was not rebuilt by the background refresh",
    );
    assert.equal(
      await page.evaluate(() => scrollY),
      y,
      "the scroll position did not move",
    );
    await page.locator(".fresh-results").click();
    await page.waitForFunction(
      () => document.querySelector(".fresh-results").hidden,
    );
    await page.locator(".play-strip").waitFor();
    assert.ok(
      Math.abs((await page.evaluate(() => scrollY)) - y) < 5,
      "showing the new results kept the place",
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
    "Smooth updates desktop/phone: background results offered, not forced; showing them keeps the place.",
  );
} finally {
  await browser.close();
}
