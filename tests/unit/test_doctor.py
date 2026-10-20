"""desk doctor's clock-vs-chain check: the newest confirmed slot may not be stored yet on the node that answers."""

from __future__ import annotations

from typing import Any, cast

import pytest

from desk.core.errors import ProviderError, ProviderErrorKind
from desk.ops import doctor
from desk.providers.solana_rpc import SolanaRpcClient

SLOT = 459_089_659


class FakeRpc:
    def __init__(self, answers: dict[int, Any]) -> None:
        self.answers, self.asked = answers, []

    async def get_block_time(self, slot: int) -> int | None:
        self.asked.append(slot)
        answer = self.answers.get(slot)
        if isinstance(answer, Exception):
            raise answer
        return cast(int | None, answer)


def unavailable(slot: int) -> ProviderError:
    return ProviderError("solana_rpc", ProviderErrorKind.RPC_ERROR, f"getBlockTime: Block not available for slot {slot}")


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "SLOT_SECONDS", 0)


async def test_steps_back_when_the_newest_block_is_not_stored_yet() -> None:
    rpc = FakeRpc({SLOT: unavailable(SLOT), SLOT - 1: None, SLOT - 2: 1_792_500_000})
    assert await doctor._chain_time(cast(SolanaRpcClient, rpc), SLOT) == 1_792_500_000
    assert rpc.asked == [SLOT, SLOT - 1, SLOT - 2]


async def test_newest_block_is_used_when_available() -> None:
    rpc = FakeRpc({SLOT: 1_792_500_001})
    assert await doctor._chain_time(cast(SolanaRpcClient, rpc), SLOT) == 1_792_500_001
    assert rpc.asked == [SLOT]


async def test_gives_up_after_a_few_slots() -> None:
    rpc = FakeRpc({SLOT - back: unavailable(SLOT - back) for back in range(10)})
    with pytest.raises(ProviderError, match="Block not available"):
        await doctor._chain_time(cast(SolanaRpcClient, rpc), SLOT)
    assert len(rpc.asked) == doctor.CHAIN_TIME_TRIES


async def test_network_failure_is_not_retried() -> None:
    rpc = FakeRpc({SLOT: ProviderError("solana_rpc", ProviderErrorKind.NETWORK, "connection reset")})
    with pytest.raises(ProviderError, match="connection reset"):
        await doctor._chain_time(cast(SolanaRpcClient, rpc), SLOT)
    assert rpc.asked == [SLOT]
