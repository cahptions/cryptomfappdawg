"""``desk doctor``: a plain-English health report for the operator's machine and every data provider.

It never prints secret values, never places orders, and never touches a wallet. Network checks are read-only
(one or two requests per provider) and go through the same rate limiter as the rest of the desk.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import TypeVar

from desk import __version__
from desk.contracts.quotes import QuoteObservation
from desk.core.clock import utc_now
from desk.core.config import LoadedConfig, load_config
from desk.core.errors import ConfigError, DeskError, ProviderError, ProviderErrorKind
from desk.core.paths import REPO_ROOT, DeskPaths
from desk.core.secrets import Secrets, load_secrets
from desk.ops import system
from desk.providers.factory import ProviderSuite, build_suite
from desk.providers.registry import load_registry
from desk.providers.solana_rpc import SolanaRpcClient
from desk.storage.databases import Databases

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PUMP_TOKEN_2022_MINT = "7wrVy57WvGRxwn679U3buysv6FDdAV3hoDfJSqVrpump"  # noqa: S105 - public mint from the M0 evidence
CHAIN_TIME_TRIES = 4          # the newest slot may be skipped or not yet stored on the node that answers
SLOT_SECONDS = 0.4
T = TypeVar("T")


class Status(StrEnum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


@dataclass
class Check:
    group: str
    name: str
    status: Status
    detail: str
    hint: str | None = None


class Report:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, group: str, name: str, status: Status, detail: str, hint: str | None = None) -> None:
        self.checks.append(Check(group, name, status, detail, hint))

    @property
    def failed(self) -> bool:
        return any(c.status is Status.FAIL for c in self.checks)


def _machine_checks(report: Report, paths: DeskPaths) -> None:
    py = sys.version_info
    status = Status.OK if py >= (3, 11) else Status.FAIL
    report.add("machine", "Python", status, f"{py.major}.{py.minor}.{py.micro}",
               None if status is Status.OK else "Python 3.11 or newer is required (3.12 recommended)")

    free = system.disk_free_gb(paths.home)
    if free is None:
        report.add("machine", "Disk space", Status.WARN, "could not measure")
    else:
        status = Status.OK if free >= 10 else Status.WARN if free >= 2 else Status.FAIL
        report.add("machine", "Disk space", status, f"{free:.0f} GB free for {paths.home}",
                   None if status is Status.OK else "the desk needs several GB per month (PLAN.md §7.3)")

    mem = system.memory_info()
    if mem is None:
        report.add("machine", "Memory", Status.INFO, "not available on this system")
    else:
        status = Status.OK if mem.available_mb >= 2048 else Status.WARN
        report.add("machine", "Memory", status, f"{mem.available_mb / 1024:.1f} GB available of {mem.total_mb / 1024:.1f} GB")
        if mem.swap_total_mb:
            used = mem.swap_used_mb / mem.swap_total_mb
            report.add("machine", "Swap", Status.WARN if used > 0.5 else Status.OK,
                       f"{mem.swap_used_mb} MB of {mem.swap_total_mb} MB used ({used:.0%})",
                       "heavy swap use slows everything; consider closing apps/containers" if used > 0.5 else None)

    ntp = system.ntp_synchronized()
    report.add("machine", "Clock sync (NTP)",
               {True: Status.OK, False: Status.WARN, None: Status.INFO}[ntp],
               {True: "synchronized", False: "NOT synchronized", None: "timedatectl not available"}[ntp],
               "run: timedatectl set-ntp true" if ntp is False else None)

    bat = system.battery_info()
    if bat is None:
        report.add("machine", "Power", Status.INFO, "no battery found (desktop or container)")
    else:
        low = bat.status == "Discharging" and (bat.capacity_pct or 0) < 30
        report.add("machine", "Power", Status.WARN if low else Status.OK,
                   f"{bat.name}: {bat.status}, {bat.capacity_pct}%",
                   "plug in the charger: new entries will halt on low battery (PLAN.md §18)" if low else None)


def _secrets_checks(report: Report, secrets: Secrets) -> None:
    if not secrets.file_exists:
        report.add("keys", "Secrets file", Status.INFO, f"{secrets.file_path} does not exist (keyless mode)",
                   "optional: create it with your free Jupiter key and RPC URL — see README 'Free API keys'")
    else:
        report.add("keys", "Secrets file", Status.OK if secrets.file_mode_ok else Status.FAIL,
                   f"{secrets.file_path} ({'private' if secrets.file_mode_ok else 'readable by others'})",
                   None if secrets.file_mode_ok else f"run: chmod 600 {secrets.file_path}")
    for problem in secrets.problems:
        if "chmod" not in problem:
            report.add("keys", "Secrets file format", Status.WARN, problem)
    report.add("keys", "Jupiter API key", Status.OK if secrets.configured("JUPITER_API_KEY") else Status.INFO,
               "configured (value hidden)" if secrets.configured("JUPITER_API_KEY")
               else "not configured — keyless limits (≈30/min) apply")
    report.add("keys", "Solana RPC URL", Status.OK if secrets.configured("SOLANA_RPC_URL") else Status.WARN,
               "configured (value hidden)" if secrets.configured("SOLANA_RPC_URL")
               else "not configured — using the public endpoint (not for production; holder data may be blocked)",
               None if secrets.configured("SOLANA_RPC_URL") else "a free keyed RPC (e.g. Helius) is recommended")


async def _timed(report: Report, name: str, coro: Awaitable[T]) -> tuple[T | None, float]:
    """Await ``coro``; on a provider/desk error record a FAIL line and return ``None``."""
    start = time.monotonic()
    try:
        return await coro, (time.monotonic() - start) * 1000
    except (DeskError, ValueError) as exc:
        report.add("providers", name, Status.FAIL, str(exc)[:200])
        return None, (time.monotonic() - start) * 1000


async def _chain_time(rpc: SolanaRpcClient, slot: int) -> int:
    """Block time of ``slot`` or of the nearest earlier slot the answering node has; each step back is one slot."""
    for back in range(CHAIN_TIME_TRIES):
        if back:
            await asyncio.sleep(SLOT_SECONDS)
        try:
            block_time = await rpc.get_block_time(slot - back)
        except ProviderError as exc:
            if exc.kind is not ProviderErrorKind.RPC_ERROR or back == CHAIN_TIME_TRIES - 1:
                raise
            continue
        if block_time is not None:
            return block_time
    raise ProviderError("solana_rpc", ProviderErrorKind.RPC_ERROR, f"getBlockTime: no block time near slot {slot}")


async def _provider_checks(report: Report, suite: ProviderSuite) -> None:
    gt, ms = await _timed(report, "GeckoTerminal", suite.geckoterminal.new_pools(1))
    if gt is not None:
        report.add("providers", "GeckoTerminal", Status.OK if not gt.errors else Status.WARN,
                   f"{len(gt.items)} new pools in {ms:.0f} ms; {len(gt.errors)} schema problems")

    ds, ms = await _timed(report, "DexScreener", suite.dexscreener.tokens([USDC]))
    if ds is not None:
        report.add("providers", "DexScreener", Status.OK if not ds.errors else Status.WARN,
                   f"{len(ds.items)} USDC pairs in {ms:.0f} ms")

    quote, ms = await _timed(report, "Jupiter quote", suite.jupiter.order(SOL, USDC, 100_000_000))
    if isinstance(quote, QuoteObservation):
        usdc = quote.out_amount_raw / 1e6
        report.add("providers", "Jupiter quote", Status.OK,
                   f"0.1 SOL → {usdc:.4f} USDC (SOL ≈ ${usdc * 10:.2f}) via {quote.router}, "
                   f"fee {quote.fee_bps} bps, {ms:.0f} ms")
    elif quote is not None:
        report.add("providers", "Jupiter quote", Status.FAIL, f"no quote: {quote.error_code} {quote.error_message}")

    tokens, ms = await _timed(report, "Jupiter tokens", suite.jupiter.tokens_search([USDC, SOL]))
    if tokens is not None:
        host = "api.jup.ag (keyed)" if suite.jupiter.http.has_key else "lite-api.jup.ag (keyless)"
        report.add("providers", "Jupiter tokens", Status.OK if len(tokens.items) == 2 else Status.WARN,
                   f"{len(tokens.items)}/2 tokens from {host} in {ms:.0f} ms")

    slot, ms = await _timed(report, "Solana RPC", suite.rpc.get_slot())
    if slot is not None:
        endpoint = "keyed endpoint" if suite.rpc.http.has_key else "public endpoint"
        report.add("providers", "Solana RPC", Status.OK, f"slot {slot} in {ms:.0f} ms ({endpoint})")
        observed = utc_now()      # drift is measured from when the slot was current, not after any retry
        block_time, _ = await _timed(report, "Clock vs chain", _chain_time(suite.rpc, slot))
        if block_time is not None:
            drift = observed.timestamp() - block_time
            report.add("machine", "Clock vs chain", Status.OK if abs(drift) <= 5 else Status.WARN,
                       f"local clock is {drift:+.1f} s from the latest confirmed block time",
                       None if abs(drift) <= 5 else "check NTP; large drift corrupts freshness checks")

    mint, ms = await _timed(report, "RPC mint account", suite.rpc.get_mint_account(PUMP_TOKEN_2022_MINT))
    if mint is not None:
        report.add("providers", "RPC mint account", Status.OK,
                   f"Token-2022 mint parsed ({len(mint.extensions)} extensions) in {ms:.0f} ms")

    start = time.monotonic()
    try:
        holders = await suite.rpc.get_token_largest_accounts(PUMP_TOKEN_2022_MINT)
        report.add("providers", "RPC holder list", Status.OK,
                   f"getTokenLargestAccounts returned {len(holders)} accounts in {(time.monotonic() - start) * 1000:.0f} ms")
    except ProviderError as exc:
        report.add("providers", "RPC holder list", Status.WARN,
                   f"getTokenLargestAccounts unavailable: {exc.kind.value}",
                   "holder-concentration checks need this; configure a free keyed RPC (SOLANA_RPC_URL)")


async def run_doctor(paths: DeskPaths, *, offline: bool = False) -> Report:
    report = Report()
    report.add("desk", "Version", Status.INFO, f"desk {__version__}")
    _machine_checks(report, paths)
    secrets = load_secrets(paths.secrets_file)
    _secrets_checks(report, secrets)

    loaded: LoadedConfig | None = None
    try:
        loaded = load_config(paths.config_dir)
        report.add("desk", "Configuration", Status.OK,
                   f"mode {loaded.settings.desk.mode.value}, snapshot {loaded.snapshot_hash[:12]}, "
                   f"paper capital ${loaded.settings.paper.starting_capital_usd}")
    except ConfigError as exc:
        report.add("desk", "Configuration", Status.FAIL, str(exc)[:400])
    try:
        load_registry(paths.config_dir, REPO_ROOT)
        report.add("desk", "Provider registry", Status.OK, "valid; every provider costs $0/month")
    except ConfigError as exc:
        report.add("desk", "Provider registry", Status.FAIL, str(exc)[:400])

    try:
        dbs = Databases.open(paths)
    except (DeskError, OSError) as exc:
        report.add("desk", "Databases", Status.FAIL, str(exc)[:300])
        return report
    try:
        problems = {n: h for n, h in dbs.health().items() if h["integrity"] != "ok" or h["journal_mode"] != "wal"}
        report.add("desk", "Databases", Status.FAIL if problems else Status.OK,
                   f"problems: {problems}" if problems else f"4 databases ok (WAL) in {paths.db_dir}")
        if not offline and loaded is not None:
            suite = build_suite(paths, loaded, secrets, dbs)
            try:
                await _provider_checks(report, suite)
            finally:
                await suite.aclose()
    finally:
        dbs.close()
    return report


_SYMBOL = {Status.OK: "✓", Status.WARN: "!", Status.FAIL: "✗", Status.INFO: "·"}


def render(report: Report) -> str:
    lines = ["", "KESTREL desk doctor", "=" * 60]
    group = None
    for check in report.checks:
        if check.group != group:
            group = check.group
            lines.append(f"\n[{group}]")
        lines.append(f"  {_SYMBOL[check.status]} {check.name:<22} {check.detail}")
        if check.hint:
            lines.append(f"      → {check.hint}")
    summary = {s: sum(1 for c in report.checks if c.status is s) for s in Status}
    lines.append("")
    lines.append(f"Summary: {summary[Status.OK]} ok, {summary[Status.WARN]} warnings, {summary[Status.FAIL]} failures")
    lines.append("Paste this whole output into the chat so the results can be recorded in PROGRESS.md.")
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return json.dumps([asdict(c) for c in report.checks], indent=2)
