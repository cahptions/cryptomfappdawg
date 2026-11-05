"""Gap repair asks Schwab for all ten days of minute bars it documents (periodType=day, period=10); the stock page's
week chart keeps asking for five."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from desk.stocks.sources.schwab import SchwabClient


async def test_gap_repair_asks_for_ten_days_of_minute_bars() -> None:
    client = SchwabClient(SimpleNamespace(), SimpleNamespace(headers=None, configured=True))
    client._get = AsyncMock(return_value=json.dumps({"symbol": "SPY", "candles": []}).encode())  # type: ignore[method-assign]
    now = datetime(2026, 11, 5, 21, tzinfo=UTC)
    await client.intraday_history("SPY", now, days=10)
    assert client._get.call_args.args[1]["period"] == 10 and client._get.call_args.args[1]["periodType"] == "day"
    await client.intraday_history("SPY", now)
    assert client._get.call_args.args[1]["period"] == 5
    with pytest.raises(ValueError, match="1-5 or 10"):
        await client.intraday_history("SPY", now, days=7)
