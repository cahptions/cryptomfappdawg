"""Explicit read-only public-source acceptance in disposable data. No operator secrets or marketplace writes.

Run intentionally from the repository root. Three bounded cycles with 60-second gaps; writes the live-catalog
report. Normal users open the app instead; it starts its collector automatically.
"""
import asyncio,json,time
from datetime import datetime,UTC
from pathlib import Path
import httpx
from desk.arbitrage.repo import Repo
from desk.arbitrage.runner import initialize,cycle
from desk.arbitrage.views import snapshot
from desk.core.paths import DeskPaths
async def main():
 root=Path(temporary);paths=DeskPaths(root/'data',root/'config',root/'no-credentials.json')
 initialize(paths); started=datetime.now(UTC).isoformat();passes=[];tick=time.monotonic()
 async with httpx.AsyncClient(timeout=25,follow_redirects=False,limits=httpx.Limits(max_connections=1)) as client:
  for n in range(3):
   outcome=await cycle(paths,client=client)
   with Repo.reader(paths) as repo:
    state=snapshot(repo,datetime.now(UTC));currencies={r[0]:r[1] for r in repo.conn.execute("SELECT json_extract(body,'$.currency'),COUNT(DISTINCT product_id) FROM arb_observations GROUP BY 1")}
    report={'pass':n+1,'products':state['pipeline']['products'],'priced_products':state['pipeline']['priced_products'],'queued_urls':state['pipeline']['pending_merchant_urls'],'currencies':currencies,'outcome':outcome,'sources':{key:{k:v.get(k) for k in ['status','items','error','page','complete']} for key,v in state['jobs'].items() if key!='collector'}}
    passes.append(report);print(json.dumps(report),flush=True)
   if n<2:await asyncio.sleep(60)
 payload={'basis':'Actual public source requests through normal cycle into isolated cloud data; no operator data or credentials. Prices/stock are retailer declarations, not completed sales or proven profitable opportunities.','started_at':started,'finished_at':datetime.now(UTC).isoformat(),'seconds':round(time.monotonic()-tick,2),'passes':passes,'marketplace_writes':False}
 Path('research/arbitrage/validation/live-catalog-results.json').write_text(json.dumps(payload,indent=2)+'\n')
if __name__ == '__main__':
 from tempfile import TemporaryDirectory
 with TemporaryDirectory(prefix='kestrel-public-catalog-check-') as temporary:
  asyncio.run(main())
