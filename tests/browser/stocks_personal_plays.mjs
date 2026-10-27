// Full personal selection workflow against --scenario personal-workflows. All quotes and companies synthetic.
import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.KESTREL_STOCKS_URL ?? "http://127.0.0.1:8892";
const out =
  process.env.KESTREL_BROWSER_OUTPUT ?? "/tmp/kestrel-browser-results";
const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_EXECUTABLE || undefined,
});
const errors = [];
try {
  for (const width of [1366, 375]) {
    const page = await browser.newPage({
      viewport: { width, height: 950 },
      hasTouch: width === 375,
    });
    page.on("pageerror", (e) => errors.push(String(e)));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    const overflow = () =>
      page.evaluate(() => document.scrollingElement.scrollWidth - innerWidth);
    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    await page.getByRole("tab", { name: /Most likely to finish ITM/ }).click();
    await page.getByLabel("Option type").selectOption("put");
    const card = page.locator("article.play-card").first();
    await card.waitFor();
    assert.match(await card.locator(".play-underlying").innerText(), /\$/);
    await card
      .getByText("What do Δ, Γ, Θ and ν mean?", { exact: true })
      .click();
    assert.match(
      await card.innerText(),
      /one percentage point of implied volatility/,
    );
    await card.locator(".play-track-button").click();
    const dialog = page.locator("dialog.play-confirmation");
    await dialog.waitFor();
    await page
      .getByRole("button", { name: "Confirm PAPER tracking", exact: true })
      .waitFor();
    await page.waitForFunction(
      () => !document.querySelector(".play-confirmation .primary").disabled,
    );
    const bounds = await dialog.boundingBox();
    assert.ok(
      bounds.x >= 0 && bounds.x + bounds.width <= width + 1 && bounds.y >= 0,
    );
    await page.getByLabel("Contracts per leg").fill("3");
    await page.screenshot({
      path: `${out}/plays-confirm-${width}.png`,
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "Confirm PAPER tracking", exact: true })
      .click();
    await page.waitForURL(/#plays\/mine\/[a-f0-9]+$/);
    await page.getByRole("heading", { name: "QUEUED", exact: true }).waitFor();
    await page
      .getByRole("button", { name: "Open exact-leg payoff calculator" })
      .click();
    await page.locator("dialog.options-calculator").waitFor();
    assert.equal(await page.locator(".calc-leg").count(), 1);
    assert.equal(
      await page.getByLabel("Call or put", { exact: true }).inputValue(),
      "put",
    );
    assert.equal(
      await page.getByLabel("Contracts", { exact: true }).inputValue(),
      "3",
    );
    await page.waitForFunction(() =>
      document
        .querySelector(".calc-live")
        .textContent.includes("Scenario P/L after fees"),
    );
    await page.screenshot({
      path: `${out}/plays-exact-put-${width}.png`,
      fullPage: true,
    });
    await page.getByRole("button", { name: "Close", exact: true }).click();
    const selectedHash = await page.evaluate(() => location.hash);
    const pass = await page.evaluate(async () => {
      const session = await (await fetch("/api/stocks/session")).json();
      return (
        await fetch("/test/quote-pass", {
          method: "POST",
          headers: { "X-CSRF-Token": session.csrf_token },
        })
      ).json();
    });
    assert.ok(pass.changed >= 1);
    await page.getByRole("button", { name: "Refresh P/L" }).click();
    await page.getByRole("heading", { name: "OPEN", exact: true }).waitFor();
    await page.getByLabel("Contracts", { exact: true }).fill("5");
    await page.getByRole("button", { name: "1W", exact: true }).click();
    await page.evaluate(async () => {
      const session = await (await fetch("/api/stocks/session")).json();
      await fetch("/test/quote-pass", {
        method: "POST",
        headers: { "X-CSRF-Token": session.csrf_token },
      });
    });
    await page
      .getByText(/Saved observations checked/)
      .waitFor({ timeout: 22000 });
    assert.equal(
      await page.getByLabel("Contracts", { exact: true }).inputValue(),
      "5",
      "auto-updates preserve hypothetical chart quantity",
    );
    assert.equal(
      await page
        .getByRole("button", { name: "1W", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    await page
      .getByRole("button", { name: "Close PAPER tracking", exact: true })
      .click();
    await page.getByRole("heading", { name: "CLOSING", exact: true }).waitFor();
    await page.evaluate(async () => {
      const session = await (await fetch("/api/stocks/session")).json();
      await fetch("/test/quote-pass", {
        method: "POST",
        headers: { "X-CSRF-Token": session.csrf_token },
      });
    });
    await page.getByRole("button", { name: "Refresh P/L" }).click();
    await page.getByRole("heading", { name: "CLOSED", exact: true }).waitFor();
    assert.equal(
      await page.getByLabel("Contracts", { exact: true }).inputValue(),
      "3",
    );
    assert.ok((await overflow()) <= 1, `personal page no overflow ${width}`);
    await page.screenshot({
      path: `${out}/plays-personal-${width}.png`,
      fullPage: true,
    });
    await page.reload();
    await page.getByRole("heading", { name: "CLOSED", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => location.hash), selectedHash);
    await page
      .getByRole("link", { name: "My tracked plays", exact: true })
      .click();
    await page
      .getByRole("heading", { name: "My tracked plays", exact: true })
      .waitFor();
    assert.ok((await page.locator(".personal-play-card").count()) > 0);

    await page.goto(`${base}/#plays`);
    await page.getByRole("heading", { name: "Plays", exact: true }).waitFor();
    await page.getByLabel("Option type").selectOption("all");
    await page.getByRole("tab", { name: /Sell rich premium/ }).click();
    const spread = page.locator("article.play-card").first();
    await spread.getByRole("link", { name: /Open play, P\/L/ }).click();
    await page
      .getByRole("button", { name: "Open exact-leg payoff calculator" })
      .click();
    await page.locator("dialog.options-calculator").waitFor();
    assert.equal(await page.locator(".calc-leg").count(), 2);
    assert.deepEqual(
      (
        await page
          .getByLabel("Action", { exact: true })
          .evaluateAll((nodes) => nodes.map((n) => n.value))
      ).sort(),
      ["buy", "sell"],
    );
    await page.waitForFunction(() =>
      document
        .querySelector(".calc-live")
        .textContent.includes("Scenario P/L after fees"),
    );
    assert.ok((await overflow()) <= 1, `multi-leg page no overflow ${width}`);
    await page.getByRole("button", { name: "Close", exact: true }).click();

    await page.goto(`${base}/#stock/CHEAP`);
    await page
      .getByRole("heading", { name: "About the company", exact: true })
      .waitFor();
    await page.getByText(/Synthetic company: designs and sells/).waitFor();
    assert.match(
      await page.locator(".company-profile").innerText(),
      /Pittsburgh, PA/,
    );
    assert.ok((await overflow()) <= 1, `company page no overflow ${width}`);
    await page.screenshot({
      path: `${out}/stock-company-${width}.png`,
      fullPage: true,
    });
    await page.close();
  }
  assert.deepEqual(errors, []);
  console.log(
    JSON.stringify({
      ok: true,
      workflows: [
        "adjacent confirmation",
        "put selection",
        "quantity",
        "opening quote",
        "close quote",
        "reload",
        "multi-leg calculator",
        "company profile",
      ],
      widths: [1366, 375],
    }),
  );
} finally {
  await browser.close();
}
