"""Local connection preferences, owner-only credential updates, and a dedicated one-use OAuth callback."""

from __future__ import annotations

import json
import os
import re
import secrets
import ssl
import stat
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from desk.core.config import StocksSection
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets, _parse_env_file
from desk.stocks.schwab_auth import SchwabAuthError, SchwabTokens, authorization_url, callback_code, private_lock, token_path


def preference_path(paths: DeskPaths) -> Path:
    return paths.home / "stocks-connection.json"


def preferences(paths: DeskPaths, settings: StocksSection) -> dict[str, str]:
    value = {"options_provider": settings.options_provider, "callback_url": settings.schwab_callback_url}
    path = preference_path(paths)
    if path.exists():
        try:
            local = json.loads(path.read_text(encoding="utf-8"))
            if local["options_provider"] not in ("tradier", "schwab"):
                raise ValueError
            value.update(options_provider=local["options_provider"], callback_url=validate_preference(local["callback_url"]))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SchwabAuthError("Local Stocks connection preferences are invalid; save Settings again") from exc
    return value


def validate_preference(url: str) -> str:
    from desk.stocks.schwab_auth import validate_callback

    return validate_callback(url)


def _atomic_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise SchwabAuthError("Refusing a symlink for local connection settings")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".stocks-settings-",
                                     delete=False) as handle:
        temporary = Path(handle.name)
        os.fchmod(handle.fileno(), 0o600)
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_connection(paths: DeskPaths, *, app_key: str, app_secret: str, callback: str, provider: str) -> None:
    callback = validate_preference(callback)
    if provider not in {"schwab", "tradier"}:
        raise SchwabAuthError("Choose Schwab or Tradier")
    updates = {"SCHWAB_APP_KEY": app_key.strip(), "SCHWAB_APP_SECRET": app_secret.strip()}
    if any(len(v) > 2000 or any(c in v for c in "\r\n\x00") for v in updates.values()):
        raise SchwabAuthError("App credentials must be single-line values")
    with private_lock(paths.secrets_file.with_name(".stocks-credentials.lock")):
        old = ""
        if paths.secrets_file.exists():
            descriptor = os.open(paths.secrets_file, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, encoding="utf-8") as handle:
                info = os.fstat(handle.fileno())
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise SchwabAuthError("Existing secrets.env must be owned by this user and private (chmod 600)")
                old = handle.read()
        # Blank password fields preserve an existing value. Other providers/comments are retained.
        replacing = {key: value for key, value in updates.items() if value}
        old_values, _ = _parse_env_file(old)
        lines = [line for line in old.splitlines() if not any(
            re.match(r"^\s*(?:export\s+)?" + re.escape(key) + r"\s*=", line) for key in replacing)]
        lines.extend(f"{key}='{value}'" for key, value in replacing.items())
        local_json = json.dumps({"options_provider": provider, "callback_url": callback}) + "\n"
        if any(old_values.get(key) != value for key, value in replacing.items()):
            token = token_path(paths)
            with private_lock(token.with_suffix(".lock")):
                # Invalidate before replacing credentials: a crash cannot pair old tokens with a changed app.
                token.unlink(missing_ok=True)
                _atomic_private(paths.secrets_file, "\n".join(lines) + "\n")
                _atomic_private(preference_path(paths), local_json)
        else:
            _atomic_private(paths.secrets_file, "\n".join(lines) + "\n")
            _atomic_private(preference_path(paths), local_json)


def local_tls(paths: DeskPaths) -> ssl.SSLContext:
    """A self-signed loopback certificate, never added to the system/browser trust store."""
    import ipaddress
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    directory = paths.secrets_file.parent
    cert, keyfile = directory / "schwab-loopback.crt", directory / "schwab-loopback.key"
    with private_lock(directory / ".schwab-certificate.lock"):
        if not cert.exists() or not keyfile.exists():
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KESTREL local OAuth callback")])
            now = datetime.now(UTC)
            certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                           .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                           .not_valid_after(now + timedelta(days=365)).add_extension(x509.SubjectAlternativeName([
                               x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                           .sign(key, hashes.SHA256()))
            _atomic_private(keyfile, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                      serialization.NoEncryption()).decode())
            _atomic_private(cert, certificate.public_bytes(serialization.Encoding.PEM).decode())
        if (keyfile.is_symlink() or cert.is_symlink() or not keyfile.is_file() or not cert.is_file()
                or keyfile.stat().st_uid != os.getuid() or stat.S_IMODE(keyfile.stat().st_mode) & 0o077):
            raise SchwabAuthError("Loopback certificate key must be a private regular file owned by you")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(cert), str(keyfile))
        return context


class LoginAttempt:
    """Loopback only: local HTTPS, or HTTP behind optional private Tailscale Serve."""

    def __init__(self, tokens: SchwabTokens, callback: str, app_key: str, *, port: int = 8788, timeout: float = 300,
                 tls: ssl.SSLContext | None = None) -> None:
        if not 1024 <= port <= 65535:
            raise SchwabAuthError("Callback listener port must be 1024..65535")
        self.tokens, self.callback = tokens, validate_preference(callback)
        self.state = secrets.token_urlsafe(32)
        self.url = authorization_url(app_key, self.callback, self.state)
        self.error: str | None = None
        self.complete = threading.Event()
        self.deadline = time.monotonic() + timeout
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(5)
                super().setup()

            def log_message(self, _format: str, *args: Any) -> None:
                pass  # request targets contain authorization codes; never log them

            def do_GET(self) -> None:
                expected = urlsplit(owner.callback)
                host = self.headers.get("Host", "")
                code = None
                try:
                    if host not in {expected.netloc, f"127.0.0.1:{port}", f"localhost:{port}"}:
                        raise SchwabAuthError("Unexpected callback host")
                    code = callback_code(f"https://{expected.netloc}{self.path}", owner.callback, owner.state)
                except SchwabAuthError:
                    self.send_response(400)
                    message = b"Callback not accepted. Use the current KESTREL login attempt."
                else:
                    try:
                        owner.tokens.exchange(code, owner.callback)
                        self.send_response(200)
                        message = b"KESTREL authorization saved. Close this page and return to Settings."
                    except SchwabAuthError as exc:
                        owner.error = str(exc)
                        self.send_response(502)
                        message = b"Authorization could not be saved. Return to KESTREL for the next step."
                    finally:
                        owner.complete.set()
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.end_headers()
                self.wfile.write(message)

        try:
            self.server = HTTPServer(("127.0.0.1", port), Handler)
            self.server.timeout = 0.5
            if tls is not None:
                self.server.socket = tls.wrap_socket(self.server.socket, server_side=True, do_handshake_on_connect=False)
        except OSError as exc:
            raise SchwabAuthError("Callback port is busy or unavailable; finish/stop the previous login first") from exc
        self.thread = threading.Thread(target=self._run, name="schwab-callback", daemon=True)

    def _run(self) -> None:
        try:
            while not self.complete.is_set() and time.monotonic() < self.deadline:
                self.server.handle_request()
            if not self.complete.is_set():
                self.error = "Authorization timed out; start a fresh login"
        finally:
            self.server.server_close()
            self.complete.set()

    def start(self) -> str:
        self.thread.start()
        return self.url

    def stop(self) -> None:
        self.complete.set()
        if self.thread.is_alive():
            self.thread.join(timeout=17)
        else:
            self.server.server_close()


def connection_status(paths: DeskPaths, settings: StocksSection, credentials: Secrets) -> dict[str, Any]:
    return {**preferences(paths, settings), "schwab": SchwabTokens(token_path(paths), credentials).status(),
            "orders_enabled": False, "mode": "PAPER"}
