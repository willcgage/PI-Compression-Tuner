"""The sign-in gate: unit tests through ASGI calls, and the real UI behind it in a server process."""

import asyncio
import base64
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pi_compression_tuner.access import ADMIN, USER, AccessSettings, CookieSigner, Grant
from pi_compression_tuner.serve import SignInGate, Step, grant_from_headers

sys.path.insert(0, str(Path(__file__).parent / "helpers"))
from gate_server import FakeSignIn  # noqa: E402


class Seen:
    """A stand-in for the UI: records the scope it was called with and answers 200."""

    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/plain")]})
            await send({"type": "http.response.body", "body": b"ui"})
        else:
            await send({"type": "websocket.accept"})


def call(gate, path="/", headers=(), kind="http", client=("10.0.0.5", 50000)):
    scope = {"type": kind, "path": path, "headers": [(k.lower().encode(), v.encode()) for k, v in headers],
             "client": client}
    sent = []

    async def receive():
        return {"type": "http.request", "body": b""} if kind == "http" else {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)
    asyncio.run(gate(scope, receive, send))
    start = next((m for m in sent if m["type"] in ("http.response.start",)), None)
    hdrs = {}
    for k, v in (start or {}).get("headers", []):
        hdrs.setdefault(k.decode(), []).append(v.decode())
    return (start or {}).get("status"), hdrs, sent


def negotiate(token: bytes) -> tuple[str, str]:
    return ("Authorization", "Negotiate " + base64.b64encode(token).decode())


@pytest.fixture
def gate():
    access = AccessSettings(admins=["CORP\\ann"], users=["CORP\\PI Engineers"])
    log = []
    ui = Seen()
    g = SignInGate(ui, new_attempt=FakeSignIn, load_access=lambda: access, signer=CookieSigner(b"k" * 32),
                   log=lambda *a: log.append(a))
    g.ui, g.logged = ui, log
    return g


def test_no_sign_in_gets_a_negotiate_challenge_and_a_help_page(gate):
    status, hdrs, sent = call(gate)
    assert status == 401 and hdrs["www-authenticate"] == ["Negotiate"]
    assert b"Windows sign-in needed" in sent[1]["body"] and gate.ui.scopes == []


def test_health_needs_no_sign_in(gate):
    assert call(gate, "/_stcore/health")[0] == 200


def test_admin_signs_in_gets_a_cookie_and_the_ui_sees_who(gate):
    status, hdrs, _ = call(gate, headers=[negotiate(b"user:CORP\\ann")])
    assert status == 200
    assert "Negotiate " + base64.b64encode(b"mutual").decode() in hdrs["www-authenticate"]
    cookie = hdrs["set-cookie"][0]
    assert cookie.startswith("pict_signin=") and "HttpOnly" in cookie
    seen = dict(gate.ui.scopes[-1]["headers"])
    assert grant_from_headers({k.decode(): v.decode() for k, v in seen.items()}) == Grant("CORP\\ann", ADMIN, "CORP\\ann")
    assert gate.logged[-1][:2] == ("CORP\\ann", "signed in")

    # Later requests and the live connection ride on the cookie.
    value = cookie.split(";")[0]
    assert call(gate, "/static/app.js", headers=[("Cookie", value)])[0] == 200
    call(gate, "/_stcore/stream", headers=[("Cookie", value)], kind="websocket")
    assert gate.ui.scopes[-1]["type"] == "websocket"


def test_group_member_is_a_user_and_outsider_is_turned_away(gate):
    call(gate, headers=[negotiate(b"user:CORP\\carol")])
    h = {k.decode(): v.decode() for k, v in gate.ui.scopes[-1]["headers"]}
    assert grant_from_headers(h).role == USER
    status, _, sent = call(gate, headers=[negotiate(b"user:CORP\\eve")])
    assert status == 403 and b"CORP\\eve" in sent[1]["body"]
    assert gate.logged[-1][:2] == ("CORP\\eve", "turned away")


def test_two_step_sign_in_on_one_connection(gate):
    status, hdrs, _ = call(gate, headers=[negotiate(b"two-step")])
    assert status == 401 and hdrs["www-authenticate"] == ["Negotiate " + base64.b64encode(b"challenge").decode()]
    assert call(gate, headers=[negotiate(b"user:CORP\\ann")])[0] == 200
    # A new connection starts over rather than continuing someone else's exchange.
    call(gate, headers=[negotiate(b"two-step")], client=("10.0.0.6", 1))
    assert len(gate._pending) == 1


def test_failed_windows_sign_in_is_401_not_a_crash(gate):
    status, hdrs, _ = call(gate, headers=[negotiate(b"rubbish")])
    assert status == 401 and gate.logged[-1][1] == "sign-in failed"


def test_identity_headers_from_the_browser_are_dropped(gate):
    forged = [("X-Pict-User", "CORP\\ann"), ("X-Pict-Role", "admin")]
    status, _, _ = call(gate, headers=forged)
    assert status == 401 and gate.ui.scopes == []
    assert call(gate, "/_stcore/health", headers=forged)[0] == 200
    assert not any(k.startswith(b"x-pict") for k, _ in gate.ui.scopes[-1]["headers"])


def test_websocket_without_cookie_is_closed(gate):
    _, _, sent = call(gate, "/_stcore/stream", kind="websocket")
    assert sent == [{"type": "websocket.close", "code": 4401}] and gate.ui.scopes == []


def test_bad_or_old_cookie_means_signing_in_again(gate):
    old = CookieSigner(b"k" * 32, hours=-1).make(Grant("CORP\\ann", ADMIN, "CORP\\ann"))
    assert call(gate, headers=[("Cookie", f"pict_signin={old}")])[0] == 401
    assert call(gate, headers=[("Cookie", "pict_signin=forged.value")])[0] == 401


# --- the real UI behind the gate --------------------------------------------------

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    pytest.importorskip("streamlit")
    requests = pytest.importorskip("requests")
    port = _free_port()
    env = dict(os.environ, PICT_CONFIG=str(tmp_path / "pict.toml"))
    (tmp_path / "pict.toml").write_text('[access]\nadmins = ["CORP\\\\ann"]\nusers = ["CORP\\\\PI Engineers"]\n')
    helper = Path(__file__).parent / "helpers" / "gate_server.py"
    p = subprocess.Popen([sys.executable, str(helper), str(port)], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                if requests.get(base + "/_stcore/health", timeout=1).text == "ok":
                    break
            except requests.ConnectionError:
                time.sleep(0.25)
        else:
            raise AssertionError(p.stdout.read1().decode() if p.stdout else "no server")
        yield base, tmp_path
    finally:
        p.terminate()
        p.wait(10)


def test_real_ui_behind_the_gate(server):
    import requests
    base, folder = server
    assert requests.get(base + "/").status_code == 401
    s = requests.Session()
    r = s.get(base + "/", headers=dict([negotiate(b"user:CORP\\carol")]))
    assert r.status_code == 200 and "<html" in r.text.lower()
    assert s.get(base + "/_stcore/host-config").status_code == 200   # the cookie carries on
    log = (folder / "pict-activity.log").read_text()
    assert "CORP\\carol\tsigned in\tuser through CORP\\PI Engineers" in log

    websockets = pytest.importorskip("websockets.sync.client")
    ws_url = base.replace("http", "ws") + "/_stcore/stream"
    with pytest.raises(Exception):
        websockets.connect(ws_url, open_timeout=5).close()
    cookie = "; ".join(f"{k}={v}" for k, v in s.cookies.items())
    with websockets.connect(ws_url, additional_headers={"Cookie": cookie}, subprotocols=["streamlit"],
                            open_timeout=10) as ws:
        assert ws.protocol.state.name == "OPEN"
