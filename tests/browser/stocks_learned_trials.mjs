// What the Learner tries next, on Plays: a confirmed "do" finding shown as a learned trial section (labelled a trial,
// with the finding's evidence and its forward record), a confirmed "don't" warning on the straddles it describes and
// the graduated Learner's picks section, at 1366 and 375 wide.
//   uv run python -m tests.browser.stocks_fixture_server --scenario learned-trials --port 8886
// Synthetic findings and chains only; no provider is contacted.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8886";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  const request = await browser.newContext().then((c) => c.request);
  const board = await (await request.get(`${base}/api/stocks/plays`)).json();
  const trial = board.categories.find((c) => c.id.startsWith("learned_"));
  assert.ok(trial, "the learned trial is a Plays section");
  assert.match(trial.title, /^Learned: Long calls expiring about a month/);
  assert.ok(trial.plays.length > 0);
  for (const p of trial.plays) assert.equal(p.structure, "long_call");
  const picks = board.categories.find((c) => c.id === "learner_picks");
  assert.ok(picks && picks.plays.length > 0 && picks.learner.graduated);
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    await page.getByRole("tab", { name: /Learned: Long calls/ }).click();
    await page
      .getByRole("heading", {
        name: /^🧪 Learned: Long calls expiring about a month/,
      })
      .waitFor();
    const box = page.locator(".play-trial");
    await box.waitFor();
    const text = await box.innerText();
    assert.match(text, /^Trial · something the Learner learned/);
    assert.match(text, /confirmed it|confirmed on later, untouched data/);
    assert.match(
      text,
      /Forward record so far: Plays: nothing graded yet; PAPER/,
    );
    assert.match(text, /fingerprint [0-9a-f]{16}/);
    const blurb = await page
      .locator(".play-body .section-heading p")
      .first()
      .innerText();
    assert.match(
      blurb,
      /Found on held-out data: \+12% average held to expiry over 30 days; confirmed on 12 later untouched days/,
    );
    assert.ok((await page.locator("article.play-card:visible").count()) > 0);
    assert.equal(await page.locator("article.play-card .play-dont").count(), 0);
    // The graduated Learner's picks: a real section, its banner and each card's own choice.
    await page.getByRole("tab", { name: /Learner picks/ }).click();
    await page.getByRole("heading", { name: "🤖 Learner picks" }).waitFor();
    assert.match(
      await page.locator(".play-learner").innerText(),
      /^Graduated · the Learner's own picks\s+Graduated \d{4}-\d{2}-\d{2}: this version beat its random twin/,
    );
    const pick = page
      .locator("article.play-card:visible .play-learner-pick")
      .first();
    assert.match(
      await pick.innerText(),
      /^Learner's choice: .+ · exploit · its exit rule: /,
    );
    // The "don't": every straddle in Cheap volatility carries the warning.
    await page.getByRole("tab", { name: /Cheap volatility/ }).click();
    const cards = page.locator("article.play-card:visible");
    await cards.first().waitFor();
    const n = await cards.count();
    assert.ok(n > 0);
    for (let i = 0; i < n; i++) {
      const w = cards.nth(i).locator(".play-dont");
      assert.match(
        await w.innerText(),
        /^⚠ Learned "don't": The Learner found that long straddles did worse than a random candidate/,
      );
    }
    const scroll = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    assert.ok(scroll <= 1, `no sideways scroll at ${width} (${scroll})`);
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log("learned trials: ok");
} finally {
  await browser.close();
}
