"""Isolated synthetic test server, never operator data or credentials. Run from repository root."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import uvicorn
from desk.arbitrage.contracts import Product, Observation, Route, Settings
from desk.arbitrage.repo import Repo
from desk.arbitrage.runner import initialize
from desk.arbitrage.web import create_app
from desk.core.paths import DeskPaths
with TemporaryDirectory(prefix='kestrel-arbitrage-ui-') as temporary:
    root=Path(temporary)
    paths=DeskPaths(root/'data',root/'config',root/'no-credentials.json')
    initialize(paths)
    now=datetime.now(UTC)
    p=Product(id='ui-fixture',title='TEST FIXTURE — Precision driver kit (not live)',brand='Fixture',mpn='FIX24',category='Tools',pack_count=1,variant='24 pieces',identity_confirmed=True)
    with Repo.open(paths) as repo:
        repo.save_settings(Settings(capital=500,enabled=False))
        for d in range(7):
            repo.observe(Observation(product=p,source_id='ui-fixture',url='https://www.adafruit.com/product/4300',price=Decimal(50+d),currency='USD',at=now-timedelta(days=d),availability='DECLARED_AVAILABLE',method='synthetic-browser-fixture',evidence='Test fixture only, not a retailer observation'))
        repo.save_route(Route(id='ui-route',product_id=p.id,channel='TEST CHANNEL — synthetic fees',sale_price=100,stress_price=80,fee_fraction=Decimal('.1'),fixed_fee=1,fulfillment_cost=5,return_allowance=2,overhead=1,days_to_cash=14,demand_limit=5,eligible=True,identity_confirmed=True,reviewed=True,fee_version='synthetic-only',evidence_note='Not actual fees or sales',valid_until=now+timedelta(days=1)))
    app=create_app(paths,auto_start=False)
    @app.get('/test-fixture-identity')
    def fixture_identity():
        return {'fixture':'kestrel-arbitrage-browser-synthetic-only-v1'}
    uvicorn.run(app,host='127.0.0.1',port=18868,access_log=False)
