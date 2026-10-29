"""Private Schwab OAuth credentials. Tokens never pass through the raw-response/evidence HTTP layer.

The approved app supplies market-data access; this module implements no account/order requests. Token refresh
is serialized across threads/processes, rereads disk inside the lock, and atomically saves rotating credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import math
import os
import re
import secrets
import stat
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from desk.core.errors import DeskError
from desk.core.paths import DeskPaths
from desk.core.secrets import Secrets

AUTHORIZE_URL = "https://api.schwabapi.com/v1/oauth/authorize"
TOKEN_URL = "https://api.schwabapi.com/v1/oauth/token"  # noqa: S105 -- public endpoint, not a credential


# Schwab issues 30-minute access tokens and a refresh token that stops working 7 days after the browser login;
# only a new interactive login renews it.
LOGIN_LIFETIME_DAYS = 7
_ERROR_CODE = re.compile(r"^[A-Za-z_]{1,64}$")
RELOGIN = ("Schwab sign-in has expired: Schwab requires a fresh browser login every 7 days. In Settings choose "
           "Start browser authorization and log in again; your app key and secret are kept.")


class SchwabAuthError(DeskError):
    pass


class SchwabLoginExpired(SchwabAuthError):
    """The saved refresh authorization no longer works; only a new browser login fixes it."""


def token_path(paths: DeskPaths) -> Path:
    return paths.secrets_file.with_name("schwab-token.json")


def validate_callback(url: str) -> str:
    try:
        p = urlsplit(url)
        port = p.port
        if (p.scheme != "https" or not p.hostname or p.username is not None or p.password is not None
                or p.query or p.fragment or (port is not None and not 1 <= port <= 65535)
                or any(c.isspace() for c in url) or "\\" in url):
            raise ValueError
    except ValueError as exc:
        raise SchwabAuthError("Use the exact registered HTTPS callback, without a query, fragment or credentials") from exc
    return url


def authorization_url(app_key: str, callback: str, state: str) -> str:
    if not app_key or not state:
        raise SchwabAuthError("Schwab app key and a fresh authorization state are required")
    return AUTHORIZE_URL + "?" + urlencode({"client_id": app_key, "redirect_uri": validate_callback(callback),
                                          "response_type": "code", "state": state})


def callback_code(returned_url: str, registered: str, state: str) -> str:
    try:
        actual, expected = urlsplit(returned_url), urlsplit(validate_callback(registered))
        if (actual.scheme != expected.scheme or actual.netloc != expected.netloc
                or (actual.path or "/") != (expected.path or "/") or actual.fragment
                or len(returned_url) > 16000):
            raise ValueError
        values = parse_qs(actual.query, keep_blank_values=True)
        got = values.get("state", [])
        if len(got) != 1 or not secrets.compare_digest(got[0], state):
            raise ValueError
        if "error" in values:
            raise SchwabAuthError("Schwab authorization was declined; no token was saved")
        codes = values.get("code", [])
        if len(codes) != 1 or not codes[0] or len(codes[0]) > 8192:
            raise ValueError
        return codes[0]  # parse_qs decodes once; double decoding corrupts some authorization codes
    except ValueError as exc:
        raise SchwabAuthError("Callback address/state/code did not match this login attempt; no token was saved") from exc


@contextlib.contextmanager
def private_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if os.fstat(fd).st_uid != os.getuid() or stat.S_IMODE(os.fstat(fd).st_mode) & 0o077:
            raise SchwabAuthError("Schwab token lock must be owned by this user and private (chmod 600)")
        until = time.monotonic() + 20
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                if time.monotonic() >= until:
                    raise SchwabAuthError("Another Schwab token operation is busy; retry shortly") from exc
                time.sleep(0.05)
        yield
    finally:
        os.close(fd)


class SchwabTokens:
    def __init__(self, path: Path, credentials: Secrets, *, transport: httpx.BaseTransport | None = None) -> None:
        self.path, self.credentials, self.transport = path, credentials, transport

    @property
    def has_credentials(self) -> bool:
        return (self.credentials.file_mode_ok is not False and self.credentials.configured("SCHWAB_APP_KEY")
                and self.credentials.configured("SCHWAB_APP_SECRET"))

    @property
    def configured(self) -> bool:
        return self.has_credentials and self.path.is_file()

    def read(self) -> dict[str, Any]:
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            with os.fdopen(fd, "r", encoding="utf-8") as handle:
                info = os.fstat(handle.fileno())
                if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
                    raise SchwabAuthError("Schwab token file must be owned by this user and private (chmod 600)")
                raw = handle.read(65537)
                if len(raw) > 65536:
                    raise ValueError
                value = json.loads(raw)
            if not isinstance(value, dict) or value.get("format") != 1:
                raise ValueError
            for key in ("access_token", "refresh_token"):
                if (not isinstance(value.get(key), str) or not value[key] or len(value[key]) > 16000
                        or any(c.isspace() for c in value[key])):
                    raise ValueError
            if not math.isfinite(float(value["expires_at"])) or not math.isfinite(float(value["authorized_at"])):
                raise ValueError
            return dict(value)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise SchwabAuthError("Schwab token is missing or unreadable; run desk stocks connect") from exc

    def save(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink():
            raise SchwabAuthError("Refusing a symlink for the Schwab token file")
        name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix=".schwab-token-", delete=False) as handle:
                name = handle.name
                os.fchmod(handle.fileno(), 0o600)
                json.dump(value, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, self.path)
            name = None
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)

    @property
    def state_path(self) -> Path:
        return self.path.with_name(self.path.stem + ".state.json")

    def _mark_relogin(self, code: str | None) -> None:
        self.state_path.write_text(json.dumps({"relogin_required_at": time.time(), "error": code}), encoding="utf-8")
        os.chmod(self.state_path, 0o600)

    def _clear_relogin(self) -> None:
        self.state_path.unlink(missing_ok=True)

    def relogin_required(self) -> bool:
        return self.state_path.is_file()

    @staticmethod
    def _error_code(response: httpx.Response) -> str | None:
        """Only Schwab's OAuth error identifier (e.g. ``invalid_grant``), never its description or the body."""
        try:
            value = response.json()
        except ValueError:
            return None
        code = value.get("error") if isinstance(value, dict) else None
        return code if isinstance(code, str) and _ERROR_CODE.match(code) else None

    def _refused(self, grant: str, status: int, code: str | None) -> SchwabAuthError:
        detail = f"HTTP {status}{f', {code}' if code else ''}"
        if code in ("invalid_client", "unauthorized_client") or (status == 401 and grant != "refresh_token"):
            return SchwabAuthError(f"Schwab rejected the app key or secret ({detail}). In the developer portal check "
                                   "the app is Ready For Use, then copy its App Key and Secret into Settings again.")
        if grant == "refresh_token" and status in (400, 401):
            self._mark_relogin(code)
            return SchwabLoginExpired(f"{RELOGIN} ({detail})")
        if grant == "authorization_code":
            return SchwabAuthError(f"Schwab refused the login code ({detail}). A code works once and only for about "
                                   "30 seconds, and the callback URL must match the app exactly; start browser "
                                   "authorization again and finish promptly.")
        return SchwabAuthError(f"Schwab token request was refused ({detail}); stored credentials were retained")

    def _post(self, fields: dict[str, str]) -> dict[str, Any]:
        key, secret = self.credentials.get("SCHWAB_APP_KEY"), self.credentials.get("SCHWAB_APP_SECRET")
        if not self.has_credentials or not key or not secret:
            raise SchwabAuthError("Set SCHWAB_APP_KEY and SCHWAB_APP_SECRET privately in secrets.env")
        try:
            with httpx.Client(transport=self.transport, trust_env=True, follow_redirects=False, timeout=15) as client:
                response = client.post(TOKEN_URL, data=fields, auth=httpx.BasicAuth(key, secret))
            if response.status_code != 200:
                # Provider descriptions can echo credentials: only the OAuth error identifier is kept.
                raise self._refused(fields["grant_type"], response.status_code, self._error_code(response))
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            return dict(value)
        except httpx.HTTPError as exc:
            raise SchwabAuthError("Schwab token request could not complete; check connectivity and retry") from exc
        except (ValueError, TypeError) as exc:
            raise SchwabAuthError("Schwab returned an invalid token response; stored credentials were retained") from exc

    def _normalize(self, value: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            access = value["access_token"]
            refresh = value.get("refresh_token") or (previous or {}).get("refresh_token")
            lifetime = float(value["expires_in"])
            if (not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh
                    or len(access) > 16000 or len(refresh) > 16000 or any(c.isspace() for c in access + refresh)
                    or isinstance(value["expires_in"], bool)
                    or not math.isfinite(lifetime) or not 0 < lifetime <= 86400
                    or str(value.get("token_type", "Bearer")).lower() != "bearer"):
                raise ValueError
            return {"format": 1, "access_token": access, "refresh_token": refresh, "expires_at": time.time() + lifetime,
                    "authorized_at": (previous or {}).get("authorized_at", time.time())}
        except (KeyError, ValueError, TypeError) as exc:
            raise SchwabAuthError("Schwab token response is incomplete; stored credentials were retained") from exc

    def exchange(self, code: str, callback: str) -> None:
        with private_lock(self.path.with_suffix(".lock")):
            self.save(self._normalize(self._post({"grant_type": "authorization_code", "code": code,
                                                 "redirect_uri": validate_callback(callback)})))
            self._clear_relogin()

    def _headers(self) -> dict[str, str]:
        with private_lock(self.path.with_suffix(".lock")):
            current = self.read()
            if float(current["expires_at"]) <= time.time() + 60:
                if self.relogin_required():
                    raise SchwabLoginExpired(RELOGIN)  # asking again cannot succeed; avoid hammering the endpoint
                current = self._normalize(self._post({"grant_type": "refresh_token",
                                                      "refresh_token": current["refresh_token"]}), current)
                self.save(current)
            return {"Authorization": "Bearer " + str(current["access_token"])}

    async def headers(self) -> dict[str, str]:
        return await asyncio.to_thread(self._headers)

    def status(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"connected": False, "reason": "Run desk stocks connect", "credentials_set": self.has_credentials}
        try:
            value = self.read()
            age = (time.time() - float(value["authorized_at"])) / 86400
            left = LOGIN_LIFETIME_DAYS - age
            out = {"connected": self.has_credentials, "credentials_set": self.has_credentials,
                   "access_expires_in_seconds": round(float(value["expires_at"]) - time.time()),
                   "authorization_age_days": round(age, 2), "login_days_left": round(max(0.0, left), 2),
                   "relogin_required": False,
                   "note": (f"Signed in {age:.1f} days ago. Quotes refresh automatically; Schwab needs a fresh "
                            f"browser login every {LOGIN_LIFETIME_DAYS} days ({max(0.0, left):.1f} days left).")}
            if self.relogin_required() or left <= 0:
                out.update(connected=False, relogin_required=True, reason=RELOGIN)
            return out
        except SchwabAuthError as exc:
            return {"connected": False, "reason": str(exc), "credentials_set": self.has_credentials}
