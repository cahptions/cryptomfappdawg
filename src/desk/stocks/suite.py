"""Provider suite for KESTREL Stocks: the desk's rate limiter and health tracker, one client per source."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import httpx

from desk.core.config import LoadedConfig
from desk.core.errors import DeskError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.providers.cache import SingleFlightCache
from desk.providers.health import HealthTracker
from desk.providers.http import ProviderHttp
from desk.providers.ratelimit import RateLimiter
from desk.providers.registry import load_registry
from desk.stocks.connection import preferences
from desk.stocks.contracts import OptionQuote, StockQuote
from desk.stocks.schwab_auth import SchwabAuthError, SchwabTokens, token_path
from desk.stocks.sources.feeds import FeedsClient
from desk.stocks.sources.massive import MassiveClient
from desk.stocks.sources.schwab import SchwabClient
from desk.stocks.sources.sec import SecClient, sec_user_agent
from desk.stocks.sources.tradier import TradierClient


class OptionData(Protocol):
    @property
    def configured(self) -> bool: ...
    async def expirations(self, symbol: str) -> list[date]: ...
    async def chain(self, symbol: str, expiration: date) -> list[OptionQuote]: ...
    async def quotes(self, symbols: list[str]) -> list[StockQuote]: ...


class UnavailableOptions:
    configured = False

    async def expirations(self, symbol: str) -> list[date]:
        raise DeskError("Options connection settings need repair in Settings")

    async def chain(self, symbol: str, expiration: date) -> list[OptionQuote]:
        raise DeskError("Options connection settings need repair in Settings")

    async def quotes(self, symbols: list[str]) -> list[StockQuote]:
        raise DeskError("Options connection settings need repair in Settings")


@dataclass
class StocksSuite:
    sec: SecClient
    feeds: FeedsClient
    massive: MassiveClient
    tradier: OptionData  # retained alias for existing integrations; selected by options_provider
    limiter: RateLimiter
    health: HealthTracker
    client: httpx.AsyncClient
    problems: dict[str, str] = field(default_factory=dict)   # source → what the operator must do
    option_source: str = "tradier_sandbox"
    schwab: SchwabClient | None = None

    @property
    def options(self) -> OptionData:
        return self.tradier

    async def aclose(self) -> None:
        await self.client.aclose()
        self.limiter.close()


def build_stocks_suite(paths: DeskPaths, loaded: LoadedConfig, secrets: Secrets, runtime: sqlite3.Connection,
                       *, client: httpx.AsyncClient | None = None) -> StocksSuite:
    registry = load_registry(paths.config_dir, REPO_ROOT)
    settings = loaded.settings
    http_client = client or httpx.AsyncClient(
        trust_env=True, follow_redirects=False, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5))
    limiter = RateLimiter(paths.db_dir / "runtime.db")
    health = HealthTracker(runtime, settings.health)
    cache = SingleFlightCache()

    def http(name: str) -> ProviderHttp:
        return ProviderHttp(name, registry.get(name), secrets=secrets, limiter=limiter, health=health,
                            settings=settings.http, client=http_client, cache=cache)

    sec_www = http("sec_www")
    sec_www.user_agent = sec_user_agent(secrets.get("SEC_CONTACT_EMAIL"))
    sec_data = http("sec_data")
    sec_data.user_agent = sec_www.user_agent
    tradier = http("tradier" if settings.stocks.tradier_env == "production" else "tradier_sandbox")
    suite = StocksSuite(SecClient(sec_data, sec_www),
                        FeedsClient(http("nasdaq_trader"), http("finra_api"), http("finra_cdn"), http("treasury"), http("fred"),
                                    http("apewisdom"), http("openfigi")),
                        MassiveClient(http("massive")), TradierClient(tradier), limiter, health, http_client)
    suite.option_source = tradier.name
    suite.schwab = SchwabClient(http("schwab"), SchwabTokens(token_path(paths), secrets))
    try:
        provider = preferences(paths, settings.stocks)["options_provider"]
    except SchwabAuthError:
        provider = "unavailable"
        suite.tradier = UnavailableOptions()
        suite.option_source = "unavailable"
        suite.problems["options"] = "Saved options connection settings are invalid; save the connection again"
    if provider == "schwab":
        suite.tradier = suite.schwab
        suite.option_source = "schwab"
    if sec_www.user_agent is None:
        suite.problems["sec"] = ("SEC_CONTACT_EMAIL is not in secrets.env: the SEC asks every program to name a contact, "
                                 "and www.sec.gov refuses requests without one")
    if not suite.massive.configured:
        suite.problems["prices"] = "MASSIVE_API_KEY is not in secrets.env (free key: massive.com, Stocks Basic)"
    if not suite.tradier.configured and "options" not in suite.problems:
        suite.problems["options"] = ("Schwab is not connected; run desk stocks connect" if suite.option_source == "schwab"
                                     else "TRADIER_TOKEN is not in secrets.env (free developer sandbox token: tradier.com)")
    return suite


__all__ = ["StocksSuite", "build_stocks_suite"]
