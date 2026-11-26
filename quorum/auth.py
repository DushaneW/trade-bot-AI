"""Optional dashboard password, for running Quorum on a server.

Set QUORUM_PASSWORD and every page, API call and WebSocket needs a session
cookie, which /login hands out. The cookie is an HMAC of the password with
the local secret key, so changing the password signs everyone out.
Without the variable the dashboard stays open, which is only safe on
127.0.0.1 (the launcher refuses anything else).
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import time
from collections.abc import Callable
from pathlib import Path

COOKIE = "quorum_session"
MAX_AGE = 30 * 86_400
MAX_FAILURES = 5          # wrong passwords from one address before it has to wait
LOCKOUT_S = 15 * 60
OPEN_PATHS = {"/login", "/api/health", "/static/favicon.svg", "/static/app.css"}


class Gate:
    def __init__(self, data_dir: Path, password: str | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.password = password if password is not None else os.environ.get("QUORUM_PASSWORD", "")
        self.clock = clock
        self._failures: dict[str, list[float]] = {}
        key_file = data_dir / "secret.key"
        self._key = key_file.read_bytes() if key_file.exists() else os.urandom(32)

    @property
    def enabled(self) -> bool:
        return bool(self.password)

    def token(self) -> str:
        digest = hashlib.sha256(self.password.encode()).digest()
        return hmac.new(self._key, b"session:" + digest, hashlib.sha256).hexdigest()

    def allows(self, cookie: str | None) -> bool:
        return not self.enabled or (cookie is not None and hmac.compare_digest(cookie, self.token()))

    def locked_for(self, client: str) -> float:
        """Seconds this address must wait after too many wrong passwords, 0 when it may try."""
        now = self.clock()
        recent = [t for t in self._failures.get(client, []) if now - t < LOCKOUT_S]
        self._failures[client] = recent
        return LOCKOUT_S - (now - recent[-MAX_FAILURES]) if len(recent) >= MAX_FAILURES else 0.0

    async def check_password(self, attempt: str, client: str = "", delay: float = 1.0) -> bool:
        ok = hmac.compare_digest(attempt.encode(), self.password.encode())
        if ok:
            self._failures.pop(client, None)
        else:
            self._failures.setdefault(client, []).append(self.clock())
            await asyncio.sleep(delay)  # slow down guessing
        return ok


LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quorum · Sign in</title><link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/app.css"></head>
<body class="login-body">
<form class="login" method="post" action="/login">
  <img src="/static/favicon.svg" alt="" width="44" height="44">
  <h1>Quorum</h1>
  <p>This dashboard controls real money. Enter the password set in QUORUM_PASSWORD.</p>
  {error}
  <input class="input" type="password" name="password" autocomplete="current-password" autofocus required
         placeholder="Password">
  <button class="btn primary" type="submit">Sign in</button>
</form></body></html>"""
