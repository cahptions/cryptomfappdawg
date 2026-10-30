// The personal layer against --scenario populated: the Home cockpit and the numbers-first toggle (saved per desk).
//   uv run python -m tests.browser.stocks_fixture_server --scenario populated --port 8877
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8877";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
const numbersFirst = (page) =>
  page.evaluate(() => document.body.classList.contains("numbers-first"));
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({
      viewport: { width, height: 900 },
      hasTouch: width === 390,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() === "error")
        errors.push(`${m.text()} (${m.location().url})`);
    });
    await page.goto(`${base}/#dashboard`);

    // Cockpit: your plays, the market (six index cards) and today's top ideas on one screen.
    const cockpit = page.locator(".cockpit");
    await cockpit.waitFor();
    await cockpit.getByText("YOUR PLAYS", { exact: true }).waitFor();
    assert.equal(await cockpit.locator(".benchmark-card").count(), 6);
    await cockpit
      .getByRole("heading", { name: "Top ideas", exact: true })
      .waitFor();
    const idea = cockpit.locator(".cockpit-ideas .cockpit-row").first();
    assert.match(await idea.getAttribute("href"), /^#plays\/idea\/[a-f0-9]+/);
    assert.match(await idea.innerText(), /\d+%/);
    await cockpit.locator(".cockpit-event").waitFor();
    assert.match(
      await page.locator("h1").first().innerText(),
      /^(Good (morning|afternoon|evening)|Late night)$/,
    );

    // Numbers first: the fixture shows explanations; turning them off hides captions and is remembered.
    assert.equal(await numbersFirst(page), false);
    const caption = page.locator("#app p.caption").first();
    await caption.waitFor();
    const saved = page.waitForResponse(
      (r) => r.url().endsWith("/api/stocks/ui") && r.status() === 200,
    );
    if (width === 390) {
      await page.getByRole("button", { name: "More", exact: true }).click();
      await page.getByRole("switch", { name: "Show explanations" }).uncheck();
    } else
      await page
        .getByRole("button", { name: "Show explanations", exact: true })
        .click();
    await saved;
    assert.equal(await numbersFirst(page), true);
    assert.equal(await caption.isVisible(), false);
    await page.reload();
    await page.locator(".cockpit").waitFor();
    await page.waitForFunction(() =>
      document.body.classList.contains("numbers-first"),
    );
    const restored = page.waitForResponse(
      (r) => r.url().endsWith("/api/stocks/ui") && r.status() === 200,
    );
    if (width === 390) {
      await page.getByRole("button", { name: "More", exact: true }).click();
      await page.getByRole("switch", { name: "Show explanations" }).check();
    } else
      await page
        .getByRole("button", { name: "Show explanations", exact: true })
        .click();
    await restored;
    assert.equal(await numbersFirst(page), false);
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
    );
    await page.screenshot({ path: `/tmp/kestrel-personal-${width}.png` });
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    "Personal desktop/phone: cockpit (plays, six index cards, top ideas, latest event, greeting) and the saved numbers-first toggle passed.",
  );
} finally {
  await browser.close();
}
