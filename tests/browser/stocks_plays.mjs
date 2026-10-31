// Plays tab: categories, cards, table view, filters and the empty state, at desktop and phone widths.
// Run against stocks_fixture_server (populated or empty), never an operator's data or credentials.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8877";
const scenario = process.env.KESTREL_STOCKS_SCENARIO ?? "populated";
const out =
  process.env.KESTREL_BROWSER_OUTPUT ?? "/tmp/kestrel-browser-results";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    if (scenario !== "empty")
      await page.addInitScript(() => {
        if (!sessionStorage.getItem("legacySeeded")) {
          localStorage.setItem(
            "kestrel-plays",
            JSON.stringify({ maxCost: "475", category: "likely_itm" }),
          );
          sessionStorage.setItem("legacySeeded", "1");
        }
      });
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    assert.equal(
      await page
        .locator('nav[aria-label="Main"] a[href="#plays"]')
        .getAttribute("aria-current"),
      "page",
    );
    if (scenario === "empty") {
      await page.getByRole("heading", { name: "No plays yet" }).waitFor();
      await page.goto(`${base}/#plays/history`);
      await page
        .getByRole("heading", { name: "Nothing recorded yet" })
        .waitFor();
      await page.goto(`${base}/#plays/performance`);
      await page.getByRole("heading", { name: "Nothing graded yet" }).waitFor();
      await page.goto(`${base}/#plays`);
      await page.getByRole("heading", { name: "No plays yet" }).waitFor();
      await page.screenshot({
        path: `${out}/plays-empty-${width}.png`,
        fullPage: true,
      });
      continue;
    }
    const tabs = page.getByRole("tab");
    const data = await (
      await page.request.get(`${base}/api/stocks/plays`)
    ).json();
    assert.equal(data.categories.length, 17);
    assert.equal(
      await tabs.count(),
      data.categories.length,
      "one tab per category",
    );
    const rankedCategory = data.categories.find((c) => c.id === "likely_itm");
    // A render-only synthetic expensive play verifies All prices has no ceiling; actual pricing has Python coverage.
    const expensive = structuredClone(rankedCategory.plays[0]);
    Object.assign(expensive, {
      symbol: "HIGH",
      idea_id: "f".repeat(32),
      cost: 4000.65,
      max_loss: 4000.65,
      entry: 40,
      rank: 3,
    });
    rankedCategory.plays.push(expensive);
    rankedCategory.label = {
      state: "proven",
      text: "Synthetic unrestricted-only performance label",
    };
    rankedCategory.exit = {
      test_sessions: 20,
      mean_improvement: 0.1,
      best_in_sample: { rule: "Synthetic unrestricted-only exit" },
    };
    await page.route(`${base}/api/stocks/plays`, (route) =>
      route.fulfill({ json: data }),
    );
    await page.reload();
    await page.getByRole("tab", { name: /Most likely to finish ITM/ }).click();
    await page
      .getByRole("heading", { name: /Most likely to finish ITM/ })
      .waitFor();
    const cards = page.locator("article.play-card");
    assert.ok((await cards.count()) > 0, "likely-ITM plays render as cards");
    const first = cards.first();
    assert.equal(
      await page
        .getByText("Synthetic unrestricted-only performance label", {
          exact: true,
        })
        .count(),
      1,
    );
    assert.equal(
      await page.getByText(/Synthetic unrestricted-only exit/).count(),
      1,
    );
    assert.equal(
      await page.getByLabel("Maximum cost per contract").inputValue(),
      "",
    );
    assert.ok(
      (await page.locator(".play-filters").innerText()).includes(
        "Max cost / risk ($)",
      ),
    );
    assert.equal(
      await page
        .getByRole("button", { name: "All prices", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    assert.equal(
      await cards.filter({ hasText: "$4,000.65" }).count(),
      1,
      "expensive picks remain visible",
    );
    const cheapBand = page.locator(".play-price-band").filter({
      has: page.getByRole("heading", { name: "Up to $100", exact: true }),
    });
    assert.ok(
      (await cheapBand.locator(".play-price-candidate").count()) > 0,
      "cheap alternatives appear alongside expensive picks without a price filter",
    );
    await page.screenshot({
      path: `${out}/plays-all-prices-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: "Up to $100", exact: true }).click();
    assert.equal(
      await page
        .getByText("Synthetic unrestricted-only performance label", {
          exact: true,
        })
        .count(),
      0,
    );
    assert.equal(
      await page.getByText(/Synthetic unrestricted-only exit/).count(),
      0,
    );
    const affordable = page.locator("article.play-card");
    assert.ok(
      (await affordable.count()) > 0,
      "under-$100 candidates are searched before expensive picks win",
    );
    await page.screenshot({
      path: `${out}/plays-budget-100-${width}.png`,
      fullPage: true,
    });
    const selected = data.categories.find((c) => c.id === "likely_itm");
    const cheapIds = new Set(
      [...(selected.budget_plays || []), ...selected.plays]
        .filter((p) => p.cost <= 100)
        .map((p) => p.idea_id),
    );
    for (const url of await affordable
      .locator('a[href^="#plays/idea/"]')
      .evaluateAll((nodes) => nodes.map((n) => n.getAttribute("href"))))
      assert.ok(
        cheapIds.has(url.split("/").at(-1)),
        "visible exact idea respects whole-contract budget",
      );
    await page.getByRole("button", { name: "$100–$250", exact: true }).click();
    assert.match(
      await page.locator(".play-filter-summary").innerText(),
      /Cost \/ risk above \$100\.00/,
    );
    assert.ok((await affordable.count()) > 0);
    await page.getByLabel("Maximum cost per contract").fill("0.01");
    await page.reload();
    await page
      .getByRole("heading", {
        name: /No plays in this category match these filters/,
      })
      .waitFor();
    assert.equal(
      await page.getByLabel("Maximum cost per contract").inputValue(),
      "0.01",
    );
    assert.match(
      await page.locator(".play-filter-empty").innerText(),
      /Loading is complete/,
    );
    assert.match(
      await page.locator(".play-filter-empty").innerText(),
      /Maximum cost \/ risk \$0\.01 excludes/,
    );
    assert.match(
      await page.locator(".play-filter-empty").innerText(),
      /lowest cost \/ risk/,
    );
    assert.match(
      await page
        .getByRole("tab", { name: /Most likely to finish ITM/ })
        .innerText(),
      /\(0 of \d+\)/,
    );
    await page
      .getByRole("button", {
        name: "Clear filters and show the full ranking",
        exact: true,
      })
      .click();
    assert.equal(await cards.count(), selected.plays.length);
    await page
      .getByText("Synthetic unrestricted-only performance label", {
        exact: true,
      })
      .waitFor();
    await page.getByText(/Synthetic unrestricted-only exit/).waitFor();
    assert.equal(
      await page.getByLabel("Maximum cost per contract").inputValue(),
      "",
    );
    assert.equal(
      await page
        .getByRole("button", { name: "Clear filters", exact: true })
        .isDisabled(),
      true,
    );
    assert.match(
      await first.locator(".research-badge").first().innerText(),
      /^BUY (CALL|PUT)$/,
    );
    assert.match(
      await first.locator(".play-chance strong").innerText(),
      /^\d+%$/,
    );
    for (const name of [
      "Δ Delta",
      "Γ Gamma",
      "Θ Theta / day",
      "ν Vega / IV point",
    ])
      assert.ok((await first.innerText()).includes(name));
    assert.match(await first.innerText(), /Market \d+% · History/);
    // the selling category shows credits and the chance of expiring worthless
    await page.getByRole("tab", { name: /Sell rich premium/ }).click();
    const sells = page.locator("article.play-card");
    if (await sells.count()) {
      assert.match(
        await sells.first().locator(".research-badge").first().innerText(),
        /^SELL .*CREDIT SPREAD$/,
      );
      assert.match(
        await sells.first().innerText(),
        /chance it expires worthless/i,
      );
    }
    // filters and the table layout survive switching category
    await page.getByRole("tab", { name: /Most likely to finish ITM/ }).click();
    await page.getByLabel("Layout").selectOption("table");
    await page.getByRole("columnheader", { name: "Chance ITM" }).waitFor();
    await page.getByLabel("Minimum chance at expiry, percent").fill("99");
    await page
      .getByText(/No plays in this category match these filters/)
      .waitFor();
    await page.getByLabel("Minimum chance at expiry, percent").fill("");
    await page.getByLabel("Layout").selectOption("cards");
    const overflow = await page.evaluate(
      () => document.scrollingElement.scrollWidth - window.innerWidth,
    );
    assert.ok(
      overflow <= 1,
      `no horizontal page scroll at ${width}px (overflow ${overflow})`,
    );
    await page.screenshot({
      path: `${out}/plays-${width}.png`,
      fullPage: true,
    });
    // the chosen category and layout are remembered on reload
    await page.getByRole("tab", { name: /Unusual activity/ }).click();
    await page.reload();
    await page.getByRole("heading", { name: /Unusual activity/ }).waitFor();
    await page.evaluate(() => localStorage.removeItem("kestrel-plays"));
    await page.reload();
    await page
      .getByRole("heading", { name: /Most likely to finish ITM/ })
      .waitFor();
    assert.equal(
      await page.getByLabel("Maximum cost per contract").inputValue(),
      "",
      "fresh profiles also start with no price ceiling",
    );
    rankedCategory.plays = [];
    await page.reload();
    await page
      .getByText(
        "No picks in the overall ranking match these filters. Qualifying alternatives remain in the price ranges above.",
        { exact: true },
      )
      .waitFor();
    assert.ok(
      (await page.locator(".play-price-candidate").count()) > 0,
      "budget alternatives stay available even when the original shortlist is empty",
    );
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({ scenario, ok: true }));
} finally {
  await browser.close();
}
