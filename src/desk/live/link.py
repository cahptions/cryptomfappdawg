"""The desk's side of the signer socket. One request per call; a lost answer is looked up, never repeated."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from desk.core.clock import parse_iso8601

Send = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
FINAL = ("CONFIRMED", "FAILED", "EXPIRED", "REFUSED", "NOT_RECEIVED")


@dataclass(frozen=True)
class Outcome:
    """CONFIRMED, FAILED (landed with an error: the fee was paid), EXPIRED (never landed), REFUSED (nothing was
    signed), NOT_RECEIVED (the signer never saw it), UNRESOLVED (the signer is still settling it) or UNKNOWN (no
    answer: look it up before doing anything else)."""

    state: str
    signature: str | None = None
    balances: dict[str, Any] | None = None
    problems: tuple[str, ...] = field(default_factory=tuple)

    @property
    def final(self) -> bool:
        return self.state in FINAL

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "signature": self.signature, "balances": self.balances,
                "problems": list(self.problems)}


def from_answer(answer: dict[str, Any]) -> Outcome:
    if answer.get("error") == "DUPLICATE_INTENT":          # the signer already holds this id: ask what became of it
        return Outcome("UNKNOWN", problems=("DUPLICATE_INTENT",))
    state = answer.get("state")
    if state in ("CONFIRMED", "FAILED", "EXPIRED"):
        balances = answer.get("balances")
        return Outcome(str(state), answer.get("signature") and str(answer["signature"]),
                       balances if isinstance(balances, dict) else None)
    if answer.get("ok") is False and answer.get("error"):  # every refusal happens before anything is signed
        problems = answer.get("problems") or [answer["error"]]
        return Outcome("REFUSED", problems=tuple(str(p) for p in problems))
    return Outcome("UNKNOWN", problems=("UNRECOGNIZED_ANSWER",))


def from_lookup(answer: dict[str, Any], expires_at: datetime, now: datetime) -> Outcome:
    if not answer.get("ok"):
        return Outcome("UNKNOWN", problems=(str(answer.get("error") or "LOOKUP_FAILED"),))
    if not answer.get("found"):
        # Only an expired request can no longer arrive and be signed later; until then it stays pending.
        return Outcome("NOT_RECEIVED") if now > expires_at else Outcome("UNKNOWN", problems=("NOT_FOUND_YET",))
    raw_detail = answer.get("detail")
    detail: dict[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}
    state = str(answer.get("state"))
    if state in ("CONFIRMED", "FAILED", "EXPIRED", "REFUSED"):
        balances = detail.get("balances")
        return Outcome(state, answer.get("signature") and str(answer["signature"]),
                       balances if isinstance(balances, dict) else None,
                       tuple(str(p) for p in detail.get("problems") or ()))
    return Outcome("UNRESOLVED", answer.get("signature") and str(answer["signature"]))


class SignerLink:
    def __init__(self, send: Send) -> None:
        self._send = send

    async def submit(self, payload: dict[str, Any]) -> Outcome:
        try:
            answer = await self._send(payload)
        except (FileNotFoundError, ConnectionRefusedError):
            return Outcome("REFUSED", problems=("SIGNER_NOT_RUNNING",))     # no connection: nothing was received
        except Exception as exc:  # the request may have arrived: look it up, never resend it
            return Outcome("UNKNOWN", problems=(f"NO_ANSWER:{type(exc).__name__}",))
        return from_answer(answer)

    async def lookup(self, intent_id: str, expires_at: str, now: datetime) -> Outcome:
        try:
            answer = await self._send({"op": "lookup", "intent_id": intent_id})
        except Exception as exc:
            return Outcome("UNKNOWN", problems=(f"LOOKUP_FAILED:{type(exc).__name__}",))
        return from_lookup(answer, parse_iso8601(expires_at), now)

    async def wallet(self) -> str | None:
        """The armed signer's public address (None when no signer is armed)."""
        try:
            answer = await self._send({"op": "status"})
        except Exception:
            return None
        wallet = answer.get("wallet") if answer.get("ok") else None
        return str(wallet) if wallet else None


__all__ = ["FINAL", "Outcome", "Send", "SignerLink", "from_answer", "from_lookup"]
