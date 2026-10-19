/** Real Chromium checks for the production build and local FastAPI fixture server.
 * Install playwright outside the repository, then set PLAYWRIGHT_MODULE to its module path.
 * No provider calls, funded accounts, or worker starts are part of this check.
 */
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const { chromium } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE || '/tmp/kestrel-browser/node_modules/playwright/index.mjs').href);
const base = process.env.KESTREL_BROWSER_URL || 'http://127.0.0.1:8877';
const scenario = process.env.KESTREL_BROWSER_SCENARIO || 'empty';
const output = process.env.KESTREL_BROWSER_OUTPUT || '/tmp/kestrel-browser-results';
const sampleSeconds = Number(process.env.KESTREL_BROWSER_SAMPLE_SECONDS || '12');
assert(['empty', 'populated'].includes(scenario));
assert(sampleSeconds >= 2 && sampleSeconds <= 120);
await mkdir(output, { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE || '/usr/bin/chromium', headless: true, args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, acceptDownloads: true, reducedMotion: 'reduce' });
const errors = [];
const failedRequests = [];
const requests = [];
const pendingStreams = new Set();
await context.addInitScript(() => {
  window.__browserAcceptance = { sourcesCreated: 0, sourcesClosed: 0, sourcesActive: 0, maxSourcesActive: 0, csp: [] };
  const NativeEventSource = window.EventSource;
  window.EventSource = class extends NativeEventSource {
    constructor(...args) {
      super(...args);
      const state = window.__browserAcceptance;
      state.sourcesCreated += 1;
      state.sourcesActive += 1;
      state.maxSourcesActive = Math.max(state.maxSourcesActive, state.sourcesActive);
      this.__countedClosed = false;
    }
    close() {
      if (!this.__countedClosed) {
        this.__countedClosed = true;
        window.__browserAcceptance.sourcesClosed += 1;
        window.__browserAcceptance.sourcesActive -= 1;
      }
      return super.close();
    }
  };
  document.addEventListener('securitypolicyviolation', event => window.__browserAcceptance.csp.push(`${event.violatedDirective}: ${event.blockedURI}`));
});
const page = await context.newPage();
page.on('pageerror', error => errors.push(error.message));
page.on('console', message => { if (message.type() === 'error') errors.push(`${message.text()} ${message.location().url || ''}`.trim()); });
page.on('requestfailed', request => {
  pendingStreams.delete(request);
  // Closing a streaming document deliberately aborts its SSE transport.
  if (request.failure()?.errorText !== 'net::ERR_ABORTED') failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText}`);
});
page.on('requestfinished', request => pendingStreams.delete(request));
page.on('request', request => {
  requests.push({ method: request.method(), url: request.url() });
  if (request.url().endsWith('/api/events')) pendingStreams.add(request);
});

const evidence = { scenario, viewport: '1920x1080', checks: [], memory: {}, requests: {}, browser: browser.version() };
const check = label => evidence.checks.push(label);
try {
  const navigation = await page.goto(base, { waitUntil: 'domcontentloaded' });
  assert.equal(navigation.status(), 200);
  assert.match(navigation.headers()['content-security-policy'], /frame-ancestors 'none'/);
  await page.locator('.connection-indicator.live').waitFor();
  await page.getByRole('heading', { name: 'Your edge, in perspective.' }).waitFor();
  check('Production assets load with a connected SSE snapshot and local CSP.');
  assert.equal(await page.locator('body').evaluate(node => node.scrollWidth <= window.innerWidth), true, 'desktop page overflows horizontally');
  check('1920×1080 desktop has no horizontal page overflow.');

  if (scenario === 'empty') {
    await page.getByText('No PAPER book initialized').waitFor();
    await page.getByRole('heading', { name: 'Your performance curve starts with evidence.' }).waitFor();
    await page.getByRole('heading', { name: 'Flat is a position.' }).waitFor();
    check('Empty state shows missing evidence and no invented balance/performance curve.');
  } else {
    await page.getByText('1 closed simulated trades', { exact: true }).waitFor();
    await page.locator('.position-card').first().waitFor();
    assert.equal(await page.locator('.chart-series').count(), 1);
    check('Populated state renders a recorded cash curve, one closed trade, and an open position.');
  }
  await page.screenshot({ path: path.join(output, `${scenario}-overview-1920x1080.png`), fullPage: false });
  for (const viewport of [{ width: 1366, height: 768 }, { width: 390, height: 844 }]) {
    await page.setViewportSize(viewport);
    assert.equal(await page.locator('body').evaluate(node => node.scrollWidth <= window.innerWidth), true, `page overflows horizontally at ${viewport.width}px`);
    await page.screenshot({ path: path.join(output, `${scenario}-overview-${viewport.width}x${viewport.height}.png`), fullPage: false });
  }
  await page.setViewportSize({ width: 1920, height: 1080 });
  check('1366px and 390px layouts render without horizontal page overflow.');

  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  for (const name of ['Scanner', 'Paper trading', 'Positions', 'Strategies', 'Research', 'Audit', 'System', 'Live gates', 'Overview']) {
    await nav.getByRole('link', { name, exact: true }).click();
    await page.waitForFunction(() => document.querySelector('nav a[aria-current="page"]')?.getAttribute('href') === window.location.hash);
    assert.equal(await page.locator('h1').count(), 1);
  }
  check('All nine navigation views render without a full page reload.');

  await page.keyboard.press('g');
  await page.keyboard.press('t');
  await page.waitForURL('**/#paper');
  await page.getByRole('heading', { name: 'Watch the book learn.' }).waitFor();
  if (scenario === 'empty') {
    await page.locator('.paper-status-text strong', { hasText: 'No paper book yet' }).waitFor();
    await page.getByRole('heading', { name: 'Start PAPER to open the learning book.' }).waitFor();
    check('Paper trading tab (g t) explains how to start when no book exists.');
  } else {
    await page.getByRole('heading', { name: "What it's doing", exact: true }).waitFor();
    const feed = page.locator('.audit-list.plain-titles > li');
    await feed.first().waitFor();
    assert.match(await page.locator('.audit-list.plain-titles').innerText(), /Bought FIXTURE for 0\.100000 SOL/);
    assert.match(await page.locator('.audit-list.plain-titles').innerText(), /Sold .*: \+/);
    const everything = await feed.count();
    await page.getByRole('button', { name: /^Trades/ }).click();
    assert(await feed.count() < everything, 'the Trades filter narrows the feed');
    await page.getByRole('button', { name: /^Everything/ }).click();
    assert.match(await page.locator('.paper-levels').first().innerText(), /Hard stop/);
    assert.match(await page.locator('.paper-levels').first().innerText(), /Trailing stop/);
    assert.match(await page.locator('table.paper-funnel').innerText(), /Bought\s+\d+\s+2/);
    await page.getByRole('heading', { name: 'Fees, counted on every trade', exact: true }).waitFor();
    await page.getByRole('heading', { name: 'Moonshot watch', exact: true }).waitFor();
    await page.locator('.position-card').first().click();
    await page.locator('.detail-drawer[open]').waitFor();
    await page.locator('.detail-drawer .loading-state').waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: 'Close dossier' }).click();
    check('Paper trading tab (g t): plain-English feed and filters, exit levels, funnel, fees, moonshot watch and position dossier.');
  }
  await page.screenshot({ path: path.join(output, `${scenario}-paper-1920x1080.png`), fullPage: false });
  await page.setViewportSize({ width: 390, height: 844 });
  assert.equal(await page.locator('body').evaluate(node => node.scrollWidth <= window.innerWidth), true, 'paper tab overflows at 390px');
  await page.screenshot({ path: path.join(output, `${scenario}-paper-390x844.png`), fullPage: false });
  await page.setViewportSize({ width: 1920, height: 1080 });
  check('Paper trading tab has no horizontal page overflow at 390px.');

  await page.keyboard.press('g');
  await page.keyboard.press('s');
  await page.waitForURL('**/#scanner');
  await page.keyboard.press('/');
  await page.getByRole('textbox', { name: 'Search token or mint' }).waitFor();
  // The app moves focus in a zero-delay task after "/"; wait for it instead of racing it.
  await page.waitForFunction(() => document.activeElement?.getAttribute('aria-label') === 'Search token or mint', null, { timeout: 2000 });
  await page.getByRole('textbox', { name: 'Search token or mint' }).fill('not-a-real-mint-123');
  await page.getByRole('heading', { name: 'No candidates match these filters' }).waitFor();
  await page.getByRole('textbox', { name: 'Search token or mint' }).fill('');
  await page.getByRole('textbox', { name: 'Search token or mint' }).blur();
  check('g/s and / shortcuts navigate and focus search; filtering is functional.');

  if (scenario === 'populated') {
    await page.getByRole('button', { name: 'Open FIXTURE dossier', exact: true }).first().click();
    await page.getByRole('dialog').filter({ has: page.getByRole('heading', { name: 'FIXTURE', exact: true }) }).waitFor();
    await page.getByText('Offline browser fixture', { exact: true }).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('button', { name: 'Close dossier' }).waitFor({ state: 'hidden' });
    check('Candidate dossier fetches actual recorded data and closes with Escape.');
    await nav.getByRole('link', { name: 'Positions', exact: true }).click();
    await page.locator('.position-card').first().click();
    await page.locator('.detail-drawer[open]').waitFor();
    await page.locator('.detail-drawer .loading-state').waitFor({ state: 'hidden' });
    assert.match(await page.locator('.detail-drawer').innerText(), /tokens raw/i);
    await page.getByRole('button', { name: 'Close dossier' }).click();
    check('Position dossier loads its exact raw quantity and audit evidence.');
    await nav.getByRole('link', { name: 'Strategies', exact: true }).click();
    await page.locator('.strategy-name').first().click();
    await page.locator('.detail-drawer[open]').waitFor();
    await page.locator('.detail-drawer .loading-state').waitFor({ state: 'hidden' });
    assert.match(await page.locator('.detail-drawer').innerText(), /versions/i);
    await page.getByRole('button', { name: 'Close dossier' }).click();
    check('Strategy dossier loads the persisted strategy specification.');
  }

  await nav.getByRole('link', { name: 'Research', exact: true }).click();
  await page.getByRole('heading', { name: 'Readiness verdict', exact: true }).waitFor();
  await page.locator('.readiness-verdict .badge', { hasText: scenario === 'empty' ? 'No readiness report yet' : 'INSUFFICIENT EVIDENCE' }).first().waitFor();
  check('Research shows the latest recorded readiness verdict (or that none exists yet).');
  const csvPromise = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download CSV', exact: true }).click();
  const csv = await csvPromise;
  assert.equal(csv.suggestedFilename(), 'kestrel-trades.csv');
  const csvPath = path.join(output, `${scenario}-trades.csv`);
  await csv.saveAs(csvPath);
  const csvText = await readFile(csvPath, 'utf8');
  assert.match(csvText, /^id,mint,strategy_id,state,/);
  assert.equal(csvText.trim().split('\n').length, scenario === 'empty' ? 1 : 3);
  const jsonPromise = page.waitForEvent('download');
  await page.getByRole('link', { name: 'Download JSON', exact: true }).click();
  const jsonDownload = await jsonPromise;
  const jsonPath = path.join(output, `${scenario}-trades.json`);
  await jsonDownload.saveAs(jsonPath);
  const exportedTrades = JSON.parse(await readFile(jsonPath, 'utf8'));
  assert.equal(exportedTrades.length, scenario === 'empty' ? 0 : 2);
  for (const trade of exportedTrades) {
    assert.equal(typeof trade.ticket_lamports, 'string');
    assert.equal(typeof trade.tokens_raw, 'string');
  }
  check('CSV and JSON links create real downloads matching fixture trades, with exact raw quantities encoded as strings.');

  await page.keyboard.press('Shift+S');
  await page.getByRole('heading', { name: 'Stop new entries?', exact: true }).waitFor();
  assert.equal(requests.filter(request => request.method === 'POST').length, 0, 'opening a confirmation must not submit an action');
  await page.getByRole('button', { name: 'Cancel', exact: true }).click();
  assert.equal(requests.filter(request => request.method === 'POST').length, 0);
  await page.keyboard.press('Shift+S');
  const stopResponse = page.waitForResponse(response => response.url().endsWith('/api/control/stop'));
  await page.getByRole('button', { name: 'Stop new entries', exact: true }).click();
  assert.equal((await stopResponse).status(), 200);
  await page.getByText('Entries halted', { exact: true }).first().waitFor();
  check('Shift+S opens confirmation; cancel sends no POST; confirming STOP halts fixture entries.');

  await page.keyboard.press('?');
  await page.getByRole('heading', { name: 'Move at the speed of thought.', exact: true }).waitFor();
  await page.keyboard.press('Escape');
  check('Keyboard help opens and Escape dismisses it.');

  await nav.getByRole('link', { name: 'Overview', exact: true }).click();
  const cdp = await context.newCDPSession(page);
  const snapshotRequests = () => requests.filter(request => /\/api\/(dashboard|snapshot)$/.test(request.url)).length;
  const snapshotsBefore = snapshotRequests();
  await cdp.send('HeapProfiler.collectGarbage');
  const before = await cdp.send('Runtime.getHeapUsage');
  for (let i = 0; i < 24; i += 1) {
    await nav.getByRole('link', { name: i % 2 ? 'Overview' : 'Scanner', exact: true }).click();
  }
  await page.waitForTimeout(sampleSeconds * 1000);
  await cdp.send('HeapProfiler.collectGarbage');
  const after = await cdp.send('Runtime.getHeapUsage');
  const streams = await page.evaluate(() => window.__browserAcceptance);
  assert.equal(streams.sourcesActive, 1);
  assert.equal(streams.maxSourcesActive, 1);
  assert.equal(streams.sourcesCreated, 1);
  assert.deepEqual(streams.csp, []);
  assert.equal(snapshotRequests(), snapshotsBefore, 'polling must remain idle while the SSE transport is healthy');
  assert(after.usedSize - before.usedSize < 8 * 1024 * 1024, 'large retained JS growth during short sample');
  evidence.memory = { sample_seconds: sampleSeconds, navigation_cycles: 24, before_used_bytes: before.usedSize, after_used_bytes: after.usedSize, retained_growth_bytes: after.usedSize - before.usedSize, streams, note: 'Short Chromium sample only; does not establish a long-duration absence of memory leaks.' };
  check('24 view changes retain one EventSource and bounded JS heap during the short sample.');
  evidence.requests = { total: requests.length, sse: requests.filter(request => request.url.endsWith('/api/events')).length, snapshots: snapshotRequests(), posts: requests.filter(request => request.method === 'POST').length };
  assert.equal(evidence.requests.sse, 1);
  assert.deepEqual(errors, []);
  assert.deepEqual(failedRequests, []);
  check('No JavaScript errors, CSP violations, or unexpected failed requests.');
  await page.goto('about:blank');
  for (let attempt = 0; attempt < 20 && pendingStreams.size; attempt += 1) await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(pendingStreams.size, 0, 'leaving the document must close its streaming transport');
  evidence.requests.streams_after_leaving_document = pendingStreams.size;
  check('Leaving the dashboard closes the SSE transport; healthy SSE does not also poll snapshots.');
  evidence.ok = true;
} catch (error) {
  evidence.ok = false;
  evidence.error = error.stack || String(error);
  evidence.console_errors = errors;
  evidence.failed_requests = failedRequests;
  await page.screenshot({ path: path.join(output, `${scenario}-failure.png`), fullPage: true }).catch(() => {});
  process.exitCode = 1;
} finally {
  await writeFile(path.join(output, `${scenario}-results.json`), JSON.stringify(evidence, null, 2) + '\n');
  console.log(JSON.stringify(evidence, null, 2));
  await context.close();
  await browser.close();
}
