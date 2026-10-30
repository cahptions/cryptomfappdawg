// --scenario learning-retail: dated synthetic prior research and genuine simulated small-account fills/results.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8896";
const out =
  process.env.KESTREL_BROWSER_OUTPUT || "/tmp/kestrel-browser-results";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 1000 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(`${base}/#learning`);
    await page
      .getByRole("heading", { name: "Small-account tests", exact: true })
      .waitFor();
    assert.equal(await page.locator(".learning-account-card").count(), 2);
    let data = await (
      await page.request.get(`${base}/api/stocks/learning`)
    ).json();
    assert.equal(data.accounts.length, 4);
    for (const id of ["reference_1k", "reference_5k"]) {
      const a = data.accounts.find((a) => a.id === id);
      assert.equal(a.additional_funding, 0);
      assert.equal(a.contributed, a.starting_capital);
      assert.ok(a.closed_trades > 0 && a.realized_pnl > 0);
      await page.getByRole("button", { name: a.title, exact: true }).click();
      await page
        .getByRole("heading", { name: a.title, exact: true, level: 2 })
        .waitFor();
      assert.match(
        await page.locator(".learning-transfer-policy").innerText(),
        /Provisional research findings/,
      );
      const funding = page
        .locator(".paper-metric")
        .filter({ hasText: "Additional virtual funding" });
      assert.equal(await funding.locator("strong").innerText(), "$0.00");
      await page
        .getByRole("button", { name: "Closed trades", exact: true })
        .click();
      await page.locator("button.paper-trade-card").first().click();
      const dialog = page.getByRole("dialog");
      await dialog
        .getByRole("heading", { name: "Why it was selected", exact: true })
        .waitFor();
      assert.match(
        await dialog.innerText(),
        /Prior uncapped research: long_put/,
      );
      assert.match(await dialog.innerText(), /underlying\/day blocks/);
      await dialog.getByRole("button", { name: "Close", exact: true }).click();
      await page
        .getByRole("button", { name: "Orders & fills", exact: true })
        .click();
      await page.locator(".paper-trades .paper-trade-card").first().waitFor();
      assert.match(
        await page.locator(".paper-trades").innerText(),
        /requested.*filled.*unfilled/,
      );
      const overflow = await page.evaluate(
        () => document.scrollingElement.scrollWidth - innerWidth,
      );
      assert.ok(overflow <= 1, `no overflow at ${width}: ${overflow}`);
      await page.screenshot({
        path: `${out}/learning-${id}-${width}.png`,
        fullPage: true,
      });
    }
    const before = JSON.stringify(
      data.accounts
        .filter((a) => a.small_account)
        .map((a) => [a.id, a.contributed, a.trading_pnl]),
    );
    await page.reload();
    await page
      .getByRole("heading", { name: "Small-account tests", exact: true })
      .waitFor();
    data = await (await page.request.get(`${base}/api/stocks/learning`)).json();
    assert.equal(
      JSON.stringify(
        data.accounts
          .filter((a) => a.small_account)
          .map((a) => [a.id, a.contributed, a.trading_pnl]),
      ),
      before,
    );
    if (width === 1366) {
      await page
        .getByRole("button", { name: "$5,000 account", exact: true })
        .click();
      const updated = structuredClone(data);
      updated.accounts.find((a) => a.id === "reference_5k").policy.phase =
        "Research evidence changed while account value stayed the same";
      const endpoint = `${base}/api/stocks/learning`;
      await page.route(endpoint, (route) => route.fulfill({ json: updated }));
      await page
        .getByText(
          "Research evidence changed while account value stayed the same",
          {
            exact: true,
          },
        )
        .waitFor({ timeout: 25000 });
      // Render-only late outcome: refresh evidence even when account balances/trade counts and model version stay fixed.
      assert.equal(updated.model.version, 0);
      updated.evidence.matured_outcomes += 1;
      await page
        .locator(".learning-evidence .paper-metric")
        .filter({ hasText: "Completed research outcomes" })
        .locator("strong")
        .getByText(String(updated.evidence.matured_outcomes), { exact: true })
        .waitFor({ timeout: 25000 });
      assert.match(
        await page.locator(".learning-evidence").innerText(),
        /formal comparison pending/,
      );
      await page.unroute(endpoint);
    }
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "$1k/$5k learned transfer accounts, actual results, trades, orders and restart persistence pass at 1366/375px",
  );
} finally {
  await browser.close();
}
