"""Encrypted hot-wallet keystore: scrypt + AES-GCM, file mode 0600. Imported only by ``desk.signer`` (M8-01).

The decrypted key exists only in the signer process. Nothing in this module logs; error messages never contain
key material or the passphrase.
"""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from solders.keypair import Keypair

VERSION = 1
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 15, 8, 1
MIN_PASSPHRASE = 12


class KeystoreError(Exception):
    pass


def _key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode())


def create(path: Path, passphrase: str) -> str:
    """Generate a new keypair, write it encrypted (refusing to overwrite) and return only the public key."""
    if len(passphrase) < MIN_PASSPHRASE:
        raise KeystoreError(f"passphrase must have at least {MIN_PASSPHRASE} characters")
    keypair = Keypair()
    public = str(keypair.pubkey())
    salt, nonce = os.urandom(16), os.urandom(12)
    ciphertext = AESGCM(_key(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)).encrypt(nonce, bytes(keypair), public.encode())
    document = {"version": VERSION, "pubkey": public, "kdf": "scrypt", "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P,
                "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
                "cipher": "AES-256-GCM", "ciphertext": base64.b64encode(ciphertext).decode()}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump(document, handle)
    return public


def _document(path: Path) -> dict[str, object]:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise KeystoreError(f"keystore permissions are {oct(mode)}; they must be 0600 (chmod 600 {path})")
    document: dict[str, object] = json.loads(path.read_text())
    if document.get("version") != VERSION or document.get("kdf") != "scrypt":
        raise KeystoreError("unsupported keystore format")
    return document


def public_key(path: Path) -> str:
    return str(_document(path)["pubkey"])


def unlock(path: Path, passphrase: str) -> Keypair:
    document = _document(path)
    try:
        secret = AESGCM(_key(passphrase, base64.b64decode(str(document["salt"])), int(str(document["n"])),
                             int(str(document["r"])), int(str(document["p"])))).decrypt(
            base64.b64decode(str(document["nonce"])), base64.b64decode(str(document["ciphertext"])),
            str(document["pubkey"]).encode())
    except InvalidTag:
        raise KeystoreError("wrong passphrase or corrupted keystore") from None
    keypair = Keypair.from_bytes(secret)
    if str(keypair.pubkey()) != document["pubkey"]:
        raise KeystoreError("keystore public key does not match its secret")
    return keypair
