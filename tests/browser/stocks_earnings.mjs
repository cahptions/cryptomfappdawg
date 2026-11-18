// Earnings: the stock page's "Options look cheap / expensive / fairly priced for this report" block and the Plays
// Earnings section (a bought straddle when cheap, a sold iron butterfly when expensive), at 1366 and 375 wide.
//   uv run python -m tests.browser.stocks_fixture_server --scenario earnings --port 8883
// Synthetic reports only (S12 cheap, S13 expensive, S14 fairly priced); no provider is contacted.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8883";
const out =
  process.env.KESTREL_BROWSER_OUTPUT ?? "/tmp/kestrel-browser-results";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
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

    // The stock page: verdict, the three moves with a hint each, the ratio and when the report is.
    for (const [symbol, title, verdict] of [
      ["S12", "Options look cheap for this report", "cheap"],
      ["S13", "Options look expensive for this report", "expensive"],
      ["S14", "Options look fairly priced for this report", "fair"],
    ]) {
      await page.goto(`${base}/#stock/${symbol}`);
      const block = page.locator("#sp-earnings .sp-earnings-move");
      await block.waitFor();
      assert.equal(await block.locator(".sp-em-title").innerText(), title);
      assert.ok(
        await block.evaluate((n, v) => n.classList.contains(v), verdict),
      );
      assert.match(
        await block.locator(".sp-em-when").innerText(),
        /^Results on Oct 28, after the close \(announced\)$/,
      );
      for (const label of [
        "Priced move",
        "Typical past move",
        "Expected move",
        "Expected ÷ priced",
      ]) {
        const tile = block.locator(".sp-stat").filter({
          has: page.locator("dt").getByText(label, { exact: true }),
        });
        assert.match(await tile.locator("dd").innerText(), /^±?\d|×$/);
        assert.ok((await tile.locator(".sp-em-hint").innerText()).length > 10);
      }
      assert.equal(
        await block.locator(".sp-stat dd").nth(1).innerText(),
        { S12: "±11.0%", S13: "±1.2%", S14: "±4.8%" }[symbol],
      );
      assert.match(
        await block.locator(".sp-em-note").innerText(),
        /Confidence: high \(8 past reports\)\./,
      );
      // nothing overflows the page at phone width
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth - window.innerWidth,
      );
      assert.ok(overflow <= 1, `page scrolls sideways by ${overflow}px`);
      if (symbol === "S12")
        await block.screenshot({ path: `${out}/earnings-block-${width}.png` });
    }

    // Plays: the Earnings section buys S12's straddle and sells S13's butterfly; S14 has no play.
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    await page.getByRole("tab", { name: /Earnings plays/ }).click();
    const cards = page.locator("article.play-card");
    await cards.first().waitFor();
    const text = await page.locator("main").innerText();
    assert.match(text, /S12/);
    assert.match(text, /S13/);
    assert.doesNotMatch(text, /\bS14\b/);
    const cheap = cards.filter({ hasText: "S12" }).first();
    assert.match(await cheap.innerText(), /STRADDLE/);
    assert.match(
      await cheap.innerText(),
      /Options price a ±4\.\d% results-day move; KESTREL expects ±8\.\d%/,
    );
    const dear = cards.filter({ hasText: "S13" }).first();
    assert.match(await dear.innerText(), /IRON BUTTERFLY/);
    assert.match(
      await dear.innerText(),
      /Options look expensive for this report/,
    );
    await page.screenshot({
      path: `${out}/earnings-plays-${width}.png`,
      fullPage: false,
    });
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log("stocks earnings: ok");
} finally {
  await browser.close();
}
