"""Token facts: on-chain mint accounts (verified) and provider token claims (unverified)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from desk.contracts.observed import SolAddress, UtcDatetime


class MintExtension(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    state: dict[str, Any] = Field(default_factory=dict)


class MintAccount(BaseModel):
    """A mint account read from Solana RPC. ``mint_authority is None`` means *no authority on-chain* (verified)."""

    model_config = ConfigDict(frozen=True)

    mint: SolAddress
    owner_program: SolAddress
    token_program: Literal["spl-token", "spl-token-2022"]
    decimals: int = Field(ge=0, le=255)
    supply_raw: int = Field(ge=0)
    mint_authority: SolAddress | None
    freeze_authority: SolAddress | None
    is_initialized: bool
    extensions: tuple[MintExtension, ...] = ()
    slot: int = Field(ge=0)
    observed_at: UtcDatetime
    raw_sha256: str | None = None


class TokenStatsWindow(BaseModel):
    model_config = ConfigDict(frozen=True)

    price_change_pct: Decimal | None = None
    holder_change_pct: Decimal | None = None
    liquidity_change_pct: Decimal | None = None
    volume_change_pct: Decimal | None = None
    buy_volume_usd: Decimal | None = None
    sell_volume_usd: Decimal | None = None
    buy_organic_volume_usd: Decimal | None = None
    sell_organic_volume_usd: Decimal | None = None
    num_buys: int | None = None
    num_sells: int | None = None
    num_traders: int | None = None
    num_organic_buyers: int | None = None
    num_net_buyers: int | None = None


class TokenAudit(BaseModel):
    model_config = ConfigDict(frozen=True)

    mint_authority_disabled: bool | None = None
    freeze_authority_disabled: bool | None = None
    top_holders_pct: Decimal | None = None   # undocumented definition (RESEARCH.md R-03)
    dev_mints: int | None = None
    dev_migrations: int | None = None
    is_sus: bool | None = None               # documented: present only when flagged


class TokenClaims(BaseModel):
    """What a provider *says* about a token. Every field is an unverified claim; None = not provided."""

    model_config = ConfigDict(frozen=True)

    source: str
    mint: SolAddress
    symbol: str | None = None
    name: str | None = None
    decimals: int | None = None
    token_program: SolAddress | None = None
    dev: SolAddress | None = None
    launchpad: str | None = None
    graduated_pool: SolAddress | None = None
    graduated_at: UtcDatetime | None = None
    first_pool: SolAddress | None = None
    first_pool_created_at: UtcDatetime | None = None
    created_at: UtcDatetime | None = None
    holder_count: int | None = None
    liquidity_usd: Decimal | None = None
    mcap_usd: Decimal | None = None
    fdv_usd: Decimal | None = None
    usd_price: Decimal | None = None
    circ_supply: Decimal | None = None
    total_supply: Decimal | None = None
    organic_score: Decimal | None = None
    organic_score_label: str | None = None
    tags: tuple[str, ...] = ()
    stats: dict[str, TokenStatsWindow] = Field(default_factory=dict)
    audit: TokenAudit | None = None
    provider_updated_at: UtcDatetime | None = None
    flags: tuple[str, ...] = ()
    observed_at: UtcDatetime
    raw_sha256: str | None = None
