"""The shared tuner server: the web UI for several people, with Windows sign-in.

``pict serve`` runs the Streamlit UI in this process under uvicorn, behind :class:`SignInGate`.
The gate asks the browser to sign in with Windows (HTTP Negotiate: Kerberos, or NTLM when Kerberos
isn't available), checks the account against the ``[access]`` lists in the settings file, and then
hands the browser a signed cookie so the page's later requests and its live connection need no new
sign-in. The UI learns who is signed in from headers the gate adds; the gate strips any such headers a
browser sends, and nothing reaches the UI except through the gate.

PI is read with the identity the server runs as (the service account, when it runs as a Windows
service), so give that account a read-only PI mapping. Nothing here writes to PI.
"""

from __future__ import annotations

import base64
import html
import os
import sys
import time
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote

from .access import ADMIN, COOKIE, CookieSigner, Grant, decide, log_activity

USER_HEADER, ROLE_HEADER, VIA_HEADER = b"x-pict-user", b"x-pict-role", b"x-pict-via"
OPEN_PATHS = ("/_stcore/health", "/healthz")   # answer without sign-in, for monitoring
PENDING_SECONDS = 60                            # how long a half-finished NTLM sign-in is kept
SIGNIN_ENV = "PICT_SIGNIN"                      # set while the UI runs behind the gate


# --- Windows sign-in -----------------------------------------------------------

@dataclass
class Step:
    done: bool
    token: bytes | None = None                    # to send back to the browser, if any
    user: str = ""                                # DOMAIN\name, once done
    is_member: Callable[[str], bool] | None = None


class WindowsSignIn:
    """Server side of HTTP Negotiate through Windows SSPI (pywin32). One object per sign-in attempt."""

    def __init__(self):
        import sspi  # noqa: F401  (pywin32; fail early with ImportError off Windows)
        self._sa = None

    def step(self, token: bytes) -> Step:
        import sspi
        import sspicon
        import win32api
        import win32security
        if self._sa is None:
            self._sa = sspi.ServerAuth("Negotiate")
        err, out = self._sa.authorize(token)
        reply = bytes(out[0].Buffer) if out and out[0].Buffer else None
        if err == sspicon.SEC_I_CONTINUE_NEEDED:
            return Step(False, reply)
        ctxt = self._sa.ctxt
        user = str(ctxt.QueryContextAttributes(sspicon.SECPKG_ATTR_NAMES))
        ctxt.ImpersonateSecurityContext()
        try:
            user_token = win32security.OpenThreadToken(win32api.GetCurrentThread(), win32security.TOKEN_QUERY, True)
        finally:
            ctxt.RevertSecurityContext()
        sids: dict[str, Any] = {}

        def is_member(name: str) -> bool:
            if name not in sids:
                sids[name] = win32security.LookupAccountName(None, name)[0]
            return bool(win32security.CheckTokenMembership(user_token, sids[name]))
        return Step(True, reply, user, is_member)


def windows_sign_in_available() -> bool:
    try:
        WindowsSignIn()
        return True
    except ImportError:
        return False


def grant_from_headers(headers) -> Grant | None:
    """The signed-in person, from the headers the gate added (a mapping with any-case keys, as ``st.context.headers``)."""
    get = headers.get
    user = get(USER_HEADER.decode()) or get("X-Pict-User")
    role = get(ROLE_HEADER.decode()) or get("X-Pict-Role")
    if not user or role not in ("admin", "user"):
        return None
    return Grant(unquote(user), role, unquote(get(VIA_HEADER.decode()) or get("X-Pict-Via") or ""))


# --- the gate ------------------------------------------------------------------

def _headers(scope) -> dict[bytes, bytes]:
    return {k.lower(): v for k, v in scope.get("headers", [])}


def _cookie(scope) -> str:
    raw = _headers(scope).get(b"cookie", b"").decode("latin-1")
    try:
        jar = SimpleCookie(raw)
    except Exception:
        return ""
    return jar[COOKIE].value if COOKIE in jar else ""


def _page(title: str, body: str) -> bytes:
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title>"
            "<style>body{font:15px/1.5 'Segoe UI',system-ui,sans-serif;color:#17222a;max-width:40rem;margin:4rem auto;"
            "padding:0 1rem}h1{font-size:1.3rem;color:#13708a}code{background:#f4f7f8;padding:0 .25rem}</style></head>"
            f"<body><h1>{html.escape(title)}</h1>{body}</body></html>").encode()


NEED_SIGNIN = _page("Windows sign-in needed", (
    "<p>The PI Compression Tuner signs you in with your Windows account. Your browser did not offer it.</p>"
    "<p>Edge and Chrome do this on their own for sites in the Local intranet zone. If you are asked for a user name "
    "and password, use your Windows account (<code>DOMAIN\\name</code>). In Firefox, add this server to "
    "<code>network.negotiate-auth.trusted-uris</code> in <code>about:config</code>.</p>"))


@dataclass
class _Pending:
    attempt: Any
    since: float = field(default_factory=time.monotonic)


class SignInGate:
    """ASGI middleware: Windows sign-in, the access lists, and the identity headers the UI reads.

    ``new_attempt`` makes the object that runs one Negotiate exchange (:class:`WindowsSignIn` in production).
    ``load_access`` returns the current :class:`~.access.AccessSettings`; it is called at each sign-in so that
    changes saved on the Admin page apply to the next person who signs in.
    """

    def __init__(self, app, new_attempt: Callable[[], Any], load_access: Callable[[], Any],
                 signer: CookieSigner | None = None, secure_cookie: bool = False,
                 log: Callable[[str, str, str], None] = log_activity):
        self.app = app
        self.new_attempt = new_attempt
        self.load_access = load_access
        self.signer = signer or CookieSigner()
        self.secure_cookie = secure_cookie
        self.log = log
        self._pending: dict[tuple, _Pending] = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        clean = [(k, v) for k, v in scope.get("headers", []) if k.lower() not in (USER_HEADER, ROLE_HEADER, VIA_HEADER)]
        scope = dict(scope, headers=clean)
        if scope["type"] == "http" and scope.get("path") in OPEN_PATHS:
            return await self.app(scope, receive, send)
        grant = self.signer.read(_cookie(scope)) if _cookie(scope) else None
        if grant:
            return await self.app(self._with_identity(scope, grant), receive, send)
        if scope["type"] == "websocket":
            # The page signs in over plain HTTP first; a live connection without the cookie is refused.
            await send({"type": "websocket.close", "code": 4401})
            return
        await self._sign_in(scope, receive, send)

    @staticmethod
    def _with_identity(scope, grant: Grant):
        # Percent-encoded, so names outside ASCII survive every server's header decoding.
        extra = [(USER_HEADER, quote(grant.user).encode()), (ROLE_HEADER, grant.role.encode()),
                 (VIA_HEADER, quote(grant.via).encode())]
        return dict(scope, headers=list(scope["headers"]) + extra)

    async def _sign_in(self, scope, receive, send):
        auth = _headers(scope).get(b"authorization", b"").decode("latin-1")
        scheme, _, data = auth.partition(" ")
        if scheme.lower() != "negotiate" or not data.strip():
            return await self._respond(send, 401, NEED_SIGNIN, [(b"www-authenticate", b"Negotiate")])
        try:
            token_in = base64.b64decode(data.strip())
        except ValueError:
            return await self._respond(send, 400, _page("Bad sign-in", "<p>The browser sent a malformed sign-in.</p>"))

        now = time.monotonic()
        self._pending = {k: p for k, p in self._pending.items() if now - p.since < PENDING_SECONDS}
        key = tuple(scope.get("client") or ("?", 0))
        pending = self._pending.pop(key, None)
        attempt = pending.attempt if pending else self.new_attempt()
        import anyio
        try:
            step = await anyio.to_thread.run_sync(attempt.step, token_in)
        except Exception as e:
            self.log("", "sign-in failed", f"{key[0]}: {e}")
            return await self._respond(send, 401, _page("Windows sign-in failed", (
                "<p>Windows did not accept the sign-in. Close the browser and try again, or ask your PI administrator.</p>")),
                [(b"www-authenticate", b"Negotiate")])
        challenge = [(b"www-authenticate", b"Negotiate " + base64.b64encode(step.token))] if step.token else []
        if not step.done:
            self._pending[key] = _Pending(attempt)
            return await self._respond(send, 401, b"", challenge)

        access = self.load_access()
        grant = await anyio.to_thread.run_sync(decide, step.user, access, step.is_member or (lambda name: False))
        if grant is None:
            self.log(step.user, "turned away", f"from {key[0]}; not in the admins or users list")
            body = _page("No access to the PI Compression Tuner", (
                f"<p>You are signed in as <b>{html.escape(step.user)}</b>, which is not on this server's list of "
                "people who may use the tuner.</p><p>Ask a tuner admin to add your account, or a group you are in, "
                "on the Admin page.</p>"))
            return await self._respond(send, 403, body, challenge)
        self.log(grant.user, "signed in", f"{'admin' if grant.role == ADMIN else 'user'} through {grant.via}, from {key[0]}")
        cookie = f"{COOKIE}={self.signer.make(grant)}; Path=/; HttpOnly; SameSite=Lax"
        if self.secure_cookie:
            cookie += "; Secure"
        extra = challenge + [(b"set-cookie", cookie.encode())]

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                message = dict(message, headers=list(message.get("headers", [])) + extra)
            await send(message)
        await self.app(self._with_identity(scope, grant), receive, send_with_cookie)

    @staticmethod
    async def _respond(send, status: int, body: bytes, headers: list | None = None):
        hs = [(b"content-type", b"text/html; charset=utf-8"), (b"content-length", str(len(body)).encode()),
              (b"cache-control", b"no-store")] + (headers or [])
        await send({"type": "http.response.start", "status": status, "headers": hs})
        await send({"type": "http.response.body", "body": body})


# --- running it ----------------------------------------------------------------

STREAMLIT_OPTIONS = {   # the same look and behavior as `pict ui`
    "browser.gatherUsageStats": False,
    "server.showEmailPrompt": False,
    "server.headless": True,
    "client.toolbarMode": "minimal",
    "theme.base": "light",
    "theme.primaryColor": "#13708a",
    "theme.textColor": "#17222a",
    "theme.secondaryBackgroundColor": "#f4f7f8",
}


def _apply_streamlit_options():
    """Run under uvicorn, Streamlit doesn't read its command-line flags, so set the options directly."""
    from streamlit import config
    config.get_config_options()
    for name, value in STREAMLIT_OPTIONS.items():
        config.set_option(name, value, "command-line argument or environment variable")


def build_app(sign_in: bool = True, secure_cookie: bool = False, new_attempt: Callable[[], Any] | None = None):
    """The ASGI app: the Streamlit UI, behind the sign-in gate unless ``sign_in`` is False."""
    if sign_in:
        os.environ[SIGNIN_ENV] = "windows"
    else:
        os.environ.pop(SIGNIN_ENV, None)
    import streamlit as st
    from starlette.middleware import Middleware
    _apply_streamlit_options()

    from .settings import load_app_settings

    def load_access():
        try:
            return load_app_settings().access
        except ValueError:
            from .access import AccessSettings
            return AccessSettings()   # an unreadable file falls back to the safe default: local Administrators
    middleware = []
    if sign_in:
        middleware.append(Middleware(SignInGate, new_attempt=new_attempt or WindowsSignIn, load_access=load_access,
                                     secure_cookie=secure_cookie))
    script = Path(__file__).parent / "ui" / "app.py"
    return st.App(str(script), middleware=middleware)


def run(host: str = "0.0.0.0", port: int = 8501, sign_in: bool = True, cert: str | None = None,
        key: str | None = None) -> None:
    if sign_in and not windows_sign_in_available():
        raise RuntimeError("Windows sign-in needs Windows and pywin32. Use --no-sign-in only on a trusted machine.")
    import uvicorn
    app = build_app(sign_in=sign_in, secure_cookie=bool(cert))
    scheme = "https" if cert else "http"
    shown = os.environ.get("COMPUTERNAME", "localhost") if host in ("0.0.0.0", "::") else host
    print(f"PI Compression Tuner serving at {scheme}://{shown}:{port}/"
          + (" with Windows sign-in" if sign_in else " WITHOUT sign-in: anyone who can reach it is an admin"),
          flush=True)
    if not cert and sign_in and host not in ("localhost", "127.0.0.1"):
        print("Note: without --cert the page and its data cross the network unencrypted.", file=sys.stderr, flush=True)
    log_activity("", "server started", f"{scheme}://{shown}:{port} sign-in {'on' if sign_in else 'off'}")
    uvicorn.run(app, host=host, port=port, **_uvicorn_options(cert, key))


def _uvicorn_options(cert: str | None, key: str | None) -> dict:
    """Streamlit's own uvicorn settings (WebSocket protocol, ping, message size), with our TLS files."""
    try:
        from streamlit.web.server.starlette.starlette_server import _get_uvicorn_config_kwargs
        options = _get_uvicorn_config_kwargs()
    except Exception:   # a Streamlit without this helper: uvicorn's defaults work too
        options = {}
    options.update(ssl_certfile=cert, ssl_keyfile=key, log_level="warning")
    return options
