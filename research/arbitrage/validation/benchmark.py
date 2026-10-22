"""Synthetic snapshot benchmark only; no network or operator runtime data. Run from repository root."""
import json,resource,time
from datetime import UTC,datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from desk.arbitrage.contracts import Product,Observation,Route,Settings
from desk.arbitrage.repo import Repo
from desk.arbitrage.views import snapshot
from desk.core.paths import DeskPaths
from desk.core.hashing import sha256_hex
from desk.storage.db import transaction
with TemporaryDirectory(prefix='arbitrage-bench-') as temporary:
 paths=DeskPaths(Path(temporary)/'data',Path(temporary)/'config',Path(temporary)/'none.json')
 now=datetime.now(UTC)
 with Repo.open(paths) as repo:
  repo.save_settings(Settings(capital=500,enabled=False))
  with transaction(repo.conn):
   for p in range(500):
    product=Product(id=f'fixture-{p}',title=f'SYNTHETIC benchmark {p}',brand='Fixture',mpn=str(p),pack_count=1)
    repo.save_product(product)
    repo.save_route(Route(id=f'route-{p}',product_id=product.id,channel='Fixture',sale_price=100,stress_price=80,valid_until=now+timedelta(days=1)))
    for day in range(10):
     o=Observation(product=product,source_id=f'fixture-{p}',url='https://www.adafruit.com/product/4300',price=50,currency='USD',at=now-timedelta(days=day),availability='DECLARED_AVAILABLE',method='synthetic-benchmark')
     body=o.model_dump_json()
     repo.conn.execute('INSERT INTO arb_observations(product_id,source_id,at,body,input_hash) VALUES (?,?,?,?,?)',(product.id,o.source_id,o.at.isoformat(),body,sha256_hex(body)))
  start=time.perf_counter();cpu=time.process_time();data=snapshot(repo,now)
  result={'basis':'Synthetic benchmark on cloud executor; not a measurement of the operator Dell laptop','products':len(data['board']),'observations':5000,'routes':500,'snapshot_wall_seconds':round(time.perf_counter()-start,4),'snapshot_cpu_seconds':round(time.process_time()-cpu,4),'peak_process_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,2),'serialized_snapshot_bytes':len(json.dumps(data)),'database_bytes_including_wal':sum(p.stat().st_size for p in paths.db_dir.iterdir()),'network_calls':0}
  print(json.dumps(result,indent=2))
  Path('research/arbitrage/validation/resources-synthetic.json').write_text(json.dumps(result,indent=2)+'\n')
