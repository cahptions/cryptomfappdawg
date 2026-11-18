// Where the money came from: the Learning report's "What it learned from its own trades" section (by section, from
// stored quotes) and a graded play's detail view splitting its P/L into direction, volatility, time decay and costs,
// at 1366 and 375 wide.
//   uv run python -m tests.browser.stocks_fixture_server --scenario attribution --port 8887
// Synthetic plays only; no provider is contacted.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8887";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  const request = await browser.newContext().then((c) => c.request);
  const report = await (
    await request.get(`${base}/api/stocks/learning/report`)
  ).json();
  const own = report.sections.find((s) => s.id === "own_trades");
  assert.ok(own && own.items.length > 0, "the report splits its own trades");
  const where = own.items.find((i) =>
    i.headline.startsWith("Where the money came from — "),
  );
  assert.ok(where);
  assert.match(where.headline, / per contract\.$/);
  // A graded play with its split, found through the saved sessions' plays.
  const { sessions } = await (
    await request.get(`${base}/api/stocks/plays/sessions`)
  ).json();
  let target = null;
  for (const { session } of sessions) {
    if (target) break;
    const day = await (
      await request.get(
        `${base}/api/stocks/plays/history?when=${session}&scope=closing&section=all`,
      )
    ).json();
    for (const c of day.categories || [])
      for (const p of c.plays || []) {
        if (target) break;
        const v = await (
          await request.get(
            `${base}/api/stocks/plays/play?session=${session}&category=${c.id}&rank=${p.rank}`,
          )
        ).json();
        if (v.attribution) target = { session, category: c.id, rank: p.rank };
      }
  }
  assert.ok(target, "a graded play with a split");
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(`${base}/#learning`);
    const fold = page.locator(
      '.learning-report-section[data-section="own_trades"]',
    );
    await fold.waitFor();
    await fold.locator("summary").click();
    assert.match(
      await fold.innerText(),
      /What it learned from its own trades[\s\S]*Where the money came from — /,
    );
    await page.goto(
      `${base}/#plays/play/${target.session}/${target.category}/${target.rank}`,
    );
    const split = page.locator(".pl-attribution");
    await split.waitFor();
    assert.match(
      await split.innerText(),
      /^Where the money came from \(per contract\): [+−]\$[\d,]+ (from |costs)[\s\S]*the rest is shown as unexplained\.$/,
    );
    const scroll = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    assert.ok(scroll <= 1, `no sideways scroll at ${width} (${scroll})`);
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log("attribution: ok");
} finally {
  await browser.close();
}
