import assert from 'node:assert/strict';
import { chromium } from 'playwright';
// Synthetic stale-prices fixture only; no provider calls, keys or worker start.
const base=process.env.KESTREL_STOCKS_URL ?? 'http://127.0.0.1:8879';
const browser=await chromium.launch({executablePath:process.env.CHROMIUM_EXECUTABLE || undefined});
try {
  for (const width of [1366,375]) {
    const page=await browser.newPage({viewport:{width,height:1000}});
    const errors=[]; page.on('pageerror',e=>errors.push(String(e)));
    await page.goto(base);
    await page.getByRole('heading',{name:'Options activity',exact:true}).waitFor();
    const snap=await page.evaluate(async()=>await(await fetch('/api/stocks/snapshot')).json());
    assert.equal(snap.research_preview.asof,'2026-10-21');
    assert.equal(snap.options_research.asof,'2026-10-22');
    assert.equal(snap.options_research.coverage.quality_pass,3);
    await page.goto(`${base}/#options`);
    await page.getByRole('heading',{name:'Options activity',exact:true}).waitFor();
    for (const symbol of ['SPY','QQQ','CHEAP']) await page.getByRole('link',{name:symbol,exact:true}).first().waitFor();
    await page.screenshot({path:`/tmp/kestrel-options-stale-${width}.png`,fullPage:true});
    for (const symbol of ['SPY','QQQ','CHEAP']) {
      await page.goto(`${base}/#stock/${symbol}`);
      await page.getByRole('tab',{name:'Calls',exact:true}).waitFor();
      assert.equal(await page.locator('.option-chain tbody tr').count(),3);
      await page.getByRole('tab',{name:'Puts',exact:true}).click();
      assert.equal(await page.locator('.option-chain tbody tr').count(),3);
      await page.getByRole('columnheader',{name:'Quote time (ET)',exact:true}).waitFor();
    }
    await page.goto(`${base}/#system`);
    await page.getByRole('heading',{name:'Options coverage',exact:true}).waitFor();
    await page.getByRole('heading',{name:'Recent stock prices unavailable',exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
    assert.deepEqual(errors,[]); await page.close();
  }
  console.log('Stale-price/403 scenario: SPY, QQQ and company calls/puts; independent options lists; desktop/mobile; diagnostics passed.');
} finally {await browser.close();}
