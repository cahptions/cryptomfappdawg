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
  // How strictly it is tested, in plain words: many held-out splits, luck from what it tried, ensemble vs single.
  const titles = data.evaluation.map((l) => l.title);
  for (const t of [
    "Expected fill vs paying the ask",
    "Many held-out splits",
    "After accounting for what it tried",
    "Ensemble vs single model",
  ])
    assert.ok(titles.includes(t), `missing "${t}" in ${titles}`);
  assert.ok(data.evaluation.every((l) => l.text && l.hint));
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
      "Learner picks",
      "Trades and why",
      "What it learned to do / not to do",
      "How strictly it is tested",
      "By market regime",
      "Model versions",
    ])
      await page.getByRole("heading", { name, exact: true }).waitFor();
    const status = await page.locator(".learner-status-box").innerText();
    assert.match(status, /exploit trades whose lower confidence bound/);
    assert.match(status, /15% of each session's 8% new-risk budget/);
    // Not graduated in this fixture: its picks are a preview on this page only.
    assert.equal(
      await page.locator(".learner-picks-state").innerText(),
      "Preview (shadow): not shown on Plays until it graduates.",
    );
    assert.ok((await page.locator(".learner-pick").count()) > 0);
    const strict = await page.locator(".learner-strict").innerText();
    assert.match(strict, /In \d+ held-out splits, \d+ were profitable/);
    assert.match(strict, /after accounting for the \d+ things it tried/);
    // Direction vs volatility: no weekly fit in this fixture, so it says plays are valued as before.
    const forecast = await page.locator(".learner-forecast").innerText();
    assert.match(forecast, /^Direction vs volatility/);
    assert.match(forecast, /No weekly fit yet: plays are valued as before/);
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
