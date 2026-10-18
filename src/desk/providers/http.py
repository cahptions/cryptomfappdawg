"""One HTTP client per provider: budget → request → retry/backoff → health → raw evidence.

Status handling:
* 2xx, 400, 404 → returned to the caller (a 400 "not tradable" is a valid answer, not an outage).
* 429 → shared backoff (``retry-after: 0`` ignored), counted as a failure, retried.
* 5xx, timeouts, network errors → counted as failures, retried with exponential backoff + jitter.
* 401/403 → configuration problem (bad key): failure, not retried.
URLs are never logged (keyed RPC URLs contain the key); only provider and endpoint names are.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

from desk.core.clock import utc_now
from desk.core.config import HttpSection
from desk.core.errors import ProviderError, ProviderErrorKind
from desk.core.hashing import canonical_json, sha256_hex
from desk.core.secrets import Secrets
from desk.providers.cache import SingleFlightCache
from desk.providers.health import HealthTracker
from desk.providers.ratelimit import RateLimiter, Window
from desk.providers.registry import ProviderSpec
from desk.storage.raw_store import RawStore

log = logging.getLogger(__name__)

_PASS_THROUGH = frozenset({400, 404})


@dataclass(frozen=True)
class HttpResponse:
    provider: str
    endpoint: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    sha256: str
    requested_at: datetime
    received_at: datetime
    latency_ms: Decimal
    attempts: int


class ProviderHttp:
    def __init__(
        self,
        name: str,
        spec: ProviderSpec,
        *,
        secrets: Secrets,
        limiter: RateLimiter,
        health: HealthTracker,
        settings: HttpSection,
        client: httpx.AsyncClient,
        raw_store: RawStore | None = None,
        cache: SingleFlightCache | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.name = name
        self.spec = spec
        self.secrets = secrets
        self.limiter = limiter
        self.health = health
        self.settings = settings
        self.client = client
        self.raw_store = raw_store
        self.cache = cache or SingleFlightCache()
        self._sleep = sleep
        self._rng = rng

    # ── configuration ──────────────────────────────────────────────────────────────────────────────
    @property
    def key(self) -> str | None:
        return self.secrets.get(self.spec.key_env) if self.spec.key_env else None

    @property
    def has_key(self) -> bool:
        return self.key is not None

    def base_url(self, *, prefer_keyless_host: bool = False) -> str:
        if self.spec.auth == "url" and self.key:
            return self.key  # the keyed endpoint URL itself (contains the key; never logged)
        if prefer_keyless_host and not self.has_key and self.spec.keyless_base_url:
            return self.spec.keyless_base_url
        return self.spec.base_url

    def limiter_key(self, bucket: str) -> str:
        """Budgets and backoffs are per provider: a GeckoTerminal 429 must never stall DexScreener."""
        return f"{self.name}:{bucket}"

    def windows(self, bucket: str) -> tuple[Window, ...]:
        spec = self.spec.buckets[bucket]
        if self.has_key and spec.windows_with_key:
            return spec.windows_with_key
        return spec.windows

    def _headers(self) -> dict[str, str]:
        headers = {"User-Agent": self.settings.user_agent, **self.spec.default_headers}
        if self.spec.auth == "header" and self.spec.key_header and self.key:
            headers[self.spec.key_header] = self.key
        return headers

    # ── request ────────────────────────────────────────────────────────────────────────────────────
    async def request(
        self,
        endpoint: str,
        *,
        path: str | None = None,
        method: str = "GET",
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        prefer_keyless_host: bool = False,
        cache_ttl: float = 0.0,
        label: str | None = None,
    ) -> HttpResponse:
        endpoint_spec = self.spec.endpoints[endpoint]
        url = self.base_url(prefer_keyless_host=prefer_keyless_host) + (path if path is not None else endpoint_spec.path)

        async def fetch() -> HttpResponse:
            return await self._request_with_retries(label or endpoint, endpoint_spec.bucket, method, url, params,
                                                    json_body)

        if cache_ttl > 0 and method == "GET":
            key = canonical_json([self.name, url, sorted((params or {}).items())])
            return await self.cache.get_or_fetch(sha256_hex(key), cache_ttl, fetch)
        return await fetch()

    async def _request_with_retries(self, endpoint: str, bucket: str, method: str, url: str,
                                    params: Mapping[str, Any] | None, json_body: Any) -> HttpResponse:
        attempts = self.settings.max_retries + 1
        last_kind = ProviderErrorKind.NETWORK
        last_message = "no attempt made"
        last_status: int | None = None
        for attempt in range(1, attempts + 1):
            try:
                await self.limiter.acquire(self.limiter_key(bucket), self.windows(bucket))
            except TimeoutError as exc:
                raise ProviderError(self.name, ProviderErrorKind.RATE_LIMITED, f"{endpoint}: {exc}") from exc
            requested_at = utc_now()
            started = time.monotonic()
            try:
                response = await self.client.request(method, url, params=params, json=json_body,
                                                     headers=self._headers(), timeout=self.settings.timeout_seconds)
            except httpx.TimeoutException:
                last_kind, last_message, last_status = ProviderErrorKind.TIMEOUT, f"{endpoint} timed out", None
            except httpx.TransportError as exc:
                last_kind, last_message, last_status = ProviderErrorKind.NETWORK, f"{endpoint}: {type(exc).__name__}", None
            else:
                received_at = utc_now()
                latency = Decimal(str(round((time.monotonic() - started) * 1000, 1)))
                body = response.content
                status = response.status_code
                digest = (self.raw_store.put(self.name, endpoint, status, body, received_at)
                          if self.raw_store else sha256_hex(body))
                if 200 <= status < 300 or status in _PASS_THROUGH:
                    self.limiter.note_success(self.limiter_key(bucket))
                    self.health.record_success(self.name)
                    return HttpResponse(self.name, endpoint, status, dict(response.headers), body, digest,
                                        requested_at, received_at, latency, attempt)
                last_status = status
                if status == 429:
                    retry_after = _parse_retry_after(response.headers.get("retry-after"))
                    delay = self.limiter.note_rate_limited(self.limiter_key(bucket), retry_after=retry_after,
                                                           base=self.settings.backoff_base_seconds,
                                                           maximum=self.settings.backoff_max_seconds)
                    last_kind, last_message = ProviderErrorKind.RATE_LIMITED, f"{endpoint} HTTP 429 (backoff {delay:.1f}s)"
                    self.health.record_failure(self.name, last_kind.value, last_message)
                    log.warning("rate limited", extra={"provider": self.name, "endpoint": endpoint,
                                                       "backoff_s": round(delay, 1)})
                    continue  # the shared backoff is enforced by the next acquire()
                last_kind, last_message = ProviderErrorKind.HTTP_ERROR, f"{endpoint} HTTP {status}"
                if status in (401, 403):
                    self.health.record_failure(self.name, last_kind.value, last_message + " (check API key)")
                    raise ProviderError(self.name, last_kind, last_message + " (check API key)", status)
            self.health.record_failure(self.name, last_kind.value, last_message)
            log.warning("request failed", extra={"provider": self.name, "endpoint": endpoint, "attempt": attempt,
                                                 "kind": last_kind.value})
            if attempt < attempts:
                delay = min(self.settings.backoff_base_seconds * 2 ** (attempt - 1), self.settings.backoff_max_seconds)
                await self._sleep(delay * (0.8 + 0.4 * self._rng()))
        raise ProviderError(self.name, last_kind, f"{last_message} after {attempts} attempts", last_status)


def _parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None
