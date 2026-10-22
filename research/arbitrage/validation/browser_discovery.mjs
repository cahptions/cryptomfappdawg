// Isolated synthetic acceptance only. Refuses any server without the fixture identity.
import { chromium } from "/tmp/kestrel-browser/node_modules/playwright/index.mjs";
import { writeFile } from "node:fs/promises";
const browser = await chromium.launch({ executablePath: "/usr/bin/chromium" });
const page = await browser.newPage({ viewport: { width: 1600, height: 1050 } });
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  const identity = await page.request.get(
    "http://127.0.0.1:18868/test-fixture-identity",
  );
  if (
    !identity.ok() ||
    (await identity.json()).fixture !==
      "kestrel-arbitrage-browser-synthetic-only-v1"
  )
    throw Error("Not isolated fixture");
  await page.route("https://i.ebayimg.com/kestrel-test-*.svg", async (route) =>
    route.fulfill({
      contentType: "image/svg+xml",
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80"><rect width="80" height="80" fill="#a9baff"/><text x="5" y="40" font-size="9">TEST IMAGE</text></svg>',
    }),
  );
  await page.goto("http://127.0.0.1:18868");
  await page.locator(".deal-card").first().waitFor();
  await page.waitForFunction(
    () => document.querySelector(".product-photo img")?.naturalWidth > 0,
  );
  const first = await page.locator(".deal-card h3").first().innerText();
  if (!first.includes("Mirrorless"))
    throw Error(
      "Dollar-profit ranking should put larger profitable item first",
    );
  await page.locator("#rank-order").selectOption("roi");
  if ((await page.locator(".deal-card h3").first().innerText()) === first)
    throw Error("ROI ranking should differ");
  await page.locator("#product-search").fill("NO SUCH PRODUCT");
  // Search returns a useful empty state, rather than silently rebuilding the filters.
  await page
    .getByRole("heading", { name: "Your ranked shortlist starts here" })
    .waitFor();
  await page.locator("#product-search").fill("");
  await page.locator("#rank-order").selectOption("profit");
  await page.locator("[data-action=lead-save]").first().click();
  await page
    .locator("[data-action=lead-save]")
    .filter({ hasText: "Unsave" })
    .waitFor();
  await page.locator("[data-leads=saved]").click();
  await page.locator("[data-action=lead-offer]").first().click();
  await page
    .getByLabel("Direct source product link", { exact: true })
    .fill("https://www.adafruit.com/product/4300");
  await page
    .getByLabel("Actual source price you observed", { exact: true })
    .fill("42");
  await page
    .getByLabel("What you observed (pack, condition, promotion)", {
      exact: true,
    })
    .fill("SYNTHETIC review only, not a real offer");
  await page
    .getByRole("button", { name: "Save local record", exact: true })
    .click();
  await page.waitForURL("**/#product/ui-deal");
  await page
    .getByRole("heading", {
      name: "TEST FIXTURE — Precision kit clearance lead",
      exact: true,
    })
    .waitFor();
  await page
    .getByRole("button", { name: "+ Compare destination", exact: true })
    .click();
  if (
    (await page.locator("select[name=product_id]").inputValue()) !== "ui-deal"
  )
    throw Error("Lost product context");
  for (const [label, value] of [
    ["Selling marketplace / channel", "SYNTHETIC new channel"],
    ["Supported resale price", "110"],
    ["Lower resale price to test", "85"],
    ["Variable selling fees (%)", "12.9"],
    ["Extra monthly channel cost (0 if none)", "0"],
  ])
    await page.getByLabel(label, { exact: true }).fill(value);
  await page
    .getByRole("button", { name: "Save local record", exact: true })
    .click();
  await page.locator("#editor").waitFor({ state: "hidden" });
  await page
    .getByRole("button", { name: "Edit assumptions", exact: true })
    .click();
  await page
    .getByLabel("Variable selling fees (%)", { exact: true })
    .fill("14.25");
  for (const label of [
    "Fixed selling fees",
    "All inbound, prep and delivery costs",
    "Returns / damaged-stock allowance",
    "Other overhead (include your labor value)",
  ])
    await page.getByLabel(label, { exact: true }).fill("1");
  await page
    .getByLabel("Hands-on minutes per unit", { exact: true })
    .fill("15");
  await page
    .getByLabel("Expected days until cash is recovered", { exact: true })
    .fill("14");
  await page
    .getByLabel("Units supported by your demand evidence", { exact: true })
    .fill("1");
  await page
    .getByRole("button", { name: "Save local record", exact: true })
    .click();
  await page.locator("#editor").waitFor({ state: "hidden" });
  const state = await (
    await page.request.get("http://127.0.0.1:18868/api/snapshot")
  ).json();
  const route = state.configured_routes.filter(
    (r) => r.channel === "SYNTHETIC new channel",
  );
  if (
    route.length !== 1 ||
    route[0].fee_fraction !== "0.1425" ||
    route[0].product_id !== "ui-deal"
  )
    throw Error("Fee conversion/edit idempotence/context failed");
  const b = state.board.find((b) => b.product.id === "ui-deal");
  if (b.comparison.recommended || b.offers[0].availability !== "UNKNOWN")
    throw Error("Lead review improperly bypassed buying checks");
  await page.getByRole("link", { name: "Discover", exact: true }).click();
  await page.locator("[data-action=lead-dismiss]").first().click();
  await page.locator("[data-action=lead-dismiss]").waitFor({ state: "hidden" });
  await page
    .getByRole("link", { name: "Sources & system", exact: true })
    .click();
  await page.locator("#discovery-query").fill("LEGO 75375");
  await page
    .getByRole("button", {
      name: "Find sourcing and resale searches",
      exact: true,
    })
    .click();
  if ((await page.locator("#discovery-links article").count()) !== 6)
    throw Error("Missing external destinations");
  await page
    .getByRole("link", { name: "Business settings", exact: true })
    .click();
  await page.getByText("Advanced limits · optional", { exact: true }).click();
  await page
    .getByLabel("Maximum product share (0–1)", { exact: true })
    .waitFor();
  const mobile = [];
  await page.setViewportSize({ width: 390, height: 844 });
  for (const route of [
    "home",
    "product/ui-deal",
    "profitability",
    "sources",
    "settings",
  ]) {
    await page.goto("http://127.0.0.1:18868/#" + route);
    await page.locator("#content h2").first().waitFor();
    const width = await page.evaluate(() => ({
      viewport: innerWidth,
      document: document.documentElement.scrollWidth,
    }));
    if (width.document > width.viewport)
      throw Error("Overflow on " + route + ": " + JSON.stringify(width));
    mobile.push({ route, ...width });
  }
  if (errors.length) throw Error(errors.join("; "));
  await writeFile(
    "research/arbitrage/validation/browser-discovery-results.json",
    JSON.stringify(
      {
        basis:
          "Synthetic fixtures and intercepted image bytes, not real source access, actual stock or real profit",
        flows: [
          "decoded allowed-host image",
          "dollar-profit vs ROI ranking",
          "search empty state",
          "save lead",
          "saved tab",
          "lead to actual-offer review",
          "product context preselected",
          "partial comparison",
          "percentage fee conversion",
          "edit existing comparison",
          "no purchase-check bypass",
          "dismiss lead",
          "external research links",
          "advanced disclosure",
        ],
        mobile,
        uncaughtErrors: errors,
      },
      null,
      2,
    ),
  );
  console.log("Discovery/product comparison browser flows passed");
} finally {
  await browser.close();
}
