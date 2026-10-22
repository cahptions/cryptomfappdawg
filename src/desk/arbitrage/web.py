"""Loopback research UI. GETs only read; explicit CSRF-protected writes update this desk's local records."""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from desk.arbitrage.contracts import (
    DealLead,
    InventoryEvent,
    LeadOffer,
    Observation,
    Product,
    Route,
    Settings,
    SignalObservation,
    Source,
)
from desk.arbitrage.repo import Repo
from desk.arbitrage.runner import Collector, initialize
from desk.arbitrage.sources import ALLOWED_HOSTS, validate_source
from desk.arbitrage.views import snapshot
from desk.core.clock import utc_now
from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.web.app import install_browser_boundary

STATIC = Path(__file__).with_name("static")


async def document(request: Request) -> dict[str, Any]:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 500_000:
            raise HTTPException(413, "Import exceeds the 500 KB limit")
    import json

    try:
        value = json.loads(body)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(400, "Expected a JSON object") from exc
    if not isinstance(value, dict):
        raise HTTPException(400, "Expected a JSON object")
    return value


def create_app(paths: DeskPaths, *, auto_start: bool = True) -> FastAPI:
    csrf = secrets.token_urlsafe(32)
    collector = Collector(paths)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        initialize(paths)
        with Repo.reader(paths) as repo:
            enabled = repo.settings().enabled
        if auto_start and enabled:
            collector.start()
        try:
            yield
        finally:
            await collector.stop()

    app = FastAPI(title="KESTREL Arbitrage", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    from desk.arbitrage.discovery import IMAGE_HOSTS

    install_browser_boundary(app, csrf, image_hosts=tuple(sorted(IMAGE_HOSTS)))

    @app.exception_handler(DeskError)
    async def desk_error(_: Request, exc: DeskError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(ValidationError)
    async def validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        fields = [".".join(map(str, r["loc"])) for r in exc.errors()]
        return JSONResponse({"detail": "Invalid or missing inputs: " + ", ".join(fields[:20])}, status_code=422)

    @app.exception_handler(sqlite3.Error)
    async def storage_error(_: Request, exc: sqlite3.Error) -> JSONResponse:
        return JSONResponse({"detail": "Local storage unavailable/busy; no successful save was confirmed"}, status_code=503)

    @app.get("/")
    def home() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/session")
    def session() -> dict[str, str]:
        return {"csrf_token": csrf}

    @app.get("/api/setup-guides")
    def setup_guides() -> list[dict[str, Any]]:
        from desk.arbitrage.guides import GUIDES

        return GUIDES

    @app.get("/api/snapshot")
    def read_snapshot(query: str = "", category: str = "", offset: int = 0, order: str = "profit",
                      product_id: str | None = None, view: str = "catalog", source_host: str = "") -> dict[str, Any]:
        if (offset < 0 or len(source_host) > 200 or len(query) > 200 or len(category) > 80
                or order not in {"profit", "roi", "title"}):
            raise HTTPException(400, "Invalid catalog page or filters")
        with Repo.reader(paths) as repo:
            ids = [product_id] if product_id else None
            if view == "planning":
                ids = [r[0] for r in repo.conn.execute("SELECT DISTINCT product_id FROM arb_routes")]
            result = snapshot(repo, utc_now(), query=query, category=category, offset=offset, order=order,
                              product_ids=ids, source_host=source_host)
            if view == "planning":
                result["coverage"]["scope"] = "All configured selling routes; complete current inventory commitments"

        result["collector"] = {
            "active": bool(collector.task and not collector.task.done()),
            "external": collector.external,
            "error": collector.error,
        }
        from desk.arbitrage.integrations import status

        connection = status(paths)
        source_ids = {s["id"] for s in result["sources"] if s["adapter"] == "ebay_browse"}
        attempts = sorted((v for k, v in result["jobs"].items() if k in source_ids), key=lambda v: v.get("attempted_at", ""))
        if attempts:
            connection["connected"] = attempts[-1]["status"] == "ok" if attempts[-1]["status"] != "waiting" else None
            connection["last_success_at"] = max((v.get("last_ok_at") or "" for v in attempts), default="") or None
        result["integrations"] = {"ebay": connection}
        return result

    @app.post("/api/comparables/import")
    async def import_comparables(request: Request) -> dict[str, int]:
        import csv
        import io

        from desk.arbitrage.comparables import Comparable, save
        from desk.core.hashing import sha256_hex
        from desk.storage.db import transaction

        data = await document(request)
        raw = data.get("csv")
        if not isinstance(raw, str):
            raise HTTPException(400, "Choose a comparable-price CSV file")
        try:
            records = []
            for i, row in enumerate(csv.DictReader(io.StringIO(raw))):
                if i >= 500:
                    raise ValueError("Import is limited to 500 rows per file")
                row = {k: v for k, v in row.items() if v not in (None, "")}
                row["product_id"] = str(data.get("product_id") or "")
                row["id"] = "csv-" + sha256_hex(raw + str(i))[:32]
                record = Comparable.model_validate(row)
                if record.at.tzinfo is None or record.at > utc_now():
                    raise ValueError("Comparable times must include a timezone and cannot be in the future")
                records.append(record)
            if not records:
                raise ValueError("CSV contains no rows")
            with Repo.open(paths) as repo, transaction(repo.conn):
                for record in records:
                    save(repo, record)
            return {"saved": len(records)}
        except ValueError as exc:
            raise HTTPException(400, "Comparable import failed; all rows preserved unchanged. " +
                                str(exc).split("\n")[0][:150]) from exc

    @app.post("/api/integrations/ebay")
    async def save_ebay(request: Request) -> dict[str, bool]:
        from desk.arbitrage.integrations import Credentials, save

        credentials = Credentials.model_validate(await document(request))
        save(paths, credentials)
        with Repo.open(paths) as repo:
            if credentials.enabled:
                for key, query in [
                    ("drivers", "precision screwdriver kit"),
                    ("printers", "label printer"),
                    ("computers", "single board computer"),
                ]:
                    source_id = "ebay-starter-" + key
                    if not any(s.id == source_id for s in repo.sources()):
                        repo.save_source(Source(id=source_id, url="https://www.ebay.com/", adapter="ebay_browse", query=query))
                # A credential correction should not wait for a previous authentication-error backoff.
                for source in repo.sources():
                    if source.adapter == "ebay_browse":
                        repo.job(source.id, {"status": "waiting", "attempted_at": ""})
            enabled = repo.settings().enabled
        await collector.stop()
        if enabled:
            collector.start()
        return {"saved": True}

    @app.post("/api/leads/{lead_id}/{action}")
    async def lead_action(lead_id: str, action: str, request: Request) -> dict[str, bool]:
        data = await document(request)
        with Repo.open(paths) as repo:
            if action == "offer":
                from desk.storage.db import transaction

                value = LeadOffer.model_validate(data)
                row = repo.conn.execute("SELECT body FROM arb_leads WHERE id=?", (lead_id,)).fetchone()
                if row is None:
                    raise HTTPException(404, "Deal lead not found")
                lead = DealLead.model_validate_json(row[0])
                product_id = lead.id
                existing = next(iter(repo.products([product_id])), None)
                product = existing or Product(id=product_id, title=lead.title, category=lead.category, image_url=lead.image_url)
                with transaction(repo.conn):
                    repo.observe(
                        Observation(
                            product=product,
                            source_id="offer-" + lead.source_id,
                            url=value.url,
                            price=value.price,
                            currency=value.currency,
                            at=utc_now(),
                            availability="UNKNOWN",
                            method="operator-lead-review",
                            evidence=value.evidence,
                        )
                    )
                    repo.conn.execute("UPDATE arb_leads SET product_id=?,saved=1 WHERE id=?", (product_id, lead_id))
            else:
                repo.lead_action(lead_id, action)
        return {"saved": True}

    @app.get("/api/product/{product_id}")
    def product(product_id: str) -> dict[str, Any]:
        with Repo.reader(paths) as repo:
            rows = repo.observations(product_id, limit=500)
            products = repo.products([product_id])
            current = products[0] if products else None
            original_count = len(rows)
            if current:
                fields = ("brand", "mpn", "gtin", "variant", "pack_count", "condition")
                rows = [r for r in rows if all(r["product"].get(k) == getattr(current, k) for k in fields)]
            sales = [
                s.model_dump(mode="json")
                for s in repo.signals()
                if s.product_id == product_id and s.price is not None and s.kind in {"OWN_SALE", "CONFIRMED_UNITS"}
            ]
        return {
            "asking_history": list(reversed(rows)),
            "excluded_identity_observations": original_count-len(rows),
            "reported_sales": sales,
            "basis": "Operator-imported sales retain their source claim; asking prices are never sales",
        }

    @app.post("/api/settings")
    async def settings(request: Request) -> dict[str, bool]:
        value = Settings.model_validate(await document(request))
        with Repo.open(paths) as repo:
            repo.save_settings(value)
        if value.enabled:
            collector.start()
        else:
            await collector.stop()
        return {"saved": True}

    @app.post("/api/record/{kind}")
    async def save_record(kind: str, request: Request) -> dict[str, bool]:
        value = await document(request)
        models = {
            "source": Source,
            "product": Product,
            "route": Route,
            "signal": SignalObservation,
            "inventory": InventoryEvent,
            "observation": Observation,
        }
        if kind not in models:
            raise HTTPException(404, "Unknown record type")
        with Repo.open(paths) as repo:
            if kind == "source":
                source = Source.model_validate(value)
                validate_source(source)
                repo.save_source(source)
            elif kind == "product":
                repo.save_product(Product.model_validate(value), manual=True)
            elif kind == "route":
                repo.save_route(Route.model_validate(value))
            elif kind == "signal":
                signal = SignalObservation.model_validate(value)
                if signal.at > utc_now() or signal.available_at > utc_now():
                    raise HTTPException(400, "Future evidence is not accepted")
                repo.add_signal(signal)
            elif kind == "inventory":
                event = InventoryEvent.model_validate(value)
                if event.at > utc_now():
                    raise HTTPException(400, "Future inventory events are not accepted")
                repo.add_event(event)
            else:
                observed = Observation.model_validate(value)
                observed = observed.model_copy(update={"method": "operator-record"})
                if observed.at > utc_now():
                    raise HTTPException(400, "Future observations are not accepted")
                repo.observe(observed)
        return {"saved": True}

    @app.post("/api/verify/{observation_id}")
    async def verify(observation_id: int, request: Request) -> dict[str, bool]:
        value = await document(request)
        with Repo.open(paths) as repo:
            row = repo.conn.execute("SELECT body FROM arb_observations WHERE id=?", (observation_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "Offer not found")
            old = Observation.model_validate_json(row[0])
            now = utc_now()
            # Local operator attestation, explicitly labeled. Does not reserve stock or touch a cart.
            record = {
                **old.model_dump(mode="json"),
                **value,
                "at": now,
                "method": "operator-delivery-check",
                "verified_until": now + timedelta(minutes=5),
            }
            product_row = repo.conn.execute("SELECT body FROM arb_products WHERE id=?", (old.product.id,)).fetchone()
            if product_row is not None:
                record["product"] = Product.model_validate_json(product_row[0]).model_dump(mode="json")
            checked = Observation.model_validate(record)
            if checked.availability == "VERIFIED_AVAILABLE" and (
                not checked.evidence or not checked.region or not checked.quantity
            ):
                raise HTTPException(400, "Record the delivery region, positive quantity and verification evidence")
            if checked.product.id != old.product.id or checked.source_id != old.source_id or checked.url != old.url:
                raise HTTPException(400, "Verification cannot change product or source identity")
            repo.observe(checked)
        return {"saved": True}

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        contracts: list[tuple[str, type[BaseModel]]] = [
            ("settings", Settings),
            ("product", Product),
            ("source", Source),
            ("observation", Observation),
            ("route", Route),
            ("signal", SignalObservation),
            ("inventory", InventoryEvent),
        ]
        return {
            "retailer_hosts": sorted(ALLOWED_HOSTS | {"store.arduino.cc"}),
            "catalog": {"page_size": 50, "capacity": 50000, "source_setup": "Settings",
                        "source_browser": "Sources", "checkpoints": True, "real_acceptance": "unverified"},
            "resale_matching": "Read-only eBay GTIN + explicit pack + condition; current asking prices only",
            "price_evidence": ["ASKING", "REPORTED_SOLD", "TRANSACTION_RECORD", "OWN_NET", "REFERENCE"],
            "marketplace_writes": False,
            "fee_estimate_integrations": [],
            "confirmed_sales_integrations": [],
            "contracts": {name: model.model_json_schema() for name, model in contracts},
            "deal_discovery": {
                "feeds": ["Slickdeals public feed candidate", "DealNews public feed candidate"],
                "live_acceptance": "pending real-network verification",
                "evidence": "Published leads only; never stock, completed sales or profit",
            },
        }

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
