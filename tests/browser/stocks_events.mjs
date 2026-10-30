// Notable events against --scenario populated: the scan over synthetic history plus one planted sell-off day.
//   uv run python -m tests.browser.stocks_fixture_server --scenario populated --port 8877
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8877";
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
    await page.goto(`${base}/#events`);
    await page.getByRole("heading", { name: "Notable events" }).waitFor();
    assert.equal(
      await page.locator('nav a[href="#events"]').getAttribute("aria-current"),
      "page",
    );

    // Where things stand: distance from the 52-week high with its state.
    const tile = page.locator(".standing-tile").first();
    await tile.waitFor();
    assert.match(await tile.innerText(), /S&P 500[\s\S]*−\d+\.\d%/);
    assert.match(
      await tile.locator(".standing-meter").getAttribute("aria-label"),
      /from the 52-week high/,
    );

    // The planted day: a record stock drop with its headline, a basket with members, a broad sell-off.
    const stock = page.locator(".event-card", {
      hasText: "S10 Corp (S10) fell 13.1%",
    });
    await stock.waitFor();
    assert.equal(
      await stock.locator(".event-figure strong").innerText(),
      "−13.1%",
    );
    assert.match(await stock.innerText(), /Extreme/);
    const headline = stock.locator(".event-headlines a");
    assert.equal(await headline.getAttribute("target"), "_blank");
    assert.match(await headline.getAttribute("rel"), /noopener/);
    const basket = page.locator(".event-card", {
      hasText: "AI stocks fell 3.4%",
    });
    assert.equal(await basket.locator(".event-member").count(), 5);
    assert.equal(
      await basket.locator(".event-member").first().getAttribute("href"),
      "#stock/S10",
    );
    await page.locator(".event-card", { hasText: "Broad sell-off" }).waitFor();

    // Several indexes crossing the same rule on one day share a card.
    const together = page.locator(".event-card", { hasText: "Also" }).first();
    assert.ok((await together.locator(".event-member").count()) >= 1);

    // Filters: stocks only, then major only.
    await page.getByRole("button", { name: "Stocks", exact: true }).click();
    await page.locator(".event-results:not([aria-busy])").waitFor();
    await page.waitForFunction(
      () =>
        [...document.querySelectorAll(".event-card .event-meta")].length > 0 &&
        ![...document.querySelectorAll(".event-card h3")].some((h) =>
          /S&P 500|AI stocks|Broad/.test(h.textContent),
        ),
    );
    await page.getByLabel("Major only").check();
    await page.waitForFunction(() =>
      [...document.querySelectorAll(".event-card")].every((c) =>
        /severity-[23]/.test(c.className),
      ),
    );
    await page.getByRole("button", { name: "All", exact: true }).click();
    await page.getByLabel("Major only").uncheck();
    await page.getByText("How events are found").click();
    await page.getByText(/sessions judged/).waitFor();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );

    // Home sidebar: the latest events link to the stock or the log.
    await page.goto(`${base}/#dashboard`);
    const row = page.locator(".hub-events .event-row").first();
    await row.waitFor();
    assert.match(await row.innerText(), /S10 Corp \(S10\) fell 13\.1%/);
    assert.equal(await row.getAttribute("href"), "#stock/S10");

    // Stock page: the symbol's own entries.
    await page.goto(`${base}/#stock/S10`);
    const moves = page.locator("#sp-events");
    await moves.waitFor({ timeout: 30000 });
    await moves.getByText("S10 Corp (S10) fell 13.1%").waitFor();
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Notable events desktop/phone: standing, planted sell-off, headlines, basket members, grouped indexes, filters, method, Home rows and stock page section passed.",
  );
} finally {
  await browser.close();
}
