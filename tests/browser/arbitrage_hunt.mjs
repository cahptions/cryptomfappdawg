// Run against tests/browser/arbitrage_fixture_server.py. Every price, sale and card is synthetic.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_ARBITRAGE_URL || "http://127.0.0.1:8895";
const browser = await chromium.launch();
const errors = [];
try {
  for (const viewport of [
    { width: 1280, height: 900 },
    { width: 390, height: 844 },
  ]) {
    const page = await browser.newPage({ viewport });
    page.on("pageerror", (e) => errors.push(e.message));
    await page.goto(`${base}/#deals`);
    await page.waitForSelector(".kinds .chip");
    if (viewport.width > 600) {
      // a barcode on the shelf: sold prices, the card and its numbers
      await page.fill("#q", "027084686524");
      await page.fill("#price", "9.97");
      await page.fill("#qty", "12");
      await page.locator("#check > button").last().click();
      await page.waitForSelector("#result .deal .verdict b");
      assert.equal(await page.textContent("#result .deal .verdict b"), "BUY");
      assert.match(
        await page.textContent("#result .deal h3"),
        /Fixture 20-car gift pack/,
      );
      assert.match(
        await page.textContent("#result .deal .stats"),
        /You pay\$10\.67/,
      );
      // a refused search falls back to pasting eBay's sold results
      await page.fill("#q", "refused thing");
      await page.fill("#price", "20");
      await page.locator("#check > button").last().click();
      await page.waitForSelector("#result textarea");
      const today = new Date().toLocaleDateString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
      });
      await page.fill(
        "#result textarea",
        `Sold  ${today}\nRefused thing in its box\n$80.00\nFree delivery`,
      );
      await page.click("#result .paste button");
      await page.waitForSelector(
        "#result .deal.buy, #result .deal.maybe, #result .deal.skip",
      );
      assert.match(
        await page.textContent("#result .deal .stats"),
        /Sells for\$80\.00/,
      );
      // a card, picked from the search
      await page.getByRole("radio", { name: "Pokémon" }).click();
      await page.fill("#q", "charizard 4");
      await page.locator("#check > button").click();
      await page.fill("#result #price", "250");
      await page.click(".card-pick");
      await page.waitForSelector("#result .deal .stats");
      assert.match(
        await page.textContent("#result .deal"),
        /TCGplayer market price/,
      );
      // gold against melt
      await page.getByRole("radio", { name: "Gold & silver" }).click();
      await page.selectOption("#karat", "10k");
      await page.fill("#grams", "6.2");
      await page.fill("#price", "89");
      await page.locator("#check > button").click();
      await page.waitForSelector("#result .deal .stats");
      assert.match(
        await page.textContent("#result .deal .stats"),
        /Melt value\$332\.2/,
      );
      // a rising LEGO set lands in Buy & Hold
      await page.getByRole("radio", { name: "LEGO" }).click();
      await page.fill("#q", "10497");
      await page.locator("#check > button").last().click();
      await page.waitForSelector("#result .deal");
      await page.goto(`${base}/#holds`);
      await page.waitForSelector("main .rise");
      assert.match(
        await page.textContent("main .rise"),
        /Rising \+\d+% a year/,
      );
      await page.goto(`${base}/#settings`);
      await page.fill("#bankroll", "500");
      await page.click("main form button");
      await page.getByText("Saved.").waitFor();
    }
    await page.goto(`${base}/#deals`);
    await page.waitForSelector("main .deal");
    assert.equal(await page.locator("main .deal").count(), 3); // the LEGO set had no price: not a deal
    assert.equal(
      await page.locator("main .deal .verdict b").first().textContent(),
      "BUY",
    );
    await page.goto(`${base}/#metals`);
    await page.waitForSelector("main table td");
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth + 1,
      ),
      false,
      "no sideways scrolling",
    );
    await page.screenshot({
      path: `/tmp/kestrel-arbitrage-${viewport.width}.png`,
      fullPage: true,
    });
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Arbitrage desktop/mobile: barcode check, paste fallback, card, gold vs melt, buy & hold, deals, settings passed.",
  );
} finally {
  await browser.close();
}
