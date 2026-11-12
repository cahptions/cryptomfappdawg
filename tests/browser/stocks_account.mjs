// My Schwab account against the disposable account fixture (synthetic positions and fills, no provider access):
//   uv run python -m tests.browser.stocks_fixture_server --scenario account --port 8941
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8941";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() === "error")
        errors.push(`${m.text()} (${m.location().url})`);
    });
    await page.goto(`${base}/#account`);
    const view = page.locator(".account-page");
    await view.locator(".account-table").waitFor();
    // Labelled as the real account, read only, in a PAPER-only app.
    const banner = await view.locator(".account-real").innerText();
    assert.match(banner, /real account · read only/i);
    assert.match(banner, /cannot place, change or\s+cancel an order/);
    assert.match(await page.locator("header .badge").innerText(), /PAPER/);
    // Positions: a stock with its committee view, options with odds, exit plan and IV.
    const rows = view.locator(".account-table tbody tr");
    assert.equal(await rows.count(), 4);
    const text = await view.locator(".account-table").innerText();
    assert.match(text, /Committee \d+ of 100/);
    assert.match(
      text,
      /Chance of a profit at expiry from your average price: \d+%/,
    );
    assert.match(text, /Exit plan: take profit at \+100%/);
    assert.match(text, /…4321/);
    // Only the last four digits ever appear; no hash or account number reaches the page.
    const html = await page.content();
    assert.doesNotMatch(html, /SYNTHETIC|hashValue|accountNumber/);
    // Decisions graded: early, with twins.
    const graded = view.locator(".account-graded");
    assert.match(
      await graded.locator(".account-early").innerText(),
      /^Early: 3 closed trades compared/,
    );
    assert.match(await graded.innerText(), /random/);
    // No sideways scroll at phone width.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    assert.ok(overflow <= 1, `page scrolls sideways by ${overflow}px`);
    await page.screenshot({
      path: `${process.env.SCREENSHOT_DIR || "/tmp"}/stocks-account-${width}.png`,
      fullPage: true,
    });
    assert.deepEqual(errors, []);
    await page.close();
  }
  // The Settings switch turns reading off (and on); Refresh now while signed out says so plainly.
  const page = await browser.newPage({
    viewport: { width: 1366, height: 1000 },
  });
  await page.goto(`${base}/#settings`);
  const toggle = page.locator("#read-account");
  await toggle.waitFor();
  await page.waitForFunction(
    () => document.querySelector("#read-account").checked,
  );
  await toggle.click();
  await page
    .locator("#account-settings [role=status]")
    .filter({ hasText: "Off." })
    .waitFor();
  await page.goto(`${base}/#account`);
  await page.getByText("Reading your account is off").waitFor();
  await page.goto(`${base}/#settings`);
  await page.waitForFunction(
    () => !document.querySelector("#read-account").checked,
  );
  await page.locator("#read-account").click();
  await page
    .locator("#account-settings [role=status]")
    .filter({ hasText: "On." })
    .waitFor();
  await page.goto(`${base}/#account`);
  await page.locator("#account-refresh").click();
  await page.locator('.account-state p[data-outcome="signed_out"]').waitFor();
  assert.match(
    await page.locator(".account-state").innerText(),
    /Sign in under Settings/,
  );
  // The saved positions stay on screen after a failed read.
  assert.equal(await page.locator(".account-table tbody tr").count(), 4);
  await page.close();
  console.log("stocks account: ok");
} finally {
  await browser.close();
}
