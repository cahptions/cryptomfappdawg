// The Learning report against the disposable learning-report fixture (synthetic data, no provider access):
//   uv run python -m tests.browser.stocks_fixture_server --scenario learning-report --port 8931
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL || "http://127.0.0.1:8931";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
try {
  const report = await (
    await browser.newContext().then((c) => c.request)
  ).get(`${base}/api/stocks/learning/report`);
  const data = await report.json();
  // Every finding carries evidence and one of the three strengths; replays stay in their own section.
  const items = data.sections.flatMap((s) => s.items);
  assert.ok(items.length > 10);
  for (const i of items) {
    assert.ok(["early", "suggestive", "tested"].includes(i.strength));
    assert.ok(Number.isInteger(i.evidence.items) && i.evidence.items >= 0);
    assert.ok(Number.isInteger(i.evidence.days) && i.evidence.days >= 0);
    assert.match(i.link, /^#[a-z]/);
  }
  assert.ok(data.headline.length >= 2 && data.headline.length <= 4);
  assert.match(data.headline[0], /graded \d+ plays over \d+ sessions?/);
  const changed = data.sections.find((s) => s.id === "changed").items;
  assert.ok(
    changed.length > 0 &&
      !/No earlier weekly snapshot/.test(changed[0].headline),
  );
  assert.match(data.markdown, /^# KESTREL Learning report/);
  for (const width of [1366, 375]) {
    const page = await browser.newPage({ viewport: { width, height: 1000 } });
    const errors = [];
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => {
      if (m.type() === "error")
        errors.push(`${m.text()} (${m.location().url})`);
    });
    await page.goto(`${base}/#learning`);
    const box = page.locator(".learning-report");
    await box.locator(".learning-report-headline p").first().waitFor();
    // At the top of the Learning page: right after the page heading, before the account status.
    assert.ok(
      await page.evaluate(() => {
        const r = document.querySelector(".learning-report"),
          s = document.querySelector(".paper-status");
        return Boolean(
          r &&
            s &&
            r.compareDocumentPosition(s) & Node.DOCUMENT_POSITION_FOLLOWING,
        );
      }),
    );
    assert.equal(
      await box.locator(".learning-report-headline p").count(),
      data.headline.length,
    );
    // Sections fold; the headline stays visible whichever are closed.
    const wrong = box.locator('details[data-section="wrong"]');
    assert.equal(await wrong.evaluate((d) => d.open), true);
    await wrong.locator("summary").click();
    assert.equal(await wrong.evaluate((d) => d.open), false);
    assert.ok(await box.locator(".learning-report-headline").isVisible());
    const coming = box.locator('details[data-section="coming"]');
    assert.equal(await coming.evaluate((d) => d.open), false);
    await coming.locator("summary").click();
    const chip = coming.locator(".learning-strength").first();
    assert.ok(await chip.isVisible());
    assert.match(await chip.innerText(), /^(early|suggestive|tested)$/i);
    assert.match(
      await coming.locator(".learning-report-evidence").first().innerText(),
      /\d+ \w+/,
    );
    await box.screenshot({ path: `/tmp/kestrel-learning-report-${width}.png` });
    // Replays are labelled and listed apart.
    const replay = box.locator('details[data-section="replay"] summary');
    assert.match(await replay.innerText(), /Replays on past data/);
    // No horizontal overflow on the page or inside the report.
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
      true,
      `horizontal overflow at ${width}px`,
    );
    assert.equal(
      await box.evaluate((n) => n.scrollWidth <= n.clientWidth),
      true,
    );
    // The Markdown download beside the report, and beside the check-in downloads.
    const [file] = await Promise.all([
      page.waitForEvent("download"),
      box.getByRole("button", { name: "Download report (Markdown)" }).click(),
    ]);
    assert.equal(file.suggestedFilename(), "kestrel-learning-report.md");
    await page.getByRole("button", { name: "Check-ins", exact: true }).click();
    const whole = page.getByRole("button", {
      name: "Download learning report (Markdown)",
    });
    await whole.waitFor();
    assert.equal(await whole.isEnabled(), true);
    await page
      .getByText(/weekly report · \d{4}-W\d{2}/)
      .first()
      .waitFor();
    // A finding's link opens the page that supports it.
    await page.goto(`${base}/#learning`);
    await box.locator(".learning-report-headline p").first().waitFor();
    const evidenceLink = box
      .locator('details[data-section="achievements"] a[href^="#plays"]')
      .first();
    await evidenceLink.click();
    await page.waitForFunction(() => location.hash.startsWith("#plays"));
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log(
    "Learning report desktop/phone: headline, folding sections, strength chips, evidence, replays apart, downloads, links and no overflow passed.",
  );
} finally {
  await browser.close();
}
