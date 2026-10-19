"""`desk live keygen` and `desk live arm`: the only entry points that touch the keystore (M8-01)."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import signal
from pathlib import Path

import httpx

from desk.core.config import LoadedConfig
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets
from desk.signer import keystore
from desk.signer.journal import Journal
from desk.signer.net import JupiterSwap, SolanaRpc
from desk.signer.server import serve
from desk.signer.service import Signer

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"


def keystore_path(paths: DeskPaths) -> Path:
    return paths.secrets_file.parent / "hot-wallet.json"


def socket_path(paths: DeskPaths) -> Path:
    return paths.home / "run" / "signer.sock"


def keygen(paths: DeskPaths) -> int:
    path = keystore_path(paths)
    if path.exists():
        print(f"  a keystore already exists at {path}; refusing to overwrite it")
        return 1
    first = getpass.getpass("New passphrase (12+ characters, typed, never pasted into chats): ")
    if first != getpass.getpass("Repeat passphrase: "):
        print("  passphrases differ; nothing was created")
        return 1
    try:
        public = keystore.create(path, first)
    except keystore.KeystoreError as exc:
        print(f"  {exc}")
        return 1
    print(f"  hot wallet created. Public address (safe to share): {public}")
    print(f"  encrypted keystore: {path} (mode 0600). Back up the file AND remember the passphrase.")
    return 0


def public_address(paths: DeskPaths) -> str | None:
    path = keystore_path(paths)
    return keystore.public_key(path) if path.exists() else None


def arm(paths: DeskPaths, loaded: LoadedConfig, secrets: Secrets) -> int:
    """Unlock the key in this process only and serve intents until disarmed or interrupted."""
    if loaded.settings.live.max_capital_sol <= 0:
        print("  live.max_capital_sol is 0: LIVE is not allowed, so the signer will not be armed")
        return 1
    try:
        keypair = keystore.unlock(keystore_path(paths), getpass.getpass("Hot-wallet passphrase: "))
    except (keystore.KeystoreError, FileNotFoundError) as exc:
        print(f"  not armed: {exc}")
        return 1

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, stop.set)
        journal = Journal(paths.db_dir / "signer-journal.db")
        async with httpx.AsyncClient(timeout=20.0) as client:
            signer = Signer(keypair, journal, JupiterSwap(client, secrets.get("JUPITER_API_KEY")),
                            SolanaRpc(client, secrets.get("SOLANA_RPC_URL") or PUBLIC_RPC), loaded.settings.live,
                            loaded.settings.limits, loaded.settings.sizing)
            for result in await signer.recover():
                print(f"  recovered intent {result.get('intent_id')}: {result.get('state')}")
            print(f"  signer armed for {signer.wallet}; socket {socket_path(paths)} (Ctrl-C or `desk live disarm`)")
            await serve(signer, socket_path(paths), stop)
        journal.close()

    asyncio.run(main())
    print("  signer disarmed; the key is gone from memory with this process")
    return 0
