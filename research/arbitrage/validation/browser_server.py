"""Isolated synthetic test server, never operator data or credentials. Run from repository root."""
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import uvicorn
from desk.arbitrage.contracts import Product, Observation, Route, Settings, DealLead
from desk.arbitrage.repo import Repo
from desk.arbitrage.runner import initialize
from desk.arbitrage.web import create_app
from desk.core.paths import DeskPaths
with TemporaryDirectory(prefix='kestrel-arbitrage-ui-') as temporary:
    root=Path(temporary)
    paths=DeskPaths(root/'data',root/'config',root/'no-credentials.json')
    initialize(paths)
    now=datetime.now(UTC)
    p=Product(id='ui-fixture',title='TEST FIXTURE — Precision driver kit (not live)',brand='Fixture',mpn='FIX24',category='Tools',pack_count=1,variant='24 pieces',identity_confirmed=True,image_url='https://i.ebayimg.com/kestrel-test-tools.svg')
    with Repo.open(paths) as repo:
        repo.save_settings(Settings(capital=500,enabled=False))
        for d in range(7):
            repo.observe(Observation(product=p,source_id='ui-fixture',url='https://www.adafruit.com/product/4300',price=Decimal(50+d),currency='USD',at=now-timedelta(days=d),availability='DECLARED_AVAILABLE',method='synthetic-browser-fixture',evidence='Test fixture only, not a retailer observation'))
        repo.save_route(Route(id='ui-route',product_id=p.id,channel='TEST CHANNEL — synthetic fees',sale_price=100,stress_price=80,fee_fraction=Decimal('.1'),fixed_fee=1,fulfillment_cost=5,return_allowance=2,overhead=1,days_to_cash=14,demand_limit=5,eligible=True,identity_confirmed=True,reviewed=True,fee_version='synthetic-only',evidence_note='Not actual fees or sales',valid_until=now+timedelta(days=1)))
        for key,title,price,sale,stress,minutes in [
            ('camera','Mirrorless camera kit',Decimal('260'),Decimal('490'),Decimal('420'),20),
            ('headphones','Wireless studio headphones',Decimal('62'),Decimal('139'),Decimal('115'),10),
        ]:
            product=Product(id='ui-'+key,title='TEST FIXTURE — '+title+' (not live)',category='Electronics',brand='Fixture',mpn=key,pack_count=1,identity_confirmed=True,image_url='https://i.ebayimg.com/kestrel-test-'+key+'.svg')
            repo.observe(Observation(product=product,source_id='ui-'+key,url='https://www.adafruit.com/product/4300',price=price,currency='USD',at=now,availability='VERIFIED_AVAILABLE',region='US',quantity=1,shipping=0,tax=0,verified_until=now+timedelta(minutes=5),method='synthetic-browser-fixture',evidence='SYNTHETIC: no real stock or price'))
            repo.save_route(Route(id='route-'+key,product_id=product.id,channel='TEST resale channel',sale_price=sale,stress_price=stress,fee_fraction=Decimal('.13'),fixed_fee=0,fulfillment_cost=8,return_allowance=5,overhead=4,monthly_cost=0,days_to_cash=14,effort_minutes=minutes,demand_limit=1,eligible=True,identity_confirmed=True,reviewed=True,fee_version='synthetic',evidence_note='Synthetic test only',valid_until=now+timedelta(days=1)))
        repo.save_leads([DealLead(id='ui-deal',source_id='deals-slickdeals',title='TEST FIXTURE — Precision kit clearance lead',url='https://slickdeals.net/f/123-fixture',summary='Synthetic community lead for workflow validation, not an actual deal or current stock.',published_at=now-timedelta(minutes=40),first_seen_at=now,retrieved_at=now,image_url='https://i.ebayimg.com/kestrel-test-tools.svg')])
    app=create_app(paths,auto_start=False)
    @app.get('/test-fixture-identity')
    def fixture_identity():
        return {'fixture':'kestrel-arbitrage-browser-synthetic-only-v1'}
    uvicorn.run(app,host='127.0.0.1',port=18868,access_log=False)
