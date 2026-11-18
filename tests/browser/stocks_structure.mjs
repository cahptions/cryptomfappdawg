// What a play is worth: each Plays card's "What it's worth" box (fair value against the expected fill, the edge, and
// in plain words whether it comes from the direction or from the option price vs the expected movement), at 1366 and
// 375 wide. The "Cheap volatility" section has a synthetic graded history in which the edge ranked plays better, so
// its cards say the ranking uses it; every other section says shadow.
//   uv run python -m tests.browser.stocks_fixture_server --scenario structure --port 8884
// Synthetic data only; no provider is contacted.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8884";
const out =
  process.env.KESTREL_BROWSER_OUTPUT ?? "/tmp/kestrel-browser-results";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
const driver =
  /^(Edge mostly from the expected direction of the stock\.|Edge mostly from options looking (cheap|expensive) vs how much the stock should move\.|Edge from both the expected direction and options looking (cheap|expensive) vs how much the stock should move\.|No edge at the expected fill: KESTREL's forecast values it below what it costs\.)$/;
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() === "error")
        errors.push(`${m.text()} (${m.location().url})`);
    });
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    for (const [tab, ranked] of [
      [/Most likely to finish ITM/, false],
      [/Cheap volatility/, true],
    ]) {
      await page.getByRole("tab", { name: tab }).click();
      const boxes = page.locator("article.play-card .structure-view:visible");
      await boxes.first().waitFor();
      const n = await boxes.count();
      assert.ok(n > 0);
      for (let i = 0; i < Math.min(n, 4); i++) {
        const box = boxes.nth(i);
        assert.equal(
          await box.locator("strong").innerText(),
          "What it's worth",
        );
        assert.match(
          await box.locator(".structure-value").innerText(),
          /^KESTREL values it at \$[\d,.]+( to the seller)? against \$[\d,.]+ (paid|received) at the expected fill: edge [+−]\$[\d,.]+( \([+−]?\d+% of the money at risk\))?\.$/,
        );
        assert.match(
          await box.locator(".structure-driver").innerText(),
          driver,
        );
        const note = await box.locator("small").innerText();
        assert.match(
          note,
          /^From the direction [+−]\$[\d,.]+ · from the option price vs the expected movement [+−]\$[\d,.]+ · chance of profit \d+%\./,
        );
        assert.match(
          note,
          ranked
            ? /This section's ranking uses it: it ranked past plays better\.$/
            : /Shadow: shown and recorded, not yet used for ranking\.$/,
        );
      }
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      assert.ok(overflow <= 1, `page scrolls sideways by ${overflow}px`);
      if (ranked)
        await boxes
          .first()
          .screenshot({ path: `${out}/structure-box-${width}.png` });
    }
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log("stocks_structure: ok");
} finally {
  await browser.close();
}
