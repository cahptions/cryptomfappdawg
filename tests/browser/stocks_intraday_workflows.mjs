// Isolated intraday-workflows fixture: timestamped synthetic minute candles, no provider access.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8884";
const money = (v) =>
  new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(
    v,
  );
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 950 },
      hasTouch: width === 375,
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    for (const symbol of ["SPY", "QQQ"]) {
      const data = await (
        await page.request.get(`${base}/api/stocks/stock/${symbol}`)
      ).json();
      const minutes = data.stock_data.intraday.bars;
      assert.equal(minutes.length, 390);
      await page.goto(`${base}/#stock/${symbol}`);
      await page.getByRole("button", { name: "1D", exact: true }).click();
      const chart = page.locator("svg.price-chart"),
        headline = page.getByTestId("headline-price");
      assert.match(await chart.getAttribute("aria-label"), /1-minute intraday/);
      assert.equal(await page.locator(".volume-bar").count(), 390);
      assert.match(
        await page
          .locator(".kc-time-axis")
          .allTextContents()
          .then((v) => v.join(" ")),
        /9:30 AM ET/,
      );
      assert.match(
        await page
          .locator(".kc-time-axis")
          .allTextContents()
          .then((v) => v.join(" ")),
        /12:44 PM ET/,
      );
      assert.match(
        await page
          .locator(".kc-time-axis")
          .allTextContents()
          .then((v) => v.join(" ")),
        /3:59 PM ET/,
      );
      assert.equal(await headline.innerText(), money(minutes.at(-1).close));
      await chart.focus();
      await chart.press("Home");
      assert.equal(await headline.innerText(), money(minutes[0].close));
      const tooltip = page.getByTestId("candle-tooltip");
      assert.match(await tooltip.innerText(), /9:30 AM ET/);
      assert.match(
        await tooltip.innerText(),
        /Open[\s\S]*High[\s\S]*Low[\s\S]*Close[\s\S]*Volume/,
      );
      assert.match(
        await page.getByTestId("price-caption").innerText(),
        /1-minute close.*Change vs previous close/,
      );
      await chart.press("ArrowRight");
      assert.match(await tooltip.innerText(), /9:31 AM ET/);
      assert.equal(await headline.innerText(), money(minutes[1].close));
      await chart.press("Escape");
      assert.equal(await headline.innerText(), money(minutes.at(-1).close));
      if (width === 375) {
        const touch = await page.context().newCDPSession(page);
        const bounds = await chart.boundingBox();
        await touch.send("Input.dispatchTouchEvent", {
          type: "touchStart",
          touchPoints: [{ x: bounds.x + 2, y: bounds.y + 90 }],
        });
        assert.notEqual(
          await headline.innerText(),
          money(minutes.at(-1).close),
        );
        assert.match(
          await page.getByTestId("price-caption").innerText(),
          /1-minute close/,
        );
        await touch.send("Input.dispatchTouchEvent", {
          type: "touchEnd",
          touchPoints: [],
        });
        assert.equal(await headline.innerText(), money(minutes.at(-1).close));
        await touch.detach();
      }
      await page.getByRole("button", { name: "Candles", exact: true }).click();
      assert.equal(await page.locator(".candle-body").count(), 390);
      const box = await chart.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + 100);
      await tooltip.waitFor({ state: "visible" });
      const rect = await tooltip.boundingBox();
      assert.ok(
        rect.width > 100 &&
          rect.x >= box.x &&
          rect.x + rect.width <= box.x + box.width + 1,
      );
      assert.match(await tooltip.innerText(), /Schwab 1-minute OHLC/);
      await page.mouse.move(0, 0);
      await page.getByRole("button", { name: "1M", exact: true }).click();
      assert.equal(await page.locator(".candle-body").count(), 2);
      await page.getByRole("button", { name: "1D", exact: true }).click();
      assert.equal(await page.locator(".candle-body").count(), 390);
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
        true,
      );
      if (symbol === "QQQ")
        await page.screenshot({
          path: `/tmp/kestrel-intraday-${width}.png`,
          fullPage: true,
        });
    }
    await page.goto(`${base}/#stock/CHEAP`);
    await page.getByRole("button", { name: "1D", exact: true }).click();
    await page
      .getByRole("heading", { name: "Intraday data is loading", exact: true })
      .waitFor();
    assert.equal(
      await page.locator("svg.price-chart").count(),
      0,
      "Daily observations must never be relabelled as intraday",
    );
    await page.getByRole("button", { name: "1M", exact: true }).click();
    await page.locator("svg.price-chart").waitFor();
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log(
    "Desktop/mobile intraday: SPY and QQQ 1D line/candles, 390 minute bars, ET axis, hover/keyboard OHLC, headline prices, daily switching and honest missing-data state passed.",
  );
} finally {
  await browser.close();
}
