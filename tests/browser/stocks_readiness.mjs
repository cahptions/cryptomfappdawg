// Run only against stocks_fixture_server; simulated API states are not market evidence.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8878";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(String(error)));
    const original = await (
      await page.request.get(`${base}/api/stocks/snapshot`)
    ).json();
    for (const state of [
      {
        key: false,
        sessions: 0,
        running: false,
        recorded: false,
        title: "Connect stock history",
      },
      {
        key: true,
        sessions: 20,
        running: true,
        recorded: false,
        title: "Downloading research history",
      },
      {
        key: true,
        sessions: 20,
        running: false,
        recorded: false,
        title: "Downloading research history",
      },
      {
        key: true,
        sessions: 60,
        running: true,
        recorded: false,
        title: "First research lists pending",
      },
      {
        key: true,
        sessions: 60,
        running: true,
        recorded: true,
        title: "No qualifying names yet",
      },
    ]) {
      await page.route("**/api/stocks/snapshot", (route) =>
        route.fulfill({
          json: {
            ...original,
            asof: state.recorded ? "2026-10-20" : null,
            source_keys: {
              ...original.source_keys,
              MASSIVE_API_KEY: state.key,
            },
            worker_running: state.running,
            collection: { running: state.running, owned: false },
            price_progress: {
              sessions: state.sessions,
              minimum_sessions: 60,
              latest: "2026-10-20",
            },
            lists: original.lists.map((list) => ({
              ...list,
              rows: [],
              asof: state.recorded ? "2026-10-20" : null,
            })),
          },
        }),
      );
      await page.goto(
        `${base}/?readiness=${state.key}-${state.sessions}-${state.running}-${state.recorded}#dashboard`,
      );
      await page
        .getByRole("heading", { name: state.title, exact: true })
        .waitFor();
      const movers = page.locator(".movers-section");
      if (state.key && !state.recorded) {
        assert.equal(
          await movers.getByText("Connect data", { exact: true }).count(),
          0,
        );
        assert.match(
          await movers.innerText(),
          new RegExp(`${state.sessions} price sessions downloaded`),
        );
        if (state.sessions < 60)
          assert.match(await movers.innerText(), /not days you must wait/);
        if (state.running) {
          assert.equal(
            await movers
              .getByRole("link", { name: "View collection status" })
              .getAttribute("href"),
            "#system",
          );
        } else
          assert.match(
            await movers.innerText(),
            /PAPER and collection are stopped/,
          );
      }
      if (state.recorded)
        assert.equal(
          await movers
            .getByText("First research list pending", { exact: true })
            .count(),
          0,
        );
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
        true,
      );
      await page.unroute("**/api/stocks/snapshot");
    }
    assert.deepEqual(errors, []);
    await page.close();
    console.log(`Stocks readiness states passed at ${width}px`);
  }
} finally {
  await browser.close();
}
