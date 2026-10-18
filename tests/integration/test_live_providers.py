"""Opt-in live read-only tests (``uv run pytest -m live``). They call real public APIs, place no orders, and
touch no wallet. They verify that the real providers still match our parsers (schema drift detection) and
that every hard-coded on-chain constant is what we think it is (M1-19)."""

from __future__ import annotations

from pathlib import Path

import pytest

from desk.contracts.quotes import QuoteObservation
from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.core.secrets import load_secrets
from desk.providers.factory import ProviderSuite, build_suite
from desk.providers.solana_rpc import TOKEN_2022_PROGRAM, TOKEN_PROGRAM
from desk.providers.venues import VENUE_PROGRAMS
from desk.storage.databases import Databases
from tests.conftest import REPO

pytestmark = pytest.mark.live

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PUMP_TOKEN_2022_MINT = "7wrVy57WvGRxwn679U3buysv6FDdAV3hoDfJSqVrpump"


@pytest.fixture
async def suite(tmp_path: Path) -> ProviderSuite:
    paths = DeskPaths(home=tmp_path / "home", config_dir=REPO / "config",
                      secrets_file=DeskPaths.from_env().secrets_file)
    dbs = Databases.open(paths)
    s = build_suite(paths, load_config(REPO / "config"), load_secrets(paths.secrets_file), dbs)
    yield s
    await s.aclose()
    dbs.close()


async def test_token_program_constants_match_chain(suite: ProviderSuite) -> None:
    usdc = await suite.rpc.get_mint_account(USDC)
    assert usdc is not None and usdc.owner_program == TOKEN_PROGRAM and usdc.decimals == 6
    pump = await suite.rpc.get_mint_account(PUMP_TOKEN_2022_MINT)
    assert pump is not None and pump.owner_program == TOKEN_2022_PROGRAM
    wsol = await suite.rpc.get_mint_account(SOL)
    assert wsol is not None and wsol.owner_program == TOKEN_PROGRAM and wsol.decimals == 9


async def test_venue_program_constants_match_live_pools(suite: ProviderSuite) -> None:
    """For each venue seen in live new/trending pools, the pool account owner must equal our constant."""
    pools = (await suite.geckoterminal.new_pools(1)).items + (await suite.geckoterminal.trending_pools("1h")).items
    checked: set[str] = set()
    for snap in pools:
        if snap.venue in VENUE_PROGRAMS and snap.venue not in checked:
            owner = await suite.rpc.get_account_owner(snap.pool_address)
            assert owner == VENUE_PROGRAMS[snap.venue], f"{snap.venue}: pool {snap.pool_address} owned by {owner}"
            checked.add(snap.venue)
    assert "pumpfun_bonding_curve" in checked or "pumpswap" in checked, f"too few venues observed: {checked}"


async def test_live_parsers_have_no_schema_drift(suite: ProviderSuite) -> None:
    gt = await suite.geckoterminal.new_pools(1)
    assert gt.items and gt.errors == []
    ds = await suite.dexscreener.tokens([USDC, SOL])
    assert ds.items and ds.errors == []
    recent = await suite.jupiter.tokens_recent()
    assert recent.items and recent.errors == []


async def test_live_quote_is_read_only(suite: ProviderSuite) -> None:
    quote = await suite.jupiter.order(SOL, USDC, 10_000_000)  # 0.01 SOL, no taker => no transaction built
    assert isinstance(quote, QuoteObservation)
    assert quote.transaction_included is False and quote.out_amount_raw > 0
