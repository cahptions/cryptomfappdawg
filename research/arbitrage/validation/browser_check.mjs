// Synthetic fixtures only. Requires the isolated browser_server.py; refuses other servers.
const {chromium}=await import(process.env.KESTREL_PLAYWRIGHT_MODULE || '/tmp/kestrel-browser/node_modules/playwright/index.mjs');
import {mkdir,writeFile} from 'node:fs/promises';
const browser=await chromium.launch({executablePath:'/usr/bin/chromium'});
const page=await browser.newPage({viewport:{width:1600,height:1100}});
await page.route('https://i.ebayimg.com/kestrel-test-*.svg', async route => {
 const name=route.request().url().includes('camera')?'CAMERA':route.request().url().includes('headphones')?'AUDIO':'TOOLS';
 await route.fulfill({contentType:'image/svg+xml',body:`<svg xmlns="http://www.w3.org/2000/svg" width="400" height="280"><rect width="400" height="280" rx="16" fill="#eef0f5"/><rect x="80" y="70" width="240" height="130" rx="18" fill="#273347"/><circle cx="200" cy="135" r="48" fill="#a5b7e8"/><circle cx="200" cy="135" r="30" fill="#1b2537"/><text x="200" y="235" text-anchor="middle" font-size="15" fill="#556079">${name} — SYNTHETIC IMAGE</text></svg>`});
});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await mkdir('research/arbitrage/validation',{recursive:true});
const identity=await page.request.get('http://127.0.0.1:18868/test-fixture-identity');
if(!identity.ok() || (await identity.json()).fixture!=='kestrel-arbitrage-browser-synthetic-only-v1')throw Error('Refusing writes: server is not the isolated test fixture');
await page.goto('http://127.0.0.1:18868');
await page.getByRole('link',{name:'TEST FIXTURE — Precision driver kit (not live)',exact:true}).last().waitFor();
await page.locator('.product-photo img').first().waitFor();
const cameraStats=page.locator('.deal-card').filter({hasText:'Mirrorless camera kit'}).locator('.resale-values strong');
if(JSON.stringify(await cameraStats.allTextContents())!==JSON.stringify(['$420.00','$480.00','$540.00','$480.00']))throw Error('Low/average/high/median display disagrees with comparable evidence');
await page.waitForFunction(()=>[...document.querySelectorAll('.product-photo img')].some(i=>i.complete && i.naturalWidth>0));
await page.screenshot({path:'research/arbitrage/validation/browser-desktop-synthetic.png',fullPage:true});
await page.getByRole('link',{name:'TEST FIXTURE — Precision driver kit (not live)',exact:true}).last().click();
await page.locator('#history svg').waitFor();
await page.getByRole('button',{name:'Import price comparables',exact:true}).click();
const observed=new Date(Date.now()-60000).toISOString();
const csv='title,channel,url,price,currency,region,condition,pack_count,gtin,evidence_type,at,evidence_note\n'+
  `SYNTHETIC price sample,TEST imported channel,https://www.ebay.com/itm/fixture-import-1,70,USD,US,new,1,012345678905,REPORTED_SOLD,${observed},SYNTHETIC imported claim not a real sale\n`+
  `SYNTHETIC price sample,TEST imported channel,https://www.ebay.com/itm/fixture-import-2,80,USD,US,new,1,012345678905,REPORTED_SOLD,${observed},SYNTHETIC imported claim not a real sale\n`;
await page.locator('#comparable-file').setInputFiles({name:'synthetic-comparables.csv',mimeType:'text/csv',buffer:Buffer.from(csv)});
await page.getByRole('button',{name:'Import file',exact:true}).click();
await page.locator('#editor').waitFor({state:'hidden'});
await page.getByRole('img',{name:'REPORTED_SOLD comparable price samples in USD',exact:true}).waitFor();
if(JSON.stringify(await page.locator('.resale-values strong').first().locator('..').locator('..').locator('strong').allTextContents())!==JSON.stringify(['$70.00','$75.00','$80.00','$75.00']))throw Error('Imported comparable statistics incorrect');
await page.getByRole('button',{name:'Review identity',exact:true}).click();
await page.getByLabel('I checked brand, model, variant, pack and condition').check();
await page.getByRole('button',{name:'Save local record',exact:true}).click();
await page.locator('#editor').waitFor({state:'hidden'});
await page.getByRole('button',{name:'Record delivery check',exact:true}).click();
for(const [label,value] of [['Current product price','50'],['Confirmed available quantity','3'],['Shipping per unit','1'],['Purchase tax per unit','1'],['What did you check? Include delivery/variant evidence','Synthetic browser test, not a live stock check']])await page.getByLabel(label,{exact:true}).fill(value);
await page.getByRole('button',{name:'Save local record',exact:true}).click();
await page.locator('#editor').waitFor({state:'hidden'});
await page.getByRole('link',{name:'Where to sell',exact:true}).click();
await page.getByText('Manage stored comparisons',{exact:true}).click();
await page.getByRole('button',{name:'Pause route',exact:true}).first().click();
await page.getByRole('button',{name:'Resume route',exact:true}).waitFor();
await page.getByRole('button',{name:'Resume route',exact:true}).click();
await page.getByRole('button',{name:'Pause route',exact:true}).first().waitFor();
await page.getByRole('link',{name:'Capital planner',exact:true}).click();
await page.getByText('1 units',{exact:false}).first().waitFor();
await page.getByRole('link',{name:'Inventory',exact:true}).click();
async function inventory(id,kind,units,amount){
 await page.getByRole('button',{name:'Record inventory event',exact:true}).click();
 for(const [label,value]of [['Unique event ID (reuse on retry)',id],['Lot ID','ui-lot'],['Units (0 for ADJUST_COST)',units],['Total landed cost / net proceeds / refund / added cost',amount],['Evidence or reference','Synthetic browser test']])await page.getByLabel(label,{exact:true}).fill(value);
 await page.locator('select[name=kind]').selectOption(kind);
 await page.getByRole('button',{name:'Save local record',exact:true}).click();
 await page.locator('#editor').waitFor({state:'hidden'});
}
await inventory('ui-receive','RECEIVE','2','104');
await inventory('ui-sell','SELL','1','90');
await page.getByText('$38.00',{exact:true}).first().waitFor();
await inventory('ui-return','RETURN','1','90');
await page.getByRole('link',{name:'Settings',exact:true}).click();
await page.getByLabel('Business capital',{exact:true}).fill('600');
await page.getByRole('button',{name:'Save business profile',exact:true}).click();
await page.getByRole('heading',{name:'Data connections & setup guides'}).waitFor();
await page.getByText('eBay Browse API · Implemented · production access required; live connection unverified',{exact:true}).click();
await page.getByRole('link',{name:'Create a developer account',exact:false}).waitFor();
await page.getByLabel('Production App ID / Client ID',{exact:true}).fill('SYNTHETIC_BROWSER_CLIENT_ID');
await page.getByLabel('Production Cert ID / Client Secret',{exact:true}).fill('SYNTHETIC_BROWSER_SECRET');
await page.getByLabel('I confirmed permitted personal research access and no added API charges',{exact:true}).check();
await page.getByLabel('Enable read-only live eBay listings',{exact:true}).check();
await page.getByRole('button',{name:'Save eBay connection',exact:true}).click();
await page.getByText('App key: saved privately',{exact:false}).waitFor();
if(await page.getByLabel('Production Cert ID / Client Secret',{exact:true}).inputValue())throw Error('Saved secret was echoed');
await page.screenshot({path:'research/arbitrage/validation/browser-settings-synthetic.png',fullPage:true});
await page.getByRole('link',{name:'Sources',exact:true}).click();
await page.getByRole('heading',{name:'Sources',exact:true}).waitFor();
if(await page.getByRole('button',{name:'Add supported product URL',exact:true}).count())throw Error('Source configuration leaked into product browsing');
await page.locator('#source-host').selectOption('www.adafruit.com');
await page.locator('.source-product-group').waitFor();
await page.getByRole('link',{name:'Settings',exact:true}).click();
await page.getByText('ebay-starter-drivers',{exact:false}).waitFor();
await page.getByRole('button',{name:'Add eBay search',exact:true}).click();
await page.getByLabel('Specific product keyword or model',{exact:true}).fill('synthetic test query');
await page.getByRole('button',{name:'Save local record',exact:true}).click();
await page.locator('#editor').waitFor({state:'hidden'});
await page.getByText('synthetic test query',{exact:false}).first().waitFor();

for(const route of ['home','research','profitability','suggestions','inventory','top','planning','sources','settings']){
 await page.goto('http://127.0.0.1:18868/#'+route);await page.locator('#content h2').first().waitFor();
}
await page.setViewportSize({width:390,height:844});
await page.goto('http://127.0.0.1:18868/#home');
await page.getByRole('link',{name:'TEST FIXTURE — Precision driver kit (not live)',exact:true}).last().waitFor();
const width=await page.evaluate(()=>({viewport:innerWidth,document:document.documentElement.scrollWidth}));
await page.screenshot({path:'research/arbitrage/validation/browser-mobile-synthetic.png',fullPage:true});
if(width.document>width.viewport)throw Error('Mobile page overflows viewport '+JSON.stringify(width));
if(errors.length)throw Error('Browser errors: '+errors.join('; '));
await writeFile('research/arbitrage/validation/browser-results.json',JSON.stringify({basis:'Synthetic isolated browser fixtures only; not live offers or forward business results',pages:9,flows:['comparable low/average/high/median display','CSV comparable upload and reported-price plot','identity review','regional availability check','capital basket','inventory receive','net sale','refund quarantine','business capital save','API guide links','private eBay credential save with blank preserved fields','eBay starter searches','custom eBay search','pause/resume selling route'],mobile:width,uncaughtErrors:errors},null,2));
console.log('Browser flows passed',width);await browser.close();
