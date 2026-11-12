// My bankroll, confidence and exit plans against --scenario populated: the amount and level are remembered, picks
// keep their limits, and every card says how far to trust it and when to leave.
//   uv run python -m tests.browser.stocks_fixture_server --scenario populated --port 8877
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8877";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({
      viewport: { width, height: 1000 },
      hasTouch: width === 390,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await page.goto(`${base}/#plays`);

    // Every card: five confidence checks with reasons, and an exit plan with a take-profit, a stop and a date.
    await page.getByRole("tab", { name: /Iron condors/ }).click();
    const card = page.locator("article.play-card").first();
    await card.waitFor();
    const meter = card.locator(".play-confidence");
    assert.match(await meter.locator("summary").innerText(), /\d\/5/);
    await meter.locator("summary").click();
    assert.equal(await meter.locator("li").count(), 5);
    const exit = card.locator(".play-exit");
    assert.match(
      await exit.innerText(),
      /Take profit[\s\S]*Stop[\s\S]*Leave by/,
    );

    // My bankroll: choose an amount and level; picks respect the per-play and total limits; the choice is kept.
    await page.getByRole("tab", { name: "💼 My bankroll" }).click();
    assert.equal(await page.locator(".play-filters").isVisible(), false);
    const amount = page.getByRole("spinbutton", {
      name: "Bankroll in dollars",
    });
    await amount.fill("5000");
    await page
      .getByRole("combobox", { name: "Risk level" })
      .selectOption("bold");
    const answered = page.waitForResponse((r) =>
      r.url().includes("/api/stocks/plays/bankroll?amount=5000&level=bold"),
    );
    await page.getByRole("button", { name: "Pick my plays" }).click();
    const data = await (await answered).json();
    await page.locator(".bankroll-summary").waitFor();
    assert.equal(
      await page.locator(".bankroll-pick").count(),
      data.picks.length,
    );
    assert.ok(data.picks.every((p) => p.risk <= data.per_play_cap));
    assert.ok(data.summary.risk <= data.risk_cap);
    if (data.picks.length) {
      assert.match(
        await page.locator(".bankroll-pick-head").first().innerText(),
        /^(Buy|Sell) \d+ ×/,
      );
      // every pick says how it was sized; the fixture has no calibrated group, so none uses Kelly
      for (const text of await page.locator(".bankroll-sizing").allInnerTexts())
        assert.match(text, /^Sized by the level's limits: .+\.$/);
      assert.ok(data.picks.every((p) => p.sizing.basis === "fixed"));
    }
    // a pick sized from a calibrated chance shows its quarter-Kelly basis
    await page.route("**/api/stocks/plays/bankroll?*", async (route) => {
      const value = await (await route.fetch()).json();
      if (value.picks.length)
        value.picks[0].sizing = {
          basis: "quarter_kelly",
          chance: 0.6,
          calibration: "this category",
          win: 0.5,
          loss: 0.5,
          kelly: 0.4,
          fraction: 0.1,
          contracts: 9,
          capped: true,
        };
      value.kelly_passed = [
        {
          symbol: "ZZZ",
          category: "edge",
          kelly: -0.2,
          reason: "no edge at its calibrated chance",
        },
      ];
      await route.fulfill({ json: value });
    });
    await page.getByRole("button", { name: "Pick my plays" }).click();
    await page.locator(".bankroll-kelly-passed", { hasText: "ZZZ" }).waitFor();
    if (data.picks.length)
      assert.match(
        await page.locator(".bankroll-sizing").first().innerText(),
        /^Sized at a quarter Kelly: calibrated take-profit chance 60% \(this category\).*10\.0% of the amount; capped by the limits from 9 contracts\.$/,
      );
    await page.unroute("**/api/stocks/plays/bankroll?*");
    await page.reload();
    await page.locator(".bankroll-summary").waitFor();
    assert.equal(await amount.inputValue(), "5000");
    assert.equal(
      await page.getByRole("combobox", { name: "Risk level" }).inputValue(),
      "bold",
    );
    // With a bankroll saved, other tabs size each play for it.
    await page.getByRole("tab", { name: /Iron condors/ }).click();
    await card.waitFor();
    assert.match(
      await card.locator(".play-size").innerText(),
      /For your \$5,000/,
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Bankroll desktop/phone: confidence checks, exit plans, sized picks within limits, remembered amount and per-card sizes passed.",
  );
} finally {
  await browser.close();
}
