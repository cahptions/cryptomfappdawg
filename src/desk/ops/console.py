"""Small read-only M2 console. The full React workstation remains M7.

No credentials, remote images, dynamic HTML, controls, or wallet routes. Reads existing databases without
creating or migrating them. The server always binds to IPv4 loopback.
"""

from __future__ import annotations

import json
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from desk.core.config import load_config
from desk.core.paths import DeskPaths
from desk.storage.discovery_repo import DiscoveryRepository

_HTML = b'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>KESTREL | Discovery</title><link rel="stylesheet" href="/style.css"><script defer src="/app.js"></script>
<body><header><strong>KESTREL</strong><span>Discovery &amp; safety</span><b id="mode">READ ONLY</b></header>
<main><h1>Desk overview</h1><p id="connection" role="status">Loading local evidence...</p>
<section id="summary"></section><article><h2>Provider health</h2><pre id="health"></pre></article>
<article><h2>Candidate funnel</h2><pre id="funnel"></pre></article>
<article><h2>Recent candidates</h2><div class="scroll"><table><thead><tr><th>Mint</th><th>Reason</th>
<th>State</th><th>Stage</th><th>Latest rule</th></tr></thead><tbody id="candidates"></tbody></table></div></article>
<p>Read-only M2 console. No trading or LIVE activation is available.</p></main></body></html>'''
_CSS = b'''body{margin:0;background:#090d13;color:#e5ecf5;font:14px system-ui,sans-serif}
header{display:flex;gap:28px;align-items:center;padding:22px 32px;background:#121923;border-bottom:1px solid #293244}
strong{letter-spacing:4px;color:#b8a1ff}header b{margin-left:auto;color:#66dbc4}main{max-width:1400px;margin:auto;padding:24px}
h1{font-size:24px}h2{font-size:16px}p,header span{color:#a8b6ca}article,.metric{background:#121923;border:1px solid #293244;
border-radius:12px;padding:18px;margin-bottom:18px}#summary{display:flex;flex-wrap:wrap;gap:16px}.metric{min-width:160px}
.metric b{display:block;font-size:25px;margin-top:8px;font-variant-numeric:tabular-nums}pre{white-space:pre-wrap;line-height:1.6}
table{width:100%;border-collapse:collapse;text-align:left}td,th{padding:12px 10px;border-bottom:1px solid #293244}
td:first-child{font-family:monospace}.scroll{overflow:auto}.error{color:#ffacaa}'''
_JS = b'''async function refresh(){try{const r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error();
const d=await r.json();document.getElementById('mode').textContent=d.mode+' / READ ONLY';
document.getElementById('connection').textContent='Last collection: '+(d.last_run?.ended_at?'ended '+d.last_run.ended_at:
d.last_run?'started '+d.last_run.started_at:'no recorded run')+' | Refreshed '+new Date().toLocaleTimeString();
const s=document.getElementById('summary');s.replaceChildren();for(const [label,value] of Object.entries(d.report.by_state||{})){
const card=document.createElement('div');card.className='metric';
const title=document.createElement('span');title.textContent=label;
const number=document.createElement('b');number.textContent=String(value);card.append(title,number);s.append(card);}
if(!s.children.length)s.textContent='No candidates recorded. Start a discovery session to collect evidence.';
document.getElementById('health').textContent=d.health.length?d.health.map(x=>x.provider+': '+x.state+
' | last success '+(x.last_success_at||'none')).join('\\n'):'No provider requests recorded.';
document.getElementById('funnel').textContent=JSON.stringify(d.report,null,2);
const body=document.getElementById('candidates');body.replaceChildren();
for(const x of d.candidates){const row=document.createElement('tr');
for(const field of ['mint','reason','state','stage','rule_id']){
const td=document.createElement('td');td.textContent=String(x[field]??'Pending');
row.append(td);}body.append(row);}document.getElementById('connection').className='';}
catch(e){const el=document.getElementById('connection');
el.textContent='Local status unavailable. Previously displayed evidence may be stale.';
el.className='error';}}refresh();setInterval(()=>{if(!document.hidden)refresh();},5000);'''


def status_payload(paths: DeskPaths) -> dict[str, Any]:
    loaded = load_config(paths.config_dir)
    payload: dict[str, Any] = {"mode": loaded.settings.desk.mode.value, "report": {"by_state": {}},
                               "candidates": [], "health": [], "last_run": None}
    market_path = paths.db_dir / "market.db"
    if market_path.exists():
        conn = sqlite3.connect(f"file:{market_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            payload["report"] = DiscoveryRepository(conn, loaded.settings.discovery).report()
            payload["candidates"] = [dict(r) for r in conn.execute(
                "SELECT d.mint, d.reason, d.state, d.stage, "
                "(SELECT rule_id FROM stage_results s WHERE s.candidate_id = d.id ORDER BY id DESC LIMIT 1) AS rule_id "
                "FROM discoveries d ORDER BY last_observed_at DESC LIMIT 100")]
            last = conn.execute("SELECT id, started_at, ended_at FROM collector_runs ORDER BY started_at DESC LIMIT 1").fetchone()
            payload["last_run"] = None if last is None else dict(last)
        finally:
            conn.close()
    runtime_path = paths.db_dir / "runtime.db"
    if runtime_path.exists():
        conn = sqlite3.connect(f"file:{runtime_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            payload["health"] = [dict(r) for r in conn.execute(
                "SELECT provider, state, last_success_at FROM provider_health ORDER BY provider")]
        finally:
            conn.close()
    return payload


def make_handler(paths: DeskPaths) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            host = self.headers.get("Host", "").split(":", 1)[0]
            if host not in {"127.0.0.1", "localhost"}:
                self.send_error(403)
                return
            routes = {"/": ("text/html", _HTML), "/app.js": ("text/javascript", _JS), "/style.css": ("text/css", _CSS)}
            if self.path == "/api/status":
                try:
                    body = json.dumps(status_payload(paths)).encode()
                except sqlite3.Error:
                    self.send_error(503, "Local evidence unavailable; initialize the database first")
                    return
                content_type = "application/json"
            elif self.path in routes:
                content_type, body = routes[self.path]
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return  # Request URLs are not copied into logs.

    return Handler


def serve(paths: DeskPaths, port: int) -> None:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    server = HTTPServer(("127.0.0.1", port), make_handler(paths))
    print(f"KESTREL read-only console listening on loopback port {port}. Press Ctrl+C to stop.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
