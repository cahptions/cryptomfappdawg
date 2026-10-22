// Run against stocks_fixture_server, never an operator's Settings/credentials.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8877";
const scenario = process.env.KESTREL_STOCKS_SCENARIO ?? "populated";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
const expectedForbidden = new Map();
const expectForbidden = (url) =>
  expectedForbidden.set(url, (expectedForbidden.get(url) || 0) + 1);
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: width === 375 });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() !== "error") return;
      const url = m.location().url,
        expected = expectedForbidden.get(url) || 0;
      if (
        expected &&
        m.text() ===
          "Failed to load resource: the server responded with a status of 403 (Forbidden)"
      ) {
        expectedForbidden.set(url, expected - 1);
      } else errors.push(`${m.text()} (${url})`);
    });
    await page.goto(base);
    await page.getByRole("heading", { name: "Market overview" }).waitFor();
    await page.getByRole("heading", { name: "Market & options top 10" }).waitFor();
    await page.getByRole("heading", { name: "Options intelligence" }).waitFor();
    await page.getByRole("tab", { name: "Rising trends" }).click();
    await page
      .getByRole("heading", { name: "Rising trends", exact: true })
      .waitFor();
    await page.getByRole("tab", { name: "Top gainers" }).click();
    await page.getByRole("heading", { name: "Top 10 gainers" }).waitFor();
    assert.equal(await page.locator(".benchmark-card").count(), 6);
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
    );
    await page.screenshot({
      path: `/tmp/kestrel-hub-${scenario}-${width}.png`,
      fullPage: true,
    });
    if (scenario === "populated") {
      await page.goto(`${base}/#stock/CHEAP`);
      await page
        .getByRole("heading", { name: "CHEAP Corp Common Stock" })
        .waitFor();
      await page.locator(".price-path").waitFor();
      assert.equal(await page.locator(".desk-card").count(), 11);
      const price = page.getByTestId("headline-price"), caption = page.getByTestId("price-caption");
      const latestPrice = await price.innerText();
      const chart = page.locator("svg.price-chart");
      await chart.focus();
      await chart.press("Home");
      assert.notEqual(await price.innerText(), latestPrice);
      assert.match(await caption.innerText(), /Change since/);
      await chart.press("Escape");
      assert.equal(await price.innerText(), latestPrice);
      const box = await chart.boundingBox();
      await page.mouse.move(box.x + 3, box.y + 100);
      assert.notEqual(await price.innerText(), latestPrice);
      await page.mouse.move(box.x, box.y - 10);
      assert.equal(await price.innerText(), latestPrice);
      if (width === 375) {
        const touch = await page.context().newCDPSession(page);
        await touch.send("Input.dispatchTouchEvent", {type: "touchStart", touchPoints: [{x: box.x + 3, y: box.y + 100}]});
        assert.notEqual(await price.innerText(), latestPrice);
        await touch.send("Input.dispatchTouchEvent", {type: "touchEnd", touchPoints: []});
        assert.equal(await price.innerText(), latestPrice);
        await touch.detach();
      }

      await page.getByRole("button", { name: "Candles", exact: true }).click();
      assert.ok((await page.locator(".candle-body").count()) > 0);
      await page.getByRole("tab", { name: "Puts", exact: true }).click();
      await page
        .getByRole("button", { name: "+ Add to watchlist", exact: true })
        .click();
      await page
        .getByRole("button", { name: "✓ In your watchlist", exact: true })
        .waitFor();
      await page.screenshot({
        path: `/tmp/kestrel-stock-${scenario}-${width}.png`,
        fullPage: true,
      });
      await page.goto(`${base}/#dashboard`);
      await page
        .locator(".watch-row")
        .getByRole("link", { name: "CHEAP", exact: true })
        .waitFor();
      await page.goto(`${base}/#stock/CHEAP`);
      await page
        .getByRole("button", { name: "✓ In your watchlist", exact: true })
        .click();
      await page
        .getByRole("button", { name: "+ Add to watchlist", exact: true })
        .waitFor();
      await page.goto(`${base}/#stock/SPY`);
      await page.getByText("ETF market/options profile", {exact: true}).waitFor();
      await page.getByText("ETF PROFILE", {exact: true}).waitFor();
      await page.getByRole("heading", {name: "ETF price & risk statistics", exact: true}).waitFor();
      assert.equal(await page.locator(".desk-card").count(), 3);
      assert.equal(await page.getByText("Market cap", {exact: true}).count(), 0);
      await page.screenshot({path: `/tmp/kestrel-spy-${scenario}-${width}.png`, fullPage: true});
      await page.locator("svg.price-chart").waitFor();
    }
    await page.goto(`${base}/#options`);
    await page.getByRole("heading", { name: "Options overview" }).waitFor();
    await page.goto(`${base}/#lists/gainers`);
    await page.getByRole("heading", { name: "Find the evidence." }).waitFor();
    assert.equal(
      await page.getByRole("combobox", { name: "Research list" }).inputValue(),
      "gainers",
    );
    await page.goto(`${base}/#paper`);
    await page.getByRole("heading", { name: "Paper portfolios." }).waitFor();
    if (scenario === "populated")
      await page.getByRole("cell", { name: "Hold SPY", exact: true }).waitFor();
    else
      await page
        .getByRole("heading", { name: "The forward record starts here" })
        .waitFor();
    await page.goto(`${base}/#proof`);
    await page
      .getByRole("heading", { name: "Evidence before confidence." })
      .waitFor();
    await page.goto(`${base}/#system`);
    await page.getByRole("heading", { name: "System.", exact: true }).waitFor();
    await page.goto(`${base}/#settings`);
    await page.getByRole("heading", { name: "Connect your data." }).waitFor();
    await page.getByRole("heading", { name: "Authorization status" }).waitFor();
    assert.equal(
      await page.locator("#app-key").getAttribute("type"),
      "password",
    );
    assert.equal(
      await page.locator("#app-secret").getAttribute("type"),
      "password",
    );
    await page
      .getByRole("heading", { name: "Start here · free stock research" })
      .waitFor();
    assert.equal(
      await page.locator("#massive-key").getAttribute("type"),
      "password",
    );
    assert.equal(
      await page.locator("#sec-contact").getAttribute("type"),
      "email",
    );
    assert.equal(
      await page.locator("#tradier-token").getAttribute("type"),
      "password",
    );
    // Fixture data directory only. No actual provider is contacted or collector started.
    await page.locator("#massive-key").fill("browser-fixture-key");
    await page.locator("#sec-contact").fill("fixture@example.org");
    await page.getByRole("button", { name: "Save research sources" }).click();
    await page
      .getByText(
        "Saved privately. Running PAPER picks up changes on its next pass.",
        {
          exact: false,
        },
      )
      .waitFor();
    assert.equal(await page.locator("#massive-key").inputValue(), "");
    assert.equal(await page.locator("#sec-contact").inputValue(), "");
    assert.equal(
      await page
        .getByRole("button", { name: "Start PAPER", exact: true })
        .count(),
      1,
    );
    // Exercise controls without spawning a real collector or making provider requests.
    let collection = {
      owned: false,
      running: false,
      stopping: false,
      exit_code: null,
    };
    await page.route("**/api/stocks/snapshot", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      body.collection = collection;
      body.worker_running = collection.running;
      await route.fulfill({ response, json: body });
    });
    await page.route("**/api/stocks/research/start", async (route) => {
      collection = {
        owned: true,
        running: true,
        stopping: false,
        exit_code: null,
      };
      await route.fulfill({ json: collection });
    });
    await page
      .getByRole("button", { name: "Start PAPER", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Pause PAPER", exact: true })
      .waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Save privately", exact: true })
        .isDisabled(),
      true,
    );
    await page.locator("#massive-key").fill("unfinished-private-edit");
    await page.locator("#app-key").fill("browser-fixture-schwab-key");
    await page.locator("#app-secret").fill("browser-fixture-schwab-secret");
    await page.route("**/api/stocks/research/pause", async (route) => {
      collection = {
        owned: false,
        running: false,
        stopping: false,
        exit_code: 0,
      };
      await route.fulfill({ json: collection });
    });
    await page.getByRole("button", { name: "Pause collector to save" }).click();
    await page
      .getByRole("button", { name: "Start PAPER", exact: true })
      .waitFor();
    assert.equal(
      await page.locator("#app-key").inputValue(),
      "browser-fixture-schwab-key",
    );
    assert.equal(
      await page.locator("#app-secret").inputValue(),
      "browser-fixture-schwab-secret",
    );
    assert.equal(
      await page
        .getByRole("button", { name: "Save privately", exact: true })
        .isEnabled(),
      true,
    );
    assert.equal(
      await page.locator("#massive-key").inputValue(),
      "unfinished-private-edit",
      "polling must preserve unsaved form values",
    );
    // A restart invalidates the old token: only the specific rejected-session response is retried, once.
    let attempts = 0,
      pendingLogin = false;
    await page.route("**/api/stocks/connection", async (route) => {
      if (route.request().method() !== "POST") {
        const response = await route.fetch();
        const body = await response.json();
        body.login = { pending: pendingLogin, error: null };
        return route.fulfill({ response, json: body });
      }
      attempts += 1;
      if (attempts === 1) {
        expectForbidden(route.request().url());
        return route.fulfill({
          status: 403,
          json: { detail: "valid dashboard session required" },
        });
      }
      return route.continue();
    });
    await page.locator("#provider").selectOption("schwab");
    await page
      .getByRole("button", { name: "Save privately", exact: true })
      .click();
    await page
      .getByText("Saved privately. Start browser authorization next.", {
        exact: true,
      })
      .waitFor();
    assert.equal(attempts, 2);
    assert.equal(await page.locator("#app-key").inputValue(), "");
    assert.equal(await page.locator("#app-secret").inputValue(), "");
    assert.match(
      await page.locator("#app-secret-presence").textContent(),
      /Saved on this laptop/,
    );
    assert.equal(
      (await page.locator("body").innerText()).includes(
        "browser-fixture-schwab-secret",
      ),
      false,
    );
    await page.route("**/api/stocks/connect", async (route) => {
      pendingLogin = true;
      await route.fulfill({
        json: {
          authorize_url:
            "https://api.schwabapi.com/v1/oauth/authorize?client_id=fixture",
          expires_in_seconds: "300",
        },
      });
    });
    await page.route("**/api/stocks/connect/cancel", async (route) => {
      pendingLogin = false;
      return route.continue();
    });
    await page
      .getByRole("button", { name: "Start browser authorization", exact: true })
      .click();
    await page
      .getByRole("link", { name: "Open Schwab authorization", exact: true })
      .waitFor();
    await page
      .getByRole("button", {
        name: "Cancel pending authorization",
        exact: true,
      })
      .click();
    await page
      .getByText(
        "Authorization cancelled. Correct Client ID and Client Secret, then Save privately and start a new authorization.",
        { exact: true },
      )
      .waitFor();
    assert.equal(
      await page
        .getByRole("link", { name: "Open Schwab authorization", exact: true })
        .count(),
      0,
    );
    // The cancellation message precedes the final status refresh; await actual control readiness.
    await page
      .getByRole("button", { name: "Start browser authorization", exact: true })
      .click({ trial: true });
    assert.equal(
      await page
        .getByRole("button", {
          name: "Start browser authorization",
          exact: true,
        })
        .isEnabled(),
      true,
    );
    let denied = 0;
    await page.route("**/api/stocks/sources", async (route) => {
      denied += 1;
      expectForbidden(route.request().url());
      await route.fulfill({
        status: 403,
        json: { detail: "same-origin requests required" },
      });
    });
    await page.getByRole("button", { name: "Save research sources" }).click();
    await page
      .getByText("same-origin requests required", { exact: true })
      .waitFor();
    assert.equal(denied, 1, "other 403 failures must not trigger retries");
    assert.equal(
      await page.locator("#massive-key").inputValue(),
      "unfinished-private-edit",
    );
    await page.locator("#massive-key").fill("");
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
      "no horizontal page overflow, including Settings on a phone",
    );
    await page.screenshot({
      path: `/tmp/kestrel-stocks-${scenario}-${width}.png`,
      fullPage: true,
    });
    await page.close();
  }
  assert.deepEqual(errors, []);
  assert.equal(
    [...expectedForbidden.values()].reduce((a, b) => a + b, 0),
    0,
  );
  console.log(
    `Stocks ${scenario}: desktop/phone navigation, chart, desk coverage, PAPER, Settings and CSP passed.`,
  );
} finally {
  await browser.close();
}
