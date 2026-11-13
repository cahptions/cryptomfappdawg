// The Learner's shadow views on Plays and its Learning report section, at desktop and phone widths, against the
// disposable learner fixture (synthetic chains and a small model trained on them; no provider access):
//   uv run python -m tests.browser.stocks_fixture_server --scenario learner --port 8941
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8941";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  const request = await browser.newContext().then((c) => c.request);
  const shadow = await (await request.get(`${base}/api/stocks/learner`)).json();
  // Labelled shadow; a frozen model; a view of board plays; its own ideas ranked by its expected return.
  assert.match(shadow.label, /^Shadow/);
  assert.match(shadow.model.id, /^[0-9a-f]{16}$/);
  assert.ok(Object.keys(shadow.plays).length > 0);
  for (const v of Object.values(shadow.plays))
    for (const p of Object.values(v.p_profit))
      assert.ok(p === null || (p >= 0 && p <= 1));
  const ideas = shadow.ideas.items;
  assert.ok(ideas.length > 0);
  const expected = ideas.map((i) => i.learner.expected);
  assert.deepEqual(
    expected,
    [...expected].sort((a, b) => b - a),
  );
  assert.ok(shadow.volume.forward.sessions >= 40);
  // The board is the rules' own: a shadow view never adds, removes or reorders a play.
  const board = await (await request.get(`${base}/api/stocks/plays`)).json();
  const shown = board.categories.flatMap((c) => c.plays);
  assert.ok(shown.length > 0);
  for (const c of board.categories)
    c.plays.forEach((p, i) => assert.equal(p.rank, i + 1));
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    // Find a section whose cards carry the Learner's view.
    const tabs = page.locator(".play-tabs .play-tab:not(.play-tab-bankroll)");
    let found = false;
    for (let i = 0; i < (await tabs.count()) && !found; i++) {
      await tabs.nth(i).click();
      if ((await page.locator(".play-card").count()) === 0) continue;
      found = (await page.locator(".play-card .learner-view").count()) > 0;
    }
    assert.ok(found, "a play card shows the Learner's view");
    const view = page.locator(".play-card .learner-view").first();
    assert.match(await view.innerText(), /Learner's view · shadow/);
    assert.match(await view.innerText(), /not traded/);
    assert.match(await view.innerText(), /Profit chance \d+% at/);
    const panel = page.locator("section.learner-ideas");
    await panel.waitFor();
    assert.match(
      await panel.locator("h2").innerText(),
      /Learner's own top ideas \(shadow\)/,
    );
    assert.match(await panel.innerText(), /Not traded and not yet proven/);
    assert.equal(await panel.locator("table tr").count(), ideas.length + 1);
    const overflow = await page.evaluate(
      () => document.scrollingElement.scrollWidth - window.innerWidth,
    );
    assert.ok(
      overflow <= 1,
      `no horizontal page scroll at ${width}px (${overflow})`,
    );
    // The Learning report keeps the Learner in its own section.
    await page.goto(`${base}/#learning`);
    const section = page.locator(
      '.learning-report details[data-section="learner"]',
    );
    await section.waitFor();
    assert.match(
      await section.locator("summary").innerText(),
      /The Learner — its model, its PAPER account and what it learned/,
    );
    await section.locator("summary").click();
    assert.match(await section.innerText(), /trained/);
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Learner shadow views desktop/phone: labelled shadow, a view on play cards, its own ideas ranked, the board unchanged, the Learning report section and no overflow passed.",
  );
} finally {
  await browser.close();
}
