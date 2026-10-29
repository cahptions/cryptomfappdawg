// Synthetic Oct 28 -> Oct 29 market-hours update, without reload, at desktop and phone widths.
// Run against --scenario plays-history. API interception tests rendering; Python tests exercise actual stored quotes.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8897";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    const sessions = await (
      await page.request.get(`${base}/api/stocks/plays/sessions`)
    ).json();
    const session = sessions.sessions.at(-1).session;
    const history = await (
      await page.request.get(
        `${base}/api/stocks/plays/history?when=${session}&scope=closing`,
      )
    ).json();
    const category = history.categories.find((c) =>
      c.plays.some((p) => p.action === "BUY" && p.expiration > "2026-10-29"),
    );
    const play = category.plays.find(
      (p) => p.action === "BUY" && p.expiration > "2026-10-29",
    );
    const path = `plays/play?session=${session}&category=${category.id}&rank=${play.rank}`;
    const original = await (
      await page.request.get(`${base}/api/stocks/${path}`)
    ).json();
    let phase = 0,
      requests = 0;
    await page.route(`${base}/api/stocks/${path}`, async (route) => {
      requests++;
      if (phase === 2)
        return route.fulfill({
          status: 503,
          json: { detail: "Synthetic refresh unavailable" },
        });
      const data = structuredClone(original);
      const point = {
        t: phase ? "2026-10-29T14:59:00+00:00" : "2026-10-28",
        at: phase ? "2026-10-29T14:59:00+00:00" : "2026-10-28T20:00:00+00:00",
        status: phase ? "live" : "estimated",
        value: 3,
        underlying: 105,
      };
      for (const basis of Object.keys(data.bases))
        point[basis] = { pnl: phase ? 227.5 : -10, pct: phase ? 0.2 : -0.01 };
      (phase ? data.intraday : data.daily).push(point);
      for (const basis of Object.keys(data.bases))
        data.exits[basis] = {
          ...data.exits[basis],
          latest: { ...point, ...point[basis] },
          giveback: 0,
        };
      data.freshness = {
        state: phase ? "current" : "stale",
        market_open: true,
        quote_at: phase ? point.at : null,
        message: phase
          ? "Updated from timestamped option quotes during this session."
          : "Waiting for today's option quotes. The last saved value is not a current market price.",
      };
      return route.fulfill({ json: data });
    });
    await page.clock.install({ time: new Date("2026-10-29T15:00:00Z") });
    await page.goto(
      `${base}/#plays/play/${session}/${category.id}/${play.rank}`,
    );
    await page
      .locator(".pl-freshness")
      .getByText(/Waiting for today's option quotes/)
      .waitFor();
    assert.match(
      await page.locator(".pl-latest-exit").innerText(),
      /Last saved estimate[\s\S]*Oct 28/,
    );
    await page.getByRole("button", { name: "1D", exact: true }).click();
    await page.locator(".pl-contracts input").fill("3");
    const rangeBefore = await page
      .getByRole("group", { name: "Time range" })
      .locator('[aria-pressed="true"]')
      .innerText();
    phase = 1;
    await page.clock.fastForward(16000);
    await page
      .locator(".pl-latest-exit")
      .getByText(/682\.50/)
      .waitFor();
    assert.match(await page.locator(".pl-latest-exit").innerText(), /Oct 29/);
    assert.equal(await page.locator(".pl-contracts input").inputValue(), "3");
    assert.equal(
      await page
        .getByRole("group", { name: "Time range" })
        .locator('[aria-pressed="true"]')
        .innerText(),
      rangeBefore,
    );
    assert.match(
      await page.locator(".pl-freshness").innerText(),
      /timestamped option quotes/,
    );
    assert.ok(
      requests >= 2,
      "detail must poll instead of leaving yesterday's loaded response in place",
    );
    const retained = await page.locator(".pl-latest-exit").innerText();
    phase = 2;
    await page
      .getByRole("button", { name: "Refresh saved values", exact: true })
      .click();
    await page
      .locator(".pl-refresh-state")
      .getByText(/Refresh failed/)
      .waitFor();
    assert.equal(await page.locator(".pl-latest-exit").innerText(), retained);
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page
      .locator(".pl-chart")
      .screenshot({ path: `/tmp/kestrel-live-history-${width}.png` });
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    const stopped = requests;
    await page.clock.fastForward(32000);
    assert.equal(
      requests,
      stopped,
      "old detail polling stops after navigating away",
    );
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log(
    "History freshness desktop/phone: yesterday -> today's quote without reload, retained controls/quantity, failed refresh preserves values, polling stops on navigation.",
  );
} finally {
  await browser.close();
}
