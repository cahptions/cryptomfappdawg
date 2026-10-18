"""Builds the shared provider suite (one HTTP client, one limiter, one health tracker) for a desk process."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from desk.core.config import LoadedConfig
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets
from desk.providers.cache import SingleFlightCache
from desk.providers.dexscreener import DexScreenerClient
from desk.providers.geckoterminal import GeckoTerminalClient
from desk.providers.health import HealthTracker
from desk.providers.http import ProviderHttp
from desk.providers.jupiter import JupiterClient
from desk.providers.ratelimit import RateLimiter
from desk.providers.registry import ProviderRegistry, load_registry
from desk.providers.solana_rpc import SolanaRpcClient
from desk.storage.databases import Databases
from desk.storage.raw_store import RawStore

# every adapter class and the registry entry it requires (checked by tests, M1-16)
ADAPTERS = {
    "geckoterminal": GeckoTerminalClient,
    "dexscreener": DexScreenerClient,
    "jupiter": JupiterClient,
    "solana_rpc": SolanaRpcClient,
}


@dataclass
class ProviderSuite:
    geckoterminal: GeckoTerminalClient
    dexscreener: DexScreenerClient
    jupiter: JupiterClient
    rpc: SolanaRpcClient
    limiter: RateLimiter
    health: HealthTracker
    registry: ProviderRegistry
    client: httpx.AsyncClient

    async def aclose(self) -> None:
        await self.client.aclose()
        self.limiter.close()


def build_suite(paths: DeskPaths, loaded: LoadedConfig, secrets: Secrets, dbs: Databases,
                *, client: httpx.AsyncClient | None = None) -> ProviderSuite:
    registry = load_registry(paths.config_dir, REPO_ROOT)
    settings = loaded.settings
    http_client = client or httpx.AsyncClient(
        trust_env=True, follow_redirects=False,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
    )
    limiter = RateLimiter(paths.db_dir / "runtime.db")
    health = HealthTracker(dbs.runtime, settings.health)
    raw_store = RawStore(paths.raw_dir, dbs.market)
    cache = SingleFlightCache()

    def http(name: str) -> ProviderHttp:
        return ProviderHttp(name, registry.get(name), secrets=secrets, limiter=limiter, health=health,
                            settings=settings.http, client=http_client, raw_store=raw_store, cache=cache)

    return ProviderSuite(
        geckoterminal=GeckoTerminalClient(http("geckoterminal")),
        dexscreener=DexScreenerClient(http("dexscreener")),
        jupiter=JupiterClient(http("jupiter")),
        rpc=SolanaRpcClient(http("solana_rpc")),
        limiter=limiter,
        health=health,
        registry=registry,
        client=http_client,
    )
