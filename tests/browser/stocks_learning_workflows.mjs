// Disposable learning-workflows fixture only. No provider access or operator account data.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8891";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    const record = await (
      await page.request.get(`${base}/api/stocks/learning`)
    ).json();
    const main = record.accounts[0],
      reference = record.accounts[1];
    assert.ok(main.additional_funding > 0); // deposits from before the cap stay in the ledger
    assert.equal(main.cap.equity, main.contributed); // capped once at what it held; no deposits since
    assert.equal(main.funding_allowed, false);
    assert.equal(main.trading_pnl, main.equity - main.contributed);
    assert.equal(reference.additional_funding, 0);
    assert.ok(main.closed_trades > 0);
    await page.goto(`${base}/#learning`);
    await page
      .getByRole("heading", { name: "PAPER Learning", exact: true })
      .waitFor();
    const evidence = page.locator(".learning-evidence");
    assert.equal(
      record.model.version,
      0,
      "the fixture has completed outcomes before formal validation is ready",
    );
    assert.ok(record.evidence.matured_outcomes > 0);
    assert.equal(
      await evidence
        .locator(".paper-metric")
        .filter({ hasText: "Completed research outcomes" })
        .locator("strong")
        .innerText(),
      String(record.evidence.matured_outcomes),
    );
    assert.match(
      await evidence.innerText(),
      /Research results collected; formal comparison pending/,
    );
    assert.doesNotMatch(await evidence.innerText(), /0 training outcomes/);
    assert.equal(await evidence.locator("progress").count(), 2);
    assert.match(await evidence.innerText(), /Need \d+ more entry days/);
    assert.match(await evidence.innerText(), /Last completed outcome:/);
    await evidence.getByText(/Early experiment results/).click();
    assert.equal(
      await evidence.locator(".learning-family").count(),
      record.evidence.families.length,
    );
    assert.ok(
      await evidence.locator(".learning-family strong").first().isVisible(),
      "observed returns remain visible on phones as well as desktops",
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    await evidence.screenshot({
      path: `/tmp/kestrel-learning-evidence-${width}.png`,
    });
    await page
      .getByRole("button", { name: "Pause Learning", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Start Learning", exact: true })
      .waitFor();
    await page.reload();
    await page
      .getByRole("button", { name: "Start Learning", exact: true })
      .click();
    await page
      .getByRole("button", { name: "Pause Learning", exact: true })
      .waitFor();
    assert.match(
      await page.locator("#app").innerText(),
      /Trading P\/L — funding excluded/,
    );
    const chart = page.locator(".paper-equity svg");
    await chart.focus();
    await chart.press("Home");
    const first = await page.locator(".paper-equity .kc-readout").innerText();
    await chart.press("End");
    assert.notEqual(
      await page.locator(".paper-equity .kc-readout").innerText(),
      first,
    );
    await page
      .getByRole("button", { name: "Closed trades", exact: true })
      .click();
    await page.locator("button.paper-trade-card").first().click();
    const dialog = page.getByRole("dialog");
    await dialog
      .getByRole("heading", { name: "Why it was selected", exact: true })
      .waitFor();
    assert.match(await dialog.innerText(), /target debit/);
    await dialog.getByRole("button", { name: "Close", exact: true }).click();
    await page
      .getByRole("button", { name: "Orders & fills", exact: true })
      .click();
    await page
      .getByRole("heading", {
        name: "Requested, filled and unfilled quantities",
        exact: true,
      })
      .waitFor();
    await page.locator(".paper-trades .paper-trade-card").first().waitFor();
    assert.match(
      await page.locator(".paper-trades").innerText(),
      /requested.*filled.*unfilled/,
    );
    const nextOrders = page.getByRole("button", {
      name: "Next orders",
      exact: true,
    });
    await nextOrders.waitFor();
    if (await nextOrders.isEnabled()) {
      await nextOrders.click();
      await page
        .getByRole("button", { name: "Previous orders", exact: true })
        .click();
    }
    await page
      .getByRole("button", { name: "Virtual funding", exact: true })
      .click();
    await page.locator(".paper-pending").first().waitFor();
    assert.match(
      await page.locator(".paper-trades").innerText(),
      /excluded from trading P\/L/,
    );
    await page
      .getByRole("button", { name: "$500,000 reference", exact: true })
      .click();
    await page
      .getByText("Additional virtual funding", { exact: true })
      .waitFor();
    const fundingMetric = page
      .locator(".paper-metric")
      .filter({ hasText: "Additional virtual funding" });
    assert.equal(await fundingMetric.locator("strong").innerText(), "$0.00");
    await page.locator('button[data-account="uncapped_research"]').click();
    assert.match(
      await page
        .locator('button[data-account="uncapped_research"]')
        .innerText(),
      /^\$[\d,]+ research$/,
    );
    await page
      .getByRole("button", { name: "Experiments", exact: true })
      .click();
    const before = (
      await (await page.request.get(`${base}/api/stocks/learning`)).json()
    ).experiment_total;
    // A lifecycle recipe: a 6-month put held for a holding time no recipe has yet (the seed holds 120 days).
    let holdDays = 1 + ((width + before) % 200);
    if (holdDays === 120) holdDays += 1;
    await page
      .getByRole("combobox", { name: "Experiment structure", exact: true })
      .selectOption("long_put");
    await page
      .getByRole("combobox", { name: "Expiry", exact: true })
      .selectOption("6m");
    const holding = page.getByRole("spinbutton", {
      name: "Holding days",
      exact: true,
    });
    assert.equal(await holding.inputValue(), "120");
    assert.equal(await holding.getAttribute("max"), "210");
    await holding.fill(String(holdDays));
    await page.evaluate(async () => {
      snapshot.collection = {
        fixture: "force ordinary collection status refresh",
      };
      await refresh();
    });
    assert.equal(await holding.inputValue(), String(holdDays));
    await page
      .getByRole("button", { name: "Register recipe", exact: true })
      .click();
    await page
      .getByRole("heading", {
        name: `${before + 1} registered hypotheses`,
        exact: true,
      })
      .waitFor();
    await page
      .getByText(`About 6 months, held up to ${holdDays} days`, {
        exact: false,
      })
      .first()
      .waitFor();
    await page
      .getByText("Registered. The new recipe starts buying", { exact: false })
      .waitFor();
    await page.getByRole("button", { name: "Check-ins", exact: true }).click();
    await page.locator(".paper-walk").first().waitFor();
    const downloadPromise = page.waitForEvent("download");
    await page
      .getByRole("button", { name: "Download Markdown check-in", exact: true })
      .click();
    assert.equal(
      (await downloadPromise).suggestedFilename(),
      "kestrel-learning.md",
    );
    await page
      .getByRole("button", { name: "Orders & fills", exact: true })
      .click();
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
      true,
    );
    await page
      .getByRole("button", { name: "Next orders", exact: true })
      .waitFor();
    await page.evaluate(() => window.scrollTo(0, 0));
    assert.ok(
      await page.locator(".paper-metric strong").evaluateAll((nodes) =>
        nodes.every((node) => {
          const range = document.createRange();
          range.selectNodeContents(node);
          return range.getClientRects().length === 1;
        }),
      ),
      "Money figures must remain on one line at the tested widths",
    );
    await page.screenshot({ path: `/tmp/kestrel-learning-${width}.png` });
    await page.goto(`${base}/#options`);
    await page
      .getByRole("heading", {
        name: "Findings from PAPER Learning",
        exact: true,
      })
      .waitFor();
    assert.match(
      await page.locator(".learning-findings").innerText(),
      /No supported learning finding yet/,
    );
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log(
    "Learning desktop/mobile: accounting, persisted pause/resume, P/L chart, closed trades, original hypotheses, partial order quantities, funding, finite reference, lifecycle recipe registration and check-in downloads passed.",
  );
} finally {
  await browser.close();
}
