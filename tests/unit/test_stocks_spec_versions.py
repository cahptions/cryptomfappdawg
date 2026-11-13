"""Every frozen rule set (desks, validation, lists, PAPER strategies, the market-events log) is registered by
version, and a database that has seen one content under a version refuses a different content under the same version
(``StocksMarket.register_spec``). That refusal stopped the daily research on every database older than 2026-11-03,
when the News desk changed the desks and validation rules without new versions. These tests make such a change fail
here, not on the user's computer: a changed rule needs a new version and a new line in stocks_spec_fingerprints.json."""

import json
from datetime import UTC, datetime
from pathlib import Path

from desk.core.hashing import canonical_json, sha256_hex
from desk.core.paths import DeskPaths
from desk.stocks import daily
from desk.stocks import desks as dk
from desk.stocks import market_events as me
from desk.stocks import validation as val
from desk.stocks.lists import LISTS
from desk.stocks.paper.strategies import STRATEGIES
from desk.stocks.repo import StocksDatabases, StocksMarket
from tests.conftest import REPO

FINGERPRINTS = Path(__file__).with_name("stocks_spec_fingerprints.json")


def current() -> dict[str, tuple[int, str]]:
    def h(content: object) -> str:
        return sha256_hex(canonical_json(content))

    out = {"stocks_desks": (dk.METHOD_VERSION, h(dk.method_content())),
           "stocks_validation": (val.VERSION, h(val.content())), me.NAME: (me.VERSION, h(me.content()))}
    out.update({f"stocks_list_{s.id}": (s.version, h(s.content())) for s in LISTS})
    out.update({f"stocks_paper_{s.id}": (s.version, h(s.content())) for s in STRATEGIES})
    from desk.stocks.learner import actions, features, labels, model

    out.update({m.NAME: (m.VERSION, h(m.content())) for m in (actions, features, labels, model)})
    return out


def test_no_rule_set_changes_without_a_new_version() -> None:
    recorded = json.loads(FINGERPRINTS.read_text())
    problems = []
    for name, (version, digest) in current().items():
        known = recorded.get(name, {}).get(str(version))
        if known is None:
            problems.append(f"{name} v{version} is new: add its fingerprint {digest} to {FINGERPRINTS.name}")
        elif known != digest:
            problems.append(f"{name} v{version} changed without a new version: bump its version (databases that "
                            "recorded the old content refuse the new one) and record the new fingerprint")
    assert not problems, "\n".join(problems)


def test_a_database_from_before_the_news_desk_runs_its_daily_research(tmp_path: Path) -> None:
    dbs = StocksDatabases.open(DeskPaths(tmp_path / "home", REPO / "config", tmp_path / "secrets.env"))
    try:
        market = StocksMarket(dbs.market)
        at = datetime(2026, 11, 1, tzinfo=UTC)
        for name, version in (("stocks_desks", 2), ("stocks_validation", 1)):     # as recorded before 2026-11-03
            dbs.market.execute("INSERT INTO stk_specs(spec_sha256, name, version, registered_at, content_json) "
                               "VALUES (?, ?, ?, ?, '{}')", (sha256_hex(f"old {name}"), name, version, at.isoformat()))
        specs = daily.register_specs(market, datetime(2026, 11, 12, tzinfo=UTC))
        assert specs["stocks_desks"] == current()["stocks_desks"][1]
    finally:
        dbs.close()
