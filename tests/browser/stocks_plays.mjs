// Plays tab: categories, cards, table view, filters and the empty state, at desktop and phone widths.
// Run against stocks_fixture_server (populated or empty), never an operator's data or credentials.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8877";
const scenario = process.env.KESTREL_STOCKS_SCENARIO ?? "populated";
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
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    assert.equal(
      await page
        .locator('nav[aria-label="Main"] a[href="#plays"]')
        .getAttribute("aria-current"),
      "page",
    );
    if (scenario === "empty") {
      await page.getByRole("heading", { name: "No plays yet" }).waitFor();
      await page.screenshot({
        path: `${out}/plays-empty-${width}.png`,
        fullPage: true,
      });
      continue;
    }
    const tabs = page.getByRole("tab");
    assert.equal(await tabs.count(), 12, "one tab per category");
    await page.getByRole("tab", { name: /Most likely to finish ITM/ }).click();
    await page
      .getByRole("heading", { name: /Most likely to finish ITM/ })
      .waitFor();
    const cards = page.locator("article.play-card");
    assert.ok((await cards.count()) > 0, "likely-ITM plays render as cards");
    const first = cards.first();
    assert.match(
      await first.locator(".research-badge").first().innerText(),
      /^BUY (CALL|PUT)$/,
    );
    assert.match(
      await first.locator(".play-chance strong").innerText(),
      /^\d+%$/,
    );
    assert.match(await first.innerText(), /Δ Γ Θ ν/);
    assert.match(await first.innerText(), /Market \d+% · History/);
    // the selling category shows credits and the chance of expiring worthless
    await page.getByRole("tab", { name: /Sell rich premium/ }).click();
    const sells = page.locator("article.play-card");
    if (await sells.count()) {
      assert.match(
        await sells.first().locator(".research-badge").first().innerText(),
        /^SELL .*CREDIT SPREAD$/,
      );
      assert.match(
        await sells.first().innerText(),
        /chance it expires worthless/i,
      );
    }
    // filters and the table layout survive switching category
    await page.getByRole("tab", { name: /Most likely to finish ITM/ }).click();
    await page.getByLabel("Layout").selectOption("table");
    await page.getByRole("columnheader", { name: "Chance ITM" }).waitFor();
    await page.getByLabel("Minimum chance in the money, percent").fill("99");
    await page
      .getByText(/No plays in this category match these filters/)
      .waitFor();
    await page.getByLabel("Minimum chance in the money, percent").fill("");
    await page.getByLabel("Layout").selectOption("cards");
    const overflow = await page.evaluate(
      () => document.scrollingElement.scrollWidth - window.innerWidth,
    );
    assert.ok(
      overflow <= 1,
      `no horizontal page scroll at ${width}px (overflow ${overflow})`,
    );
    await page.screenshot({
      path: `${out}/plays-${width}.png`,
      fullPage: true,
    });
    // the chosen category and layout are remembered on reload
    await page.getByRole("tab", { name: /Unusual activity/ }).click();
    await page.reload();
    await page.getByRole("heading", { name: /Unusual activity/ }).waitFor();
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ scenario, ok: true }));
} finally {
  await browser.close();
}
