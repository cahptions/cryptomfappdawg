"""Solana JSON-RPC: on-chain verification (mint accounts, supply, holders, slots)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from desk.contracts.tokens import MintAccount, MintExtension
from desk.core.errors import DeskError, ProviderError, ProviderErrorKind, SchemaError
from desk.providers.http import HttpResponse, ProviderHttp
from desk.providers.parsing import expect_dict, expect_list, load_json, opt_str, strict_int

PROVIDER = "solana_rpc"
TOKEN_PROGRAM = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  # noqa: S105 - public program id, verified on-chain
TOKEN_2022_PROGRAM = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"  # noqa: S105 - public program id, verified on-chain
_PROGRAM_NAMES = {TOKEN_PROGRAM: "spl-token", TOKEN_2022_PROGRAM: "spl-token-2022"}


class RpcCallError(DeskError):
    """The RPC node answered with a JSON-RPC error object."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message

    @property
    def is_rate_limit(self) -> bool:
        return self.code == 429 or "too many requests" in self.message.lower()


def rpc_result(body: bytes) -> Any:
    """Return the ``result`` of a JSON-RPC response or raise :class:`RpcCallError`."""
    doc = expect_dict(load_json(body), "rpc response")
    if "error" in doc:
        err = doc["error"] if isinstance(doc["error"], dict) else {"message": str(doc["error"])}
        code = err.get("code")
        raise RpcCallError(int(code) if isinstance(code, int) else None, str(err.get("message", "")))
    if "result" not in doc:
        raise SchemaError("JSON-RPC response has neither result nor error")
    return doc["result"]


def parse_mint_account(result: Any, mint: str, observed_at: datetime, raw_sha256: str | None = None) -> MintAccount | None:
    """Parse ``getAccountInfo(mint, {encoding: jsonParsed})``. Returns ``None`` if the account does not exist."""
    res = expect_dict(result, "result")
    slot = strict_int(expect_dict(res["context"], "context")["slot"])
    value = res.get("value")
    if value is None:
        return None
    value = expect_dict(value, "value")
    owner = value["owner"]
    data = value.get("data")
    if not isinstance(data, dict) or "parsed" not in data:
        raise SchemaError("account data is not jsonParsed (wrong encoding or not a token account)")
    parsed = expect_dict(data["parsed"], "parsed")
    if parsed.get("type") != "mint":
        raise SchemaError(f"account {mint} is not a mint (type={parsed.get('type')!r})")
    program = data.get("program")
    if program not in ("spl-token", "spl-token-2022") or _PROGRAM_NAMES.get(owner) != program:
        raise SchemaError(f"unexpected token program {program!r} / owner {owner!r}")
    info = expect_dict(parsed["info"], "info")
    extensions = tuple(
        MintExtension(name=str(expect_dict(ext, "extension")["extension"]), state=dict(ext.get("state") or {}))
        for ext in expect_list(info.get("extensions") or [], "extensions")
    )
    return MintAccount(
        mint=mint,
        owner_program=owner,
        token_program=program,
        decimals=strict_int(info["decimals"]),
        supply_raw=strict_int(info["supply"]),
        mint_authority=opt_str(info.get("mintAuthority")),
        freeze_authority=opt_str(info.get("freezeAuthority")),
        is_initialized=bool(info.get("isInitialized")),
        extensions=extensions,
        slot=slot,
        observed_at=observed_at,
        raw_sha256=raw_sha256,
    )


class SolanaRpcClient:
    def __init__(self, http: ProviderHttp) -> None:
        self.http = http
        self._next_id = 0

    async def call(self, method: str, params: list[Any] | None = None) -> tuple[Any, HttpResponse]:
        """One JSON-RPC call. JSON-RPC-level 429s share the HTTP backoff; other RPC errors raise ProviderError."""
        settings = self.http.settings
        last_error: RpcCallError | None = None
        for _ in range(settings.max_retries + 1):
            self._next_id += 1
            resp = await self.http.request("json_rpc", method="POST", label=f"json_rpc:{method}",
                                           json_body={"jsonrpc": "2.0", "id": self._next_id, "method": method,
                                                      "params": params or []})
            try:
                return rpc_result(resp.body), resp
            except RpcCallError as exc:
                last_error = exc
                if not exc.is_rate_limit:
                    self.http.health.record_failure(PROVIDER, ProviderErrorKind.RPC_ERROR.value, exc.message)
                    raise ProviderError(PROVIDER, ProviderErrorKind.RPC_ERROR, f"{method}: {exc.message}") from exc
                self.http.limiter.note_rate_limited(self.http.limiter_key("default"), retry_after=None,
                                                    base=settings.backoff_base_seconds,
                                                    maximum=settings.backoff_max_seconds)
                self.http.health.record_failure(PROVIDER, ProviderErrorKind.RATE_LIMITED.value, f"{method} rate limited")
            except SchemaError as exc:
                self.http.health.record_failure(PROVIDER, ProviderErrorKind.SCHEMA.value, str(exc))
                raise ProviderError(PROVIDER, ProviderErrorKind.SCHEMA, f"{method}: {exc}") from exc
        assert last_error is not None
        raise ProviderError(PROVIDER, ProviderErrorKind.RATE_LIMITED, f"{method}: {last_error.message}")

    async def get_health(self) -> str:
        result, _ = await self.call("getHealth")
        return str(result)

    async def get_slot(self, commitment: str = "confirmed") -> int:
        result, _ = await self.call("getSlot", [{"commitment": commitment}])
        return strict_int(result)

    async def get_block_time(self, slot: int) -> int | None:
        result, _ = await self.call("getBlockTime", [slot])
        return None if result is None else strict_int(result)

    async def get_mint_account(self, mint: str) -> MintAccount | None:
        result, resp = await self.call("getAccountInfo", [mint, {"encoding": "jsonParsed", "commitment": "confirmed"}])
        return parse_mint_account(result, mint, resp.received_at, resp.sha256)

    async def get_account_owner(self, address: str) -> str | None:
        result, _ = await self.call("getAccountInfo", [address, {"encoding": "base64", "dataSlice": {"offset": 0, "length": 0}}])
        value = expect_dict(result, "result").get("value")
        return None if value is None else opt_str(expect_dict(value, "value").get("owner"))

    async def get_token_largest_accounts(self, mint: str) -> list[dict[str, Any]]:
        result, _ = await self.call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}])
        return [expect_dict(v, "account") for v in expect_list(expect_dict(result, "result").get("value"), "value")]

    async def get_minimum_balance_for_rent_exemption(self, size: int) -> int:
        result, _ = await self.call("getMinimumBalanceForRentExemption", [size])
        return strict_int(result)
