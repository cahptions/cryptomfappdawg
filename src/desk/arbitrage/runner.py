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
        if not repo.conn.execute("SELECT 1 FROM arb_meta WHERE key='seed_discovery_v1'").fetchone():
            from desk.arbitrage.discovery import FEEDS

            for key, feed in FEEDS.items():
                repo.save_source(Source(id="deals-" + key, url=feed["url"], adapter="deal_feed"))
            repo.conn.execute("INSERT INTO arb_meta VALUES ('seed_discovery_v1','1')")
        if not repo.conn.execute("SELECT 1 FROM arb_meta WHERE key='seed_catalog_v1'").fetchone():
            from desk.arbitrage.catalog import CATALOGS

            for key, (adapter, url) in CATALOGS.items():
                repo.save_source(Source(id=key, url=url, adapter=adapter))
            repo.conn.execute("INSERT INTO arb_meta VALUES ('seed_catalog_v1','1')")

        if not repo.conn.execute("SELECT 1 FROM arb_meta WHERE key='seed_bulk_catalog_v2'").fetchone():
            from desk.arbitrage.catalog import CATALOGS

            existing = {s.id for s in repo.sources()}
            for key, (adapter, url) in CATALOGS.items():
                if key not in existing:
                    repo.save_source(Source(id=key, url=url, adapter=adapter))
            repo.conn.execute("INSERT INTO arb_meta VALUES ('seed_bulk_catalog_v2','1')")


async def cycle(paths: DeskPaths, *, client: httpx.AsyncClient) -> dict[str, Any]:
    now = utc_now()
    if shutil.disk_usage(paths.home).free < 512 * 1024 * 1024:
        raise DeskError("Collection paused: less than 512 MiB free disk; cached research remains readable")
    with Repo.reader(paths) as repo:
        sources, jobs = repo.sources(), repo.jobs()
    # Oldest-attempted first; a broken source cannot monopolize the next bounded batch.
    from datetime import datetime

    due = sorted(
        (
            s
            for s in sources
            if s.enabled
            and (not jobs.get(s.id, {}).get("retry_at") or datetime.fromisoformat(jobs[s.id]["retry_at"]) <= now)
            and (
                (s.adapter != "deal_feed" and not jobs.get(s.id, {}).get("complete"))
                or not jobs.get(s.id, {}).get("attempted_at")
                or now - datetime.fromisoformat(jobs[s.id]["attempted_at"]) >= timedelta(minutes=15)
            )
        ),
        key=lambda s: (jobs.get(s.id, {}).get("attempted_at", ""),
                       0 if s.id == "arduino-us-catalog" else 1 if s.adapter == "adafruit_category" else 2),
    )[:5]
    attempted = 0
    from desk.arbitrage.discovery import FeedDelay
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
        state: dict[str, Any] = {**last, "attempted_at": now.isoformat(), "status": "fetching",
                                 "last_ok_at": last.get("last_ok_at")}
        with Repo.open(paths) as repo:
            repo.job(source.id, state)
            repo.job("collector", {"status": "collecting", "phase": "Importing products" if
                source.adapter in {"adafruit_sitemap", "adafruit_category", "shopify_catalog"} else "Checking sources",
                "source": source.id, "updated_at": utc_now().isoformat()})
        try:
            if source.adapter in {"adafruit_sitemap", "adafruit_category", "shopify_catalog"}:
                from desk.arbitrage.catalog import fetch_catalog

                observed_rows, urls, checkpoint = await fetch_catalog(source, utc_now(), client, last)
                with Repo.open(paths) as repo:
                    repo.queue_offers(urls, source.id, now.isoformat())
                state.update(checkpoint)
                if checkpoint.get("complete"):
                    state["retry_at"] = (now + (timedelta(minutes=15) if source.adapter == "adafruit_category"
                                                       else timedelta(hours=6))).isoformat()
            elif source.adapter == "deal_feed":
                from desk.arbitrage.discovery import fetch_feed

                leads, cache, unchanged = await fetch_feed(source, utc_now(), client=client, previous=last)
                with Repo.open(paths) as repo:
                    repo.save_leads(leads)
                    repo.queue_offers([url for lead in leads for url in lead.merchant_urls], source.id, now.isoformat())
                state.update(
                    status="ok", last_ok_at=utc_now().isoformat(), error=None, items=len(leads), unchanged=unchanged, **cache
                )
                if unchanged:
                    state.update({k: last[k] for k in ("etag", "last-modified") if k in last})
                observed_rows = []
            elif source.adapter == "ebay_browse":
                if ebay is None:
                    raise DeskError(credential_error)
                observed_rows = await ebay.search(client, source, utc_now(), offset=int(last.get("next_offset", 0)))
                state.update(next_offset=ebay.next_offset, total_results=ebay.total)
                if ebay.next_offset == 0:
                    state["retry_at"] = (now + timedelta(hours=1)).isoformat()
            else:
                observed_rows = [await fetch(source, utc_now(), client=client)]
            with Repo.open(paths) as repo, transaction(repo.conn):
                for observed in observed_rows:
                    repo.observe(observed)
            if source.adapter != "deal_feed":
                state.update(status="ok", last_ok_at=utc_now().isoformat(), error=None, items=len(observed_rows))
        except FeedDelay as exc:
            state.update(status="unavailable", error=str(exc), retry_at=exc.until.isoformat())
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
        if state["status"] == "unavailable":
            for key in ("next_offset", "next_page", "page_digest", "pending_sitemaps", "visited_sitemaps",
                        "complete", "page_size"):
                if key in last:
                    state[key] = last[key]
                else:
                    state.pop(key, None)
        with Repo.open(paths) as repo:
            repo.job(source.id, state)
        attempted += 1
        await asyncio.sleep(2)  # serial requests; avoid a burst against one retailer
    # Persistent URL queue; at most 10 products/cycle, 1000 reserved page/robots attempts/day.
    with Repo.reader(paths) as repo:
        # Split the small batch between new catalog work and due refreshes, so neither starves the other.
        due_queue = [dict(r) for r in repo.conn.execute(
            "SELECT q.* FROM arb_offer_queue q JOIN arb_sources s ON s.id=q.source_id "
            "WHERE json_extract(s.body,'$.enabled')=1 AND (q.retry_at IS NULL OR q.retry_at<=?) "
            "ORDER BY COALESCE(q.attempted_at,''),q.url LIMIT 100", (now.isoformat(),))]
        new = [r for r in due_queue if r["attempted_at"] is None]
        refresh = [r for r in due_queue if r["attempted_at"] is not None]
        # Query refreshes separately when a large new backlog fills the first bounded window.
        if not refresh:
            refresh = [dict(r) for r in repo.conn.execute(
                "SELECT q.* FROM arb_offer_queue q JOIN arb_sources s ON s.id=q.source_id "
                "WHERE json_extract(s.body,'$.enabled')=1 AND q.attempted_at IS NOT NULL AND q.retry_at<=? "
                "ORDER BY q.attempted_at,q.url LIMIT 5", (now.isoformat(),))]
        queued = [*new[:5], *refresh[:5]]
        keys = {r["url"] for r in queued}
        queued.extend(r for r in due_queue if r["url"] not in keys)
        queued = queued[:10]
    for item in queued:
        with Repo.open(paths) as repo:
            budget = repo.conn.execute("SELECT value FROM arb_meta WHERE key='merchant-budget'").fetchone()
            b = json.loads(budget[0]) if budget else {}
            if b.get("day") != now.date().isoformat():
                b = {"day": now.date().isoformat(), "attempts": 0}
            if b["attempts"] >= 1000:
                break
            b["attempts"] += 2  # robots and product request; reserve before requests, including failures
            repo.conn.execute("INSERT OR REPLACE INTO arb_meta VALUES ('merchant-budget',?)", (json.dumps(b),))
        error, product_id = None, item["product_id"]
        monitored = False
        try:
            observation = await fetch(Source(id=item["source_id"], url=item["url"]), utc_now(), client=client)
            with Repo.open(paths) as repo:
                repo.observe(observation)
                monitored = bool(repo.conn.execute("SELECT 1 FROM arb_routes WHERE product_id=? LIMIT 1",
                                                   (observation.product.id,)).fetchone())
            product_id = observation.product.id
        except (DeskError, httpx.HTTPError) as exc:
            error = str(exc)[:250] if isinstance(exc, DeskError) else "Merchant network/proxy failure"
        with Repo.open(paths) as repo:
            repo.conn.execute("UPDATE arb_offer_queue SET attempted_at=?,product_id=?,error=?,retry_at=? WHERE url=?",
                              (now.isoformat(), product_id, error,
                               (now + (timedelta(minutes=10) if monitored else timedelta(hours=6)) if not error
                                else now + timedelta(minutes=30)).isoformat(), item["url"]))
        await asyncio.sleep(1)
    if ebay is not None and ebay.credentials.enabled and ebay.credentials.free_access_confirmed:
        from desk.arbitrage.matching import enrich

        matching = await enrich(paths, ebay, client, utc_now())
        with Repo.open(paths) as repo:
            repo.job("resale-matching", {"status": "partial" if matching["errors"] else "ok", **matching})
    with Repo.open(paths) as repo:
        repo.conn.execute("DELETE FROM arb_comparables WHERE at<? AND json_extract(body,'$.evidence_type')='ASKING'",
                          ((now-timedelta(hours=24)).isoformat(),))
        # Licensing-aware bounded derived-price history; inventory and own transaction records are never pruned.
        repo.conn.execute("DELETE FROM arb_observations WHERE at<?", ((now - timedelta(days=90)).isoformat(),))
        repo.conn.execute(
            "DELETE FROM arb_observations WHERE at<? AND json_extract(body,'$.method')='ebay-browse-active-listing-v1'",
            ((now - timedelta(hours=24)).isoformat(),),
        )
        repo.conn.execute(
            "DELETE FROM arb_observations WHERE id NOT IN (SELECT id FROM arb_observations ORDER BY id DESC LIMIT 100000)"
        )
        final_jobs = repo.jobs()
        pending = sum(s.enabled and (not final_jobs.get(s.id, {}).get("attempted_at") or (
            s.adapter in {"shopify_catalog", "adafruit_sitemap"} and final_jobs.get(s.id, {}).get("status") == "ok"
            and not final_jobs[s.id].get("complete"))) for s in sources)
        repo.job("collector", {"status": "idle", "backfill_pending": pending,
                               "last_cycle_at": utc_now().isoformat(), "attempted": attempted})
        from desk.arbitrage.evidence import archive

        archive(repo, utc_now())
    return {"attempted": attempted, "backfill_pending": pending}


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
                    result = await cycle(self.paths, client=client)
                    await asyncio.sleep(min(60, settings.interval_seconds) if result["backfill_pending"]
                                        else settings.interval_seconds)
        except (DeskError, OSError) as exc:
            # No restart loop: expose a bounded fault and let the operator retry after resolving it.
            self.error = type(exc).__name__ + ": collection stopped; inspect disk and source status"
        except Exception:
            self.error = "Unexpected collector fault; collection stopped and cached records preserved"
            raise
        finally:
            lock.release()
