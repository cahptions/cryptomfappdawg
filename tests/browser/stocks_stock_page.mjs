// The Robinhood-style stock page against --scenario stock-page: synthetic company context for S10, no provider calls.
//   uv run python -m tests.browser.stocks_fixture_server --scenario stock-page --port 8881
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8881";
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
    await page.goto(`${base}/#stock/S10`);
    await page
      .getByRole("heading", { name: "S10 Corp Common Stock", exact: true })
      .waitFor();
    const about = page.locator("#sp-about");
    await about.waitFor();

    // Header: price, today's change and the after-hours line.
    await page.getByText("Today", { exact: true }).waitFor();
    await page.getByText("After hours", { exact: true }).waitFor();

    // About: long descriptions open and close; facts carry their source.
    const text = about.locator(".sp-about-text");
    assert.ok(await text.evaluate((n) => n.classList.contains("clamped")));
    await about.getByRole("button", { name: "Show more" }).click();
    assert.ok(!(await text.evaluate((n) => n.classList.contains("clamped"))));
    await about.getByRole("button", { name: "Show less" }).waitFor();
    assert.equal(
      await about
        .locator(".sp-stat", { hasText: "Employees" })
        .locator("dd")
        .innerText(),
      "12,400",
    );
    assert.equal(
      await about
        .locator(".sp-stat", { hasText: "Listed" })
        .locator("dd")
        .innerText(),
      "May 14, 1998",
    );
    const website = about.getByRole("link", { name: /Website/ });
    assert.equal(await website.getAttribute("target"), "_blank");
    assert.match(await website.getAttribute("rel"), /noopener/);

    // Statistics merge Schwab sections with stored data.
    const stats = page.locator("#sp-stats");
    for (const label of [
      "Market cap",
      "Price-earnings ratio",
      "Average volume (10D)",
      "52 Week high",
      "Borrow",
    ])
      await stats.getByText(label, { exact: true }).waitFor();
    assert.equal(
      await stats
        .locator(".sp-stat", { hasText: "Average volume (10D)" })
        .locator("dd")
        .innerText(),
      "2.4M",
    );

    // Related lists, news with per-ticker sentiment, analyst ratings, earnings.
    assert.ok((await page.locator("#sp-lists a.chip").count()) >= 3);
    assert.match(
      await page.locator("#sp-lists a.chip").first().getAttribute("href"),
      /^#lists\//,
    );
    const news = page.locator("#sp-news .sp-news li");
    assert.equal(await news.count(), 3);
    await page.locator("#sp-news .sp-mood.positive").first().waitFor();
    assert.match(
      await page.locator("#sp-news .sp-news-title").first().getAttribute("rel"),
      /noreferrer/,
    );
    await page.getByText("of 26 analysts say buy", { exact: true }).waitFor();
    assert.equal(
      await page.locator("#sp-ratings .sp-ratings-hero strong").innerText(),
      "65%",
    );
    assert.equal(await page.locator("#sp-earnings .viz-dot.actual").count(), 4);
    assert.equal(
      await page.locator("#sp-earnings .viz-dot.actual.down").count(),
      1,
    );
    await page
      .locator("#sp-earnings .sp-next")
      .getByText(/after the close/)
      .waitFor();

    // Financials: quarterly by default, annual on request, values in the hover card.
    const financials = page.locator("#sp-financials");
    assert.ok((await financials.locator(".viz-group").count()) >= 4);
    const group = financials.locator(".viz-group").last();
    await group.focus();
    const tip = financials.locator(".viz-tip");
    await tip.waitFor({ state: "visible" });
    assert.match(
      await tip.innerText(),
      /Revenue[\s\S]*Gross profit[\s\S]*Net income[\s\S]*Net margin/,
    );
    await financials
      .getByRole("button", { name: "Annual", exact: true })
      .click();
    assert.match(
      await financials.locator(".viz-x span").first().innerText(),
      /^FY\d{4}$/,
    );

    // Short interest, insider activity and dividends.
    await page
      .locator("#sp-short")
      .getByText("Days to cover", { exact: true })
      .waitFor();
    assert.equal(
      await page
        .locator("#sp-insiders .sp-stat", { hasText: "Sold" })
        .first()
        .locator("dd")
        .innerText(),
      "$10M",
    );
    assert.equal(await page.locator("#sp-insiders .viz-bar.down").count(), 1);
    await page.locator("#sp-insiders summary").click();
    await page
      .locator("#sp-insiders table")
      .getByText("Sold", { exact: true })
      .waitFor();
    await page
      .locator("#sp-dividends")
      .getByText("Paid in the last 12 months", { exact: true })
      .waitFor();
    assert.match(
      await page.locator("#sp-dividends .sp-next-line").innerText(),
      /ex-dividend/,
    );

    // One-week range: five sessions of 10-minute candles.
    await page.getByRole("button", { name: "1W", exact: true }).click();
    assert.match(
      await page.locator("svg.price-chart").getAttribute("aria-label"),
      /10-minute/,
    );
    assert.match(
      await page.locator(".chart-readout").innerText(),
      /10-minute candles/,
    );
    await page.getByRole("button", { name: "YTD", exact: true }).click();
    await page.getByText("Year to date", { exact: true }).waitFor();

    // Sources are listed; on a phone the KESTREL card sits between the chart and the company sections.
    await page.getByText("Company data sources", { exact: true }).click();
    await page
      .getByText(/Schwab quote sections received: quote, fundamental/)
      .waitFor();
    if (width === 375) {
      const card = await page.locator(".committee-card").boundingBox(),
        aboutBox = await about.boundingBox(),
        chart = await page.locator("svg.price-chart").boundingBox();
      assert.ok(chart.y < card.y && card.y < aboutBox.y);
    }
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    await page.screenshot({
      path: `/tmp/kestrel-stock-page-${width}.png`,
      fullPage: true,
    });
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Stock page desktop/phone: about, statistics, lists, news, ratings, earnings, financials, short interest, insiders, dividends, 1W/YTD ranges and sources passed.",
  );
} finally {
  await browser.close();
}
