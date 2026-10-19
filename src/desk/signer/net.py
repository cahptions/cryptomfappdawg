"""Minimal network clients for the signer: Jupiter /swap/v2 order+execute and Solana JSON-RPC. httpx only."""

from __future__ import annotations

from typing import Any

import httpx

JUPITER_KEYED, JUPITER_KEYLESS = "https://api.jup.ag", "https://lite-api.jup.ag"


class JupiterSwap:
    def __init__(self, client: httpx.AsyncClient, api_key: str | None) -> None:
        self.client = client
        self.base = JUPITER_KEYED if api_key else JUPITER_KEYLESS
        self.headers = {"x-api-key": api_key} if api_key else {}

    async def order(self, input_mint: str, output_mint: str, amount: int, taker: str, slippage_bps: int) -> dict[str, Any]:
        response = await self.client.get(f"{self.base}/swap/v2/order", headers=self.headers, params={
            "inputMint": input_mint, "outputMint": output_mint, "amount": str(amount), "taker": taker,
            "slippageBps": slippage_bps})
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result

    async def execute(self, signed_transaction_b64: str, request_id: str) -> dict[str, Any]:
        response = await self.client.post(f"{self.base}/swap/v2/execute", headers=self.headers,
                                          json={"signedTransaction": signed_transaction_b64, "requestId": request_id})
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


class SolanaRpc:
    def __init__(self, client: httpx.AsyncClient, url: str) -> None:
        self.client, self.url = client, url

    async def _call(self, method: str, params: list[Any]) -> Any:
        response = await self.client.post(self.url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise RuntimeError(f"rpc {method} error {body['error'].get('code')}")
        return body["result"]

    async def block_height(self) -> int:
        return int(await self._call("getBlockHeight", [{"commitment": "confirmed"}]))

    async def accounts(self, addresses: list[str]) -> list[dict[str, Any] | None]:
        result = await self._call("getMultipleAccounts", [addresses, {"encoding": "base64", "commitment": "confirmed"}])
        return list(result["value"])

    async def simulate(self, transaction_b64: str, addresses: list[str]) -> dict[str, Any]:
        result = await self._call("simulateTransaction", [transaction_b64, {
            "encoding": "base64", "sigVerify": False, "replaceRecentBlockhash": False, "commitment": "confirmed",
            "accounts": {"encoding": "base64", "addresses": addresses}}])
        value: dict[str, Any] = result["value"]
        return value

    async def signature_status(self, signature: str) -> dict[str, Any] | None:
        result = await self._call("getSignatureStatuses", [[signature], {"searchTransactionHistory": True}])
        status: dict[str, Any] | None = result["value"][0]
        return status

    async def latest_blockhash(self) -> tuple[str, int]:
        result = await self._call("getLatestBlockhash", [{"commitment": "confirmed"}])
        return str(result["value"]["blockhash"]), int(result["value"]["lastValidBlockHeight"])

    async def send(self, transaction_b64: str) -> str:
        return str(await self._call("sendTransaction", [transaction_b64, {"encoding": "base64", "skipPreflight": False,
                                                                          "maxRetries": 0}]))
