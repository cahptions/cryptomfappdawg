// The Learner page (#learner) at desktop and phone widths, against the disposable learner-account fixture (thirty
// synthetic sessions decided by the policy and stepped by the PAPER engine; no provider access):
//   uv run python -m tests.browser.stocks_fixture_server --scenario learner-account --port 8943
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8943";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  const request = await browser.newContext().then((c) => c.request);
  const data = await (
    await request.get(`${base}/api/stocks/learner/account`)
  ).json();
  // PAPER only; both accounts; each trade carries its decision; the limits are the documented ones.
  assert.match(data.label, /^PAPER only/);
  assert.deepEqual(Object.keys(data.accounts).sort(), [
    "learner",
    "learner_random",
  ]);
  assert.equal(data.policy.explore_share, 0.15);
  assert.equal(data.policy.trade_risk, 0.02);
  assert.ok(data.trades.length > 0);
  for (const t of data.trades) {
    assert.ok(t.decision && t.decision.reason && t.decision.exit_title);
    assert.ok(t.max_loss > 0);
    if (t.state === "CLOSED" && t.return !== null)
      assert.ok(t.return >= -1 - 1e-6);
    assert.ok(
      t.decision.risk_usd <= 0.02 * t.decision.equity_usd + 0.01,
      "a trade never risks more than 2% of equity",
    );
  }
  assert.ok(data.accounts.learner.curve.length > 0);
  assert.ok(data.baselines.spy.length > 0);
  assert.ok(data.models.length > 0 && data.promotion.length > 0);
  // The saved page shows the real gate's verdict (the synthetic model does not pass it).
  assert.equal(typeof data.gate.why, "string");
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(`${base}/#learner`);
    await page.getByRole("heading", { name: "Learner", exact: true }).waitFor();
    for (const name of [
      "The account",
      "Account value against the baselines",
      "Trades and why",
      "What it learned to do / not to do",
      "By market regime",
      "Model versions",
    ])
      await page.getByRole("heading", { name, exact: true }).waitFor();
    const status = await page.locator(".learner-status-box").innerText();
    assert.match(status, /exploit trades whose lower confidence bound/);
    assert.match(status, /15% of each session's 8% new-risk budget/);
    const trade = page.locator(".learner-trade").first();
    assert.match(await trade.innerText(), /(Exploit|Explore|Random baseline)/);
    assert.match(await trade.innerText(), /at risk/);
    assert.ok((await page.locator(".learner-chart-svg path").count()) >= 2);
    assert.match(
      await page.locator(".learner-legend").innerText(),
      /Learner: \$/,
    );
    // One column on a phone: nothing wider than the screen.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    assert.ok(overflow <= 1, `page overflows by ${overflow}px at ${width}`);
    if (width === 375) {
      const boxes = await page
        .locator(".learner-grid > section")
        .evaluateAll((n) => n.map((x) => x.getBoundingClientRect().left));
      assert.ok(boxes.every((left) => Math.abs(left - boxes[0]) < 2));
      // Reached from the More sheet.
      await page.goto(`${base}/#dashboard`);
      await page.locator("#more-button").click();
      await page.locator("#more-sheet a[href='#learner']").click();
      await page
        .getByRole("heading", { name: "Learner", exact: true })
        .waitFor();
    }
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log("stocks learner account: ok");
} finally {
  await browser.close();
}
