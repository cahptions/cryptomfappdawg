// Automatic history includes disappearing/late ideas and recovered sightings, without a single user selection.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8894";
const out =
  process.env.KESTREL_BROWSER_OUTPUT ?? "/tmp/kestrel-browser-results";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 950 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(`${base}/#plays/history/yesterday`);
    await page
      .getByRole("heading", { name: "Plays of Tue, Oct 27, 2026" })
      .waitFor();
    await page.getByText(/6 saved plays/).waitFor();
    assert.equal(await page.locator("a.pl-row").count(), 6);
    assert.match(
      await page.locator(".pl-history-coverage").innerText(),
      /2 in the closing snapshot/,
    );
    const session = await page.request.get(`${base}/api/stocks/plays/mine`);
    assert.equal(
      (await session.json()).total,
      0,
      "no clicks or confirmations needed for automatic history",
    );
    await page
      .getByRole("button", { name: "Closing snapshot", exact: true })
      .click();
    await page.waitForFunction(
      () => document.querySelectorAll("a.pl-row").length === 2,
    );
    assert.doesNotMatch(
      await page.locator(".pl-rows").innerText(),
      /Waiting for its first mark/,
    );
    await page
      .getByRole("button", { name: "All identified plays", exact: true })
      .click();
    await page.getByText(/6 saved plays/).waitFor();
    assert.equal(await page.locator("a.pl-row").count(), 6);
    assert.match(
      await page.locator("a.pl-row", { hasText: "AAPL" }).innerText(),
      /\+99\.0%/,
    );
    await page.locator("a.pl-row", { hasText: "AMZN" }).click();
    await page
      .getByRole("heading", {
        name: "Recovered original sighting",
        exact: true,
      })
      .waitFor();
    await page.locator(".pl-plot svg").waitFor();
    assert.match(
      await page.locator(".play-context").innerText(),
      /original rank and model probabilities were not retained/,
    );
    await page.goBack();
    await page.getByText(/6 saved plays/).waitFor();
    const overflow = await page.evaluate(
      () => document.scrollingElement.scrollWidth - innerWidth,
    );
    assert.ok(overflow <= 1, `no page overflow at ${width}px: ${overflow}`);
    await page.screenshot({
      path: `${out}/plays-all-identified-${width}.png`,
      fullPage: true,
    });
    await page.reload();
    await page.getByText(/6 saved plays/).waitFor();
    assert.equal(await page.locator("a.pl-row").count(), 6);
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "All identified plays / closing snapshot / recovered sightings passed at 1366 and 375px",
  );
} finally {
  await browser.close();
}
