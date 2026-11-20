// Run against the paper-workflows fixture. Responses below simulate changing data and a slow local server.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8899";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});

try {
  for (const width of [375, 1366]) {
    const page = await browser.newPage({ viewport: { width, height: 780 } });
    // Make the actual polling code run more quickly without changing other timers.
    await page.addInitScript(() => {
      const original = window.setTimeout;
      window.setTimeout = (fn, delay, ...args) =>
        original(fn, [10000, 15000].includes(delay) ? 2000 : delay, ...args);
    });
    const fixture = await (
      await page.request.get(`${base}/api/stocks/paper`)
    ).json();
    const template = fixture.trades.find(
      (position) => position.state === "OPEN",
    );
    assert.ok(template, "the fixture has an open trade");
    const rows = Array.from({ length: 12 }, (_, i) => ({
      ...template,
      id: `position-${i}`,
      symbol: `T${String(i).padStart(2, "0")}`,
      plan: { ...template.plan, filled_units: 1, desired_units: 1 },
    }));
    for (const [name, endpoint, tradeEndpoint] of [
      ["paper", "paper", "paper/trades"],
      ["learning", "learning", "learning/trades"],
    ]) {
      let refreshes = 0,
        pages = 0;
      await page.route(`**/api/stocks/${endpoint}`, async (route) => {
        const response = await route.fetch();
        const json = await response.json();
        const marker = `Scroll check ${++refreshes}`;
        if (name === "paper") json.session_status.reason = marker;
        else json.progress.phase = marker;
        await route.fulfill({ response, json });
      });
      await page.route(`**/api/stocks/${tradeEndpoint}?*`, async (route) => {
        const response = await route.fetch();
        const json = await response.json();
        if (++pages > 1)
          await new Promise((resolve) => setTimeout(resolve, 700));
        json.total = rows.length;
        json[name === "paper" ? "trades" : "positions"] = rows;
        await route.fulfill({ response, json });
      });
      await page.goto(`${base}/#${name}`);
      const card = page.getByRole("button").filter({ hasText: "T08" }).last();
      await card.waitFor();
      await card.evaluate((node) => node.scrollIntoView({ block: "center" }));
      const top = await page.evaluate(() => scrollY);
      const offset = (await card.boundingBox()).y;
      await page.evaluate(() => {
        window.__scrollPositions = [];
        window.addEventListener("scroll", () =>
          window.__scrollPositions.push(scrollY),
        );
      });
      await page.getByText("Scroll check 2", { exact: true }).first().waitFor();
      assert.ok(pages >= 2);
      assert.ok(
        Math.abs((await card.boundingBox()).y - offset) <= 2,
        `${name} keeps the same trade in view`,
      );
      const recorded = await page.evaluate(() => [
        scrollY,
        ...window.__scrollPositions,
      ]);
      assert.ok(
        recorded.every((pos) => Math.abs(pos - top) <= 2),
        `${name} did not swap in an empty trade list: ${top} → ${recorded}`,
      );
      await page.unrouteAll({ behavior: "wait" });
    }

    // Force a changed snapshot and reveal a banner above the view while reading a heading.
    let revision = 0,
      banner = false;
    await page.route("**/api/stocks/snapshot", async (route) => {
      const response = await route.fetch();
      const json = await response.json();
      json.worker = { ...json.worker, finished_at: `test-${revision}` };
      if (banner) {
        json.connection = {
          ...json.connection,
          options_provider: "schwab",
          schwab: { credentials_set: true, relogin_required: true },
        };
      }
      await route.fulfill({ response, json });
    });
    for (const name of [
      "dashboard",
      "stocks",
      "options",
      "events",
      "plays",
      "plays/history",
      "plays/mine",
      "plays/performance",
      "paper",
      "learning",
      "learning/reference_1k",
      "learner",
      "research",
      "proof",
      "account",
      "system",
      "settings",
      "risk",
      "risk/reference_1k",
      "portfolio",
      "stock/SPY",
      "lists/momentum",
    ]) {
      banner = false;
      await page.goto(`${base}/#${name}`);
      await page.waitForFunction(() => {
        const h = document.querySelector("#app h1");
        return (
          h?.textContent.length > 0 && !h.textContent.startsWith("Loading")
        );
      });
      // Let progressively loaded sections settle before choosing the heading to follow.
      await page.waitForTimeout(400);
      const headings = page.locator("#app h2:visible");
      const count = await headings.count();
      const anchor = count
        ? headings.nth(Math.min(2, count - 1))
        : page.locator("#app h1").first();
      const title = await anchor.innerText();
      const level = await anchor.evaluate((node) =>
        Number(node.tagName.slice(1)),
      );
      await anchor.evaluate((node) => node.scrollIntoView({ block: "center" }));
      await page.waitForTimeout(400);
      const offset = (await anchor.boundingBox()).y;
      assert.ok(
        offset >= 0 && offset < 780,
        `${name} displays the selected heading before refresh`,
      );
      const scrolling = await page.evaluate(() => scrollY > 1);
      revision++;
      banner = true;
      await page.evaluate(() => refresh());
      await page
        .getByText(/Schwab sign-in expired/)
        .first()
        .waitFor();
      const next = await page
        .getByRole("heading", { name: title, exact: true, level })
        .first()
        .boundingBox();
      if (scrolling)
        assert.ok(
          Math.abs(next.y - offset) <= 3,
          `${name} at ${width}px shifted ${offset} → ${next.y}`,
        );
    }
    banner = false;
    await page.goto(`${base}/?scroll-check=1#dashboard`);
    await page.locator("#app h1").waitFor();
    await page.evaluate(() => {
      window.__heading = document.querySelector("#app h1");
    });
    revision++;
    await page.evaluate(async () => {
      const touchScrolling = setInterval(
        () => window.dispatchEvent(new Event("touchmove")),
        30,
      );
      try {
        window.dispatchEvent(new Event("touchmove"));
        await refresh();
      } finally {
        clearInterval(touchScrolling);
      }
    });
    assert.ok(
      await page.evaluate(
        () => window.__heading === document.querySelector("#app h1"),
      ),
      "a changed snapshot does not rebuild during scrolling",
    );

    let release, requested;
    const started = new Promise((resolve) => {
      requested = resolve;
    });
    const delayed = new Promise((resolve) => {
      release = resolve;
    });
    await page.route("**/api/stocks/home", async (route) => {
      requested();
      await delayed;
      await route.continue();
    });
    await page.waitForTimeout(350);
    revision++;
    const refreshInFlight = page.evaluate(() => refresh());
    await started;
    try {
      await page.evaluate(() => {
        location.hash = "#research";
      });
      await page
        .getByRole("heading", { name: "Research desk", exact: true })
        .waitFor();
    } finally {
      release();
      await refreshInFlight;
    }
    assert.equal(
      await page.locator("#app h1").innerText(),
      "Research desk",
      "a delayed refresh cannot replace a different page",
    );
    await page.close();
    console.log(
      `${width}px: slow PAPER and Learning refreshes plus a snapshot refresh on 22 routes`,
    );
  }
} finally {
  await browser.close();
}
