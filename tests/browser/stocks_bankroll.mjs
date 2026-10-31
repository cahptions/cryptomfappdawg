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
    if (data.picks.length)
      assert.match(
        await page.locator(".bankroll-pick-head").first().innerText(),
        /^(Buy|Sell) \d+ ×/,
      );
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
