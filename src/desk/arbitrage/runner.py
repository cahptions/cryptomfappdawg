"""One cancellable local collector, bounded requests and persistent pause. No trading imports or child processes."""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import timedelta
from typing import Any

import httpx

from desk.arbitrage.contracts import Source
from desk.arbitrage.repo import Repo
from desk.arbitrage.sources import fetch
from desk.core.clock import utc_now
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.worker_lock import WorkerBusy, WorkerLock
from desk.storage.db import transaction


def spend_ebay_request(paths: DeskPaths) -> None:
    """Local conservative ceiling, not a claim about the provider's allowed quota or pricing."""
    today = utc_now().date().isoformat()
    with Repo.open(paths) as repo, transaction(repo.conn):
        from datetime import datetime

        from desk.arbitrage.ebay import Backoff

        backoff = repo.jobs().get("ebay-provider", {}).get("retry_at")
        if backoff and datetime.fromisoformat(backoff) > utc_now():
            raise Backoff(datetime.fromisoformat(backoff))
        row = repo.conn.execute("SELECT value FROM arb_meta WHERE key='ebay-http-budget'").fetchone()
        budget: dict[str, Any] = json.loads(row[0]) if row else {"day": today, "requests": 0}
        if budget["day"] != today:
            budget = {"day": today, "requests": 0}
        if budget["requests"] >= 2000:
            raise DeskError("Local eBay ceiling of 2000 HTTP attempts/day reached; no further request was sent")
        budget["requests"] += 1
        repo.conn.execute("INSERT OR REPLACE INTO arb_meta VALUES ('ebay-http-budget',?)", (json.dumps(budget),))


def initialize(paths: DeskPaths) -> None:
    with Repo.open(paths) as repo:
        if not repo.conn.execute("SELECT 1 FROM arb_meta WHERE key='seed_v1'").fetchone():
            # A real URL to observe, not a fabricated product, price, stock level or business recommendation.
            repo.save_source(Source(id="adafruit-4300", url="https://www.adafruit.com/product/4300"))
            repo.conn.execute("INSERT INTO arb_meta VALUES ('seed_v1','1')")


async def cycle(paths: DeskPaths, *, client: httpx.AsyncClient) -> dict[str, Any]:
    now = utc_now()
    if shutil.disk_usage(paths.home).free < 512 * 1024 * 1024:
        raise DeskError("Collection paused: less than 512 MiB free disk; cached research remains readable")
    with Repo.reader(paths) as repo:
        sources, jobs = repo.sources(), repo.jobs()
    # Oldest-attempted first; a broken source cannot monopolize the next bounded batch.
    due = sorted((s for s in sources if s.enabled), key=lambda s: jobs.get(s.id, {}).get("attempted_at", ""))[:5]
    attempted = 0
    from desk.arbitrage.ebay import Backoff, Browse
    from desk.arbitrage.integrations import load

    credential_error = ""
    try:
        ebay: Browse | None = Browse(load(paths), lambda: spend_ebay_request(paths))
    except DeskError as exc:
        ebay, credential_error = None, str(exc)
    for source in due:
        last = jobs.get(source.id, {})
        from datetime import datetime

        if last.get("retry_at") and datetime.fromisoformat(last["retry_at"]) > now:
            continue
        state: dict[str, Any] = {"attempted_at": now.isoformat(), "status": "fetching", "last_ok_at": last.get("last_ok_at")}
        with Repo.open(paths) as repo:
            repo.job(source.id, state)
        try:
            if source.adapter == "ebay_browse":
                if ebay is None:
                    raise DeskError(credential_error)
                observed_rows = await ebay.search(client, source, utc_now())
            else:
                observed_rows = [await fetch(source, utc_now(), client=client)]
            with Repo.open(paths) as repo:
                for observed in observed_rows:
                    repo.observe(observed)
            state.update(status="ok", last_ok_at=utc_now().isoformat(), error=None, items=len(observed_rows))
        except Backoff as exc:
            state.update(status="unavailable", error=str(exc), retry_at=exc.until.isoformat())
            with Repo.open(paths) as repo:
                repo.job("ebay-provider", {"status": "rate_limited", "retry_at": exc.until.isoformat()})
        except httpx.HTTPError:
            state.update(
                status="unavailable",
                error="Network/proxy/timeout failure; no offer was manufactured",
                retry_at=(now + timedelta(minutes=15)).isoformat(),
            )
        except DeskError as exc:
            state.update(status="unavailable", error=str(exc), retry_at=(now + timedelta(minutes=15)).isoformat())
        with Repo.open(paths) as repo:
            repo.job(source.id, state)
        attempted += 1
        await asyncio.sleep(2)  # serial requests; avoid a burst against one retailer
    with Repo.open(paths) as repo:
        # Licensing-aware bounded derived-price history; inventory and own transaction records are never pruned.
        repo.conn.execute("DELETE FROM arb_observations WHERE at<?", ((now - timedelta(days=90)).isoformat(),))
        repo.conn.execute(
            "DELETE FROM arb_observations WHERE at<? AND json_extract(body,'$.method')='ebay-browse-active-listing-v1'",
            ((now - timedelta(hours=24)).isoformat(),),
        )
        repo.conn.execute(
            "DELETE FROM arb_observations WHERE id NOT IN (SELECT id FROM arb_observations ORDER BY id DESC LIMIT 100000)"
        )
        repo.job("collector", {"status": "idle", "last_cycle_at": utc_now().isoformat(), "attempted": attempted})
        from desk.arbitrage.evidence import archive

        archive(repo, utc_now())
    return {"attempted": attempted}


class Collector:
    def __init__(self, paths: DeskPaths) -> None:
        self.paths = paths
        self.task: asyncio.Task[None] | None = None
        self.error: str | None = None
        self.external = False

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.error = None
            self.task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def run(self) -> None:
        lock = WorkerLock(self.paths)
        while True:
            try:
                lock.acquire()
                break
            except WorkerBusy:
                self.external = True
                await asyncio.sleep(5)  # acquire automatically after a separate bounded CLI pass finishes
        self.external = False
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15), limits=httpx.Limits(max_connections=1), follow_redirects=False
            ) as client:
                while True:
                    with Repo.reader(self.paths) as repo:
                        settings = repo.settings()
                    if not settings.enabled:
                        return
                    await cycle(self.paths, client=client)
                    await asyncio.sleep(settings.interval_seconds)
        except (DeskError, OSError) as exc:
            # No restart loop: expose a bounded fault and let the operator retry after resolving it.
            self.error = type(exc).__name__ + ": collection stopped; inspect disk and source status"
        except Exception:
            self.error = "Unexpected collector fault; collection stopped and cached records preserved"
            raise
        finally:
            lock.release()
