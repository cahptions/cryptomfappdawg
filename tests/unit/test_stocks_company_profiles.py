"""Source-grounded profiles, background enrichment and failure preservation without live provider calls."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.stocks import company_profiles as cp
from desk.stocks.repo import StocksDatabases, StocksMarket
from desk.stocks.sources.sec import parse_submissions
from tests.conftest import REPO, load_fixture

NOW = datetime(2026,10,27,14,tzinfo=UTC)
BUSINESS = "We design and manufacture electronic devices for consumers and businesses worldwide. " * 20
DOCUMENT = f"""<html><script>secret script</script><body><div>Item 1. Business 3 Item 1A. Risk Factors 4</div>
<h2>ITEM 1. BUSINESS</h2><p>{BUSINESS}</p><h2>ITEM 1A. RISK FACTORS</h2><p>Do not include risks.</p></body></html>""".encode()


def test_business_extraction_skips_toc_scripts_and_unreliable_boundaries():
    text = cp.business_excerpt(DOCUMENT)
    assert text and text.startswith("We design") and "Risk Factors" not in text and "script" not in text
    assert cp.business_excerpt(b"Item 1. Business 3 Item 1A. Risk Factors 4") is None
    assert cp.business_excerpt(b"<h2>Item 1. Business</h2><p>Unbounded text</p>") is None


@pytest.fixture
def profile_world(tmp_path, monkeypatch):
    paths = DeskPaths(tmp_path/"home", REPO/"config", tmp_path/"secrets.env")
    dbs = StocksDatabases.open(paths)
    raw = json.loads(load_fixture("stocks/sec_submissions_AAPL.json"))
    raw.update(website="https://example.test", phone="555-0100",
               addresses={"business":{"city":"Cupertino","stateOrCountry":"CA"}})
    company = parse_submissions(json.dumps(raw).encode())
    dbs.market.execute("INSERT INTO stk_listings VALUES ('AAPL','Apple','Nasdaq','common',0,0,320193,1,?,?)",
                       (NOW.isoformat(),NOW.isoformat()))
    StocksMarket(dbs.market).save_company(company,"Technology",NOW)
    cp.request(dbs.market,"AAPL",NOW)
    monkeypatch.setattr(cp,"utc_now",lambda:NOW)
    yield paths, dbs, company
    dbs.close()


async def test_profile_refresh_loads_business_metadata_and_filing_provenance(profile_world, monkeypatch):
    paths, dbs, company = profile_world
    calls = []

    class Sec:
        async def submissions(self,cik):
            calls.append(cik)
            return company

        async def document(self,*args):
            return DOCUMENT

    async def close():
        calls.append("closed")

    monkeypatch.setattr(cp,"build_stocks_suite",lambda *args:SimpleNamespace(sec=Sec(),problems={},aclose=close))
    await cp.refresh(paths,load_config(paths.config_dir))
    profile = cp.read(dbs.market,"AAPL")
    assert profile["about"].startswith("We design") and profile["metadata"]["headquarters"] == "Cupertino, CA"
    assert profile["sector"] == "Technology" and profile["source_url"].startswith("https://www.sec.gov/Archives/")
    assert profile["filing_date"] and profile["error"] is None and calls == [320193,"closed"]
    await cp.refresh(paths,load_config(paths.config_dir))
    assert calls == [320193,"closed"]  # cached, no repeated full filing download


async def test_provider_failure_preserves_last_good_description_and_backs_off(profile_world, monkeypatch):
    paths, dbs, _company = profile_world
    dbs.market.execute("UPDATE stk_company_profiles SET about='Last known business',source_url='https://www.sec.gov/'")
    calls = []

    class Sec:
        async def submissions(self,cik):
            calls.append(cik)
            raise TimeoutError

    async def close():
        pass

    monkeypatch.setattr(cp,"build_stocks_suite",lambda *args:SimpleNamespace(sec=Sec(),problems={},aclose=close))
    await cp.refresh(paths,load_config(paths.config_dir))
    assert cp.read(dbs.market,"AAPL")["about"] == "Last known business"
    assert "preserved" in cp.read(dbs.market,"AAPL")["error"]
    monkeypatch.setattr(cp,"utc_now",lambda:NOW+timedelta(minutes=1))
    await cp.refresh(paths,load_config(paths.config_dir))
    assert calls == [320193]


async def test_missing_contact_has_in_app_settings_remedy_and_never_fetches(profile_world, monkeypatch):
    paths, dbs, _company = profile_world

    async def close():
        pass

    monkeypatch.setattr(cp,"build_stocks_suite",lambda *args:SimpleNamespace(problems={"sec":"missing"},aclose=close))
    await cp.refresh(paths,load_config(paths.config_dir))
    assert "Settings" in cp.read(dbs.market,"AAPL")["error"]
