"""Provider suite for the Robinhood desk: shares the desk's limiter and health tracker, not the Solana adapters."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

import httpx

from desk.core.config import LoadedConfig
from desk.core.errors import ConfigError
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.providers.cache import SingleFlightCache
from desk.providers.health import HealthTracker
from desk.providers.http import ProviderHttp
from desk.providers.ratelimit import RateLimiter
from desk.providers.registry import load_registry
from desk.rh.client import RobinhoodClient
from desk.rh.coinbase import CoinbaseClient
from desk.rh.signing import key_path, load_private_key, signer


@dataclass
class RhSuite:
    robinhood: RobinhoodClient
    coinbase: CoinbaseClient
    limiter: RateLimiter
    health: HealthTracker
    client: httpx.AsyncClient
    key_problem: str | None            # why Robinhood prices are unavailable, or None when ready

    async def aclose(self) -> None:
        await self.client.aclose()
        self.limiter.close()


def build_rh_suite(paths: DeskPaths, loaded: LoadedConfig, secrets: Secrets, runtime: sqlite3.Connection,
                   *, client: httpx.AsyncClient | None = None) -> RhSuite:
    """``runtime`` is runtime.db (shared rate budgets and provider health)."""
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

    robinhood_http = http("robinhood")
    problem: str | None = None
    api_key = secrets.get("ROBINHOOD_API_KEY")
    if api_key is None:
        problem = "ROBINHOOD_API_KEY is not in secrets.env (see `desk rh keygen`)"
    else:
        try:
            robinhood_http.signer = signer(api_key, load_private_key(key_path(paths.secrets_file)), time.time)
        except ConfigError as exc:
            problem = str(exc)
    return RhSuite(RobinhoodClient(robinhood_http), CoinbaseClient(http("coinbase")), limiter, health, http_client,
                   problem)


__all__ = ["RhSuite", "build_rh_suite"]
