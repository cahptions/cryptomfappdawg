// Run only against the paper-workflows fixture. All trades, prices and balances are synthetic.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8883";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    const live = await (
      await page.request.get(`${base}/api/stocks/paper`)
    ).json();
    const withClosed = live.report.accounts.find((a) => a.trades > 0);
    assert.ok(
      withClosed,
      "Fixture must include an actual engine-generated closed trade",
    );
    await page.goto(`${base}/#paper`);
    await page
      .getByRole("heading", { name: "Paper trading", exact: true })
      .waitFor();
    await page.locator(".paper-trade-card").first().waitFor();
    assert.equal(await page.locator("#app table").count(), 0);
    const chart = page.locator(".paper-equity svg"),
      readout = page.locator(".paper-chart-readout");
    await chart.focus();
    await chart.press("Home");
    const first = await readout.innerText();
    await chart.press("End");
    assert.notEqual(await readout.innerText(), first);
    await page.locator(".paper-trade-card").first().click();
    const dialog = page.getByRole("dialog");
    await dialog
      .getByRole("heading", { name: "Position details", exact: true })
      .waitFor();
    await dialog
      .getByRole("heading", { name: "Why it was selected", exact: true })
      .waitFor();
    assert.equal(
      await page.evaluate(
        () =>
          document.querySelector("dialog").scrollWidth <=
          document.querySelector("dialog").clientWidth,
      ),
      true,
    );
    await page.screenshot({
      path: `/tmp/kestrel-paper-trade-${width}.png`,
      fullPage: true,
    });
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await page
      .getByRole("button", { name: "All strategies", exact: true })
      .click();
    await page
      .locator(".paper-strategy")
      .filter({ hasText: withClosed.title })
      .click();
    await page.getByRole("tab", { name: /Closed trades/ }).click();
    await page.locator(".paper-trade-card").first().waitFor();
    await page.locator(".paper-trade-card").first().click();
    await page
      .getByRole("dialog")
      .getByText("Realized P/L", { exact: true })
      .waitFor();
    await page
      .getByRole("dialog")
      .getByRole("button", { name: "Close", exact: true })
      .click();
    await page
      .getByRole("tab", { name: "Decision timeline", exact: true })
      .click();
    await page.locator(".paper-timeline article").first().waitFor();
    await page
      .getByRole("tab", { name: "Historical backtests", exact: true })
      .click();
    const previousReplay = (
      await (await page.request.get(`${base}/api/stocks/backtest`)).json()
    ).report?.generated_at;
    await page
      .getByLabel("Backtest window", { exact: true })
      .selectOption("63");
    const auto = page.getByRole("checkbox", {
      name: /Automatically refresh this backtest/,
    });
    await auto.uncheck();
    const run = page.getByRole("button", {
      name: "Run historical backtest",
      exact: true,
    });
    await run.click();
    await page
      .getByRole("button", { name: "Cancel backtest", exact: true })
      .waitFor();
    await page
      .getByRole("button", { name: "Cancel backtest", exact: true })
      .click();
    await run.waitFor({ state: "visible", timeout: 20000 });
    await run.click();
    await page
      .getByRole("heading", {
        name: "Walk-forward strategy checks",
        exact: true,
      })
      .waitFor({ timeout: 60000 });
    let completed = false;
    for (let attempt = 0; attempt < 60; attempt++) {
      const result = await (
        await page.request.get(`${base}/api/stocks/backtest`)
      ).json();
      if (
        !result.job.running &&
        result.job.status === "completed" &&
        result.report.generated_at !== previousReplay
      ) {
        completed = true;
        break;
      }
      await page.waitForTimeout(500);
    }
    assert.ok(
      completed,
      "The new replay, rather than an older saved report, must complete",
    );
    assert.ok((await page.locator(".paper-walk").count()) > 0);
    await page
      .getByRole("heading", {
        name: "Session-by-session strategy replay",
        exact: true,
      })
      .waitFor();
    await page
      .getByRole("button", { name: "First session", exact: true })
      .click();
    await page.getByText(/Session 1 of 63/, { exact: false }).waitFor();
    await page
      .getByText(/No open positions on this date/, { exact: false })
      .waitFor();
    assert.equal(
      await page.locator(".paper-replay .paper-trade-card").count(),
      0,
    );
    await page
      .getByRole("tab", { name: "Session activity", exact: true })
      .click();
    await page.getByRole("heading", { name: /Decisions on/ }).waitFor();
    await page
      .getByRole("button", { name: "Play from start", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Pause playback", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Latest session", exact: true })
      .click();
    await page
      .getByRole("tab", { name: "Open positions", exact: true })
      .click();
    await page.locator(".paper-replay .paper-trade-card").first().waitFor();
    await page.locator(".paper-replay .paper-equity svg").focus();
    await page.locator(".paper-replay .paper-equity svg").press("Home");
    await page.getByRole("tooltip").waitFor();
    await page.locator(".paper-replay .paper-equity svg").press("Escape");
    await page
      .getByText("Data coverage and replay assumptions", { exact: true })
      .click();
    assert.match(
      await page.locator("#app").innerText(),
      /historical option quotes cannot be invented/,
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
    );
    await page.getByRole("tab", { name: /Open positions/ }).click();
    await page.screenshot({
      path: `/tmp/kestrel-paper-history-${width}.png`,
      fullPage: true,
    });
    const after = await (
      await page.request.get(`${base}/api/stocks/paper`)
    ).json();
    assert.deepEqual(after.trades, live.trades);
    assert.deepEqual(after.decisions, live.decisions);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log(
    "Paper desktop/mobile: real trade cards/details, closed P/L, decision timeline, equity hover, automatic preference, replay start/cancel/completion and forward isolation passed.",
  );
} finally {
  await browser.close();
}
