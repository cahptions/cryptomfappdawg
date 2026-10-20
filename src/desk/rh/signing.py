"""Robinhood Crypto Trading API request signing and the laptop-local Ed25519 key.

The signed message is ``api_key + timestamp + path + method + body`` (docs, "API Signature"); the path includes
the query string exactly as sent and the body is the exact bytes sent. Robinhood rejects a timestamp older than
30 seconds, so every attempt is signed afresh. The private key is generated here and never leaves the laptop:
only its public half is pasted into Robinhood's crypto account settings to create the credential.
"""

from __future__ import annotations

import base64
import os
import stat
from collections.abc import Callable, Mapping
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from desk.core.errors import ConfigError

KEY_FILENAME = "robinhood.key"


def key_path(secrets_file: Path) -> Path:
    """The private key sits next to secrets.env (``~/.config/desk/robinhood.key``)."""
    return secrets_file.parent / KEY_FILENAME


def message(api_key: str, timestamp: int | str, path: str, method: str, body: str = "") -> bytes:
    return f"{api_key}{timestamp}{path}{method}{body}".encode()


def signature(private_key: Ed25519PrivateKey, api_key: str, timestamp: int | str, path: str, method: str,
              body: str = "") -> str:
    return base64.b64encode(private_key.sign(message(api_key, timestamp, path, method, body))).decode("ascii")


def public_key_b64(private_key: Ed25519PrivateKey) -> str:
    return base64.b64encode(private_key.public_key().public_bytes_raw()).decode("ascii")


def private_key_from_b64(seed_b64: str) -> Ed25519PrivateKey:
    try:
        seed = base64.b64decode(seed_b64.strip(), validate=True)
    except ValueError as exc:
        raise ConfigError("the Robinhood key file is not base64") from exc
    if len(seed) != 32:
        raise ConfigError("the Robinhood key file must hold a 32-byte Ed25519 seed")
    return Ed25519PrivateKey.from_private_bytes(seed)


def load_private_key(path: Path) -> Ed25519PrivateKey:
    if not path.is_file():
        raise ConfigError(f"no Robinhood key at {path}; run `desk rh keygen`")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ConfigError(f"{path} must be readable only by you: chmod 600 {path}")
    return private_key_from_b64(path.read_text(encoding="ascii"))


def generate_key(path: Path) -> str:
    """Create the key file (owner-only) and return the public key; an existing key is never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes_raw()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(descriptor, base64.b64encode(seed) + b"\n")
    finally:
        os.close(descriptor)
    return public_key_b64(key)


def signer(api_key: str, private_key: Ed25519PrivateKey,
           clock: Callable[[], float]) -> Callable[[str, str, bytes], Mapping[str, str]]:
    """Headers for one attempt: x-api-key, x-timestamp (epoch seconds) and x-signature."""

    def sign(method: str, path: str, body: bytes) -> Mapping[str, str]:
        timestamp = int(clock())
        return {"x-api-key": api_key, "x-timestamp": str(timestamp),
                "x-signature": signature(private_key, api_key, timestamp, path, method, body.decode("utf-8"))}

    return sign


__all__ = ["KEY_FILENAME", "generate_key", "key_path", "load_private_key", "message", "private_key_from_b64",
           "public_key_b64", "signature", "signer"]
