"""Unix-domain-socket front of the signer (mode 0600). One JSON object per line in, one per line out."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path

from desk.signer.service import Signer, refusal

MAX_LINE = 65_536


async def serve(signer: Signer, socket_path: Path, stop: asyncio.Event) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        socket_path.unlink()
    lock = asyncio.Lock()                     # one intent at a time: no interleaved signing

    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readuntil(b"\n")
            if len(line) > MAX_LINE:
                answer = refusal("REQUEST_TOO_LARGE")
            else:
                try:
                    request = json.loads(line)
                except json.JSONDecodeError:
                    answer = refusal("NOT_JSON")
                else:
                    async with lock:
                        answer = await signer.handle(request)
            writer.write((json.dumps(answer, default=str) + "\n").encode())
            await writer.drain()
            if signer.disarmed:
                stop.set()
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            writer.close()

    previous = os.umask(0o177)                 # the socket is created 0600: only the operator's user connects
    try:
        server = await asyncio.start_unix_server(client, path=str(socket_path), limit=MAX_LINE)
    finally:
        os.umask(previous)
    os.chmod(socket_path, 0o600)
    async with server:
        await stop.wait()
    with contextlib.suppress(FileNotFoundError):
        socket_path.unlink()


async def request(socket_path: Path, payload: dict[str, object], timeout: float = 120.0) -> dict[str, object]:
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(str(socket_path), limit=MAX_LINE), timeout)
    try:
        writer.write((json.dumps(payload, default=str) + "\n").encode())
        await writer.drain()
        answer: dict[str, object] = json.loads(await asyncio.wait_for(reader.readuntil(b"\n"), timeout))
        return answer
    finally:
        writer.close()
