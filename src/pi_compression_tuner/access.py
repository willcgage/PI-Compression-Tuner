"""Who may use a shared tuner server, and what they may change.

When the tuner runs as a shared server (``pict serve``), people sign in with their Windows account.
Two lists in the settings file decide what they get:

    [access]
    admins = ["BUILTIN\\Administrators", "CORP\\PI Admins"]   # may also change the Admin settings
    users = ["CORP\\PI Engineers", "CORP\\jdoe"]             # may use the tuner

Each entry is a Windows account or group, written DOMAIN\\name (any case). A group entry covers
everyone in it, nested groups included, as Windows itself decides. Admins can always use the tuner.
Anyone in neither list is turned away. Without an ``[access]`` table, the server machine's local
Administrators group are the admins and nobody else gets in.

This module holds the decision, the signed sign-in cookie and the activity log. The Windows side
(Negotiate sign-in and group checks) is in :mod:`.serve`.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

DEFAULT_ADMINS = ("BUILTIN\\Administrators",)
ADMIN, USER = "admin", "user"


@dataclass
class AccessSettings:
    admins: list[str] = field(default_factory=lambda: list(DEFAULT_ADMINS))
    users: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "AccessSettings":
        def names(key, default):
            v = d.get(key, default)
            if isinstance(v, str):
                v = split_names(v)
            return [str(x).strip() for x in v if str(x).strip()]
        return cls(admins=names("admins", list(DEFAULT_ADMINS)), users=names("users", []))

    def to_dict(self) -> dict:
        return {"admins": list(self.admins), "users": list(self.users)}


def split_names(text: str) -> list[str]:
    """Names from text with one per line, or separated by commas or semicolons."""
    out = []
    for line in text.replace(";", "\n").replace(",", "\n").splitlines():
        line = line.strip()
        if line and line.lower() not in (x.lower() for x in out):
            out.append(line)
    return out


@dataclass(frozen=True)
class Grant:
    user: str      # DOMAIN\name as Windows reports it
    role: str      # ADMIN or USER
    via: str       # the list entry that let them in


def decide(user: str, access: AccessSettings, is_member: Callable[[str], bool]) -> Grant | None:
    """What ``user`` may do. ``is_member(name)`` says whether they are in the Windows group (or are the account) ``name``.

    The admin list is checked first, then the user list. Entries naming the user themselves match without
    asking Windows; any other entry is passed to ``is_member``, and one that can't be checked counts as no.
    """
    for role, names in ((ADMIN, access.admins), (USER, access.users)):
        for name in names:
            if name.lower() == user.lower():
                return Grant(user, role, name)
        for name in names:
            try:
                if is_member(name):
                    return Grant(user, role, name)
            except Exception:
                continue
    return None


# --- the sign-in cookie --------------------------------------------------------

COOKIE = "pict_signin"
COOKIE_HOURS = 8.0


class CookieSigner:
    """Signs and checks the sign-in cookie. The key is made when the server starts, so a restart signs everyone out
    (browsers then sign in again without asking, on a domain machine)."""

    def __init__(self, key: bytes | None = None, hours: float = COOKIE_HOURS):
        self.key = key or secrets.token_bytes(32)
        self.seconds = hours * 3600

    def _mac(self, body: bytes) -> str:
        return base64.urlsafe_b64encode(hmac.new(self.key, body, hashlib.sha256).digest()).decode().rstrip("=")

    def make(self, grant: Grant, now: float | None = None) -> str:
        expires = int((now or time.time()) + self.seconds)
        body = "\x1f".join([grant.user, grant.role, grant.via, str(expires)]).encode()
        return base64.urlsafe_b64encode(body).decode().rstrip("=") + "." + self._mac(body)

    def read(self, value: str, now: float | None = None) -> Grant | None:
        try:
            b64, mac = value.split(".", 1)
            body = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        except (ValueError, TypeError):
            return None
        if not hmac.compare_digest(mac, self._mac(body)):
            return None
        try:
            user, role, via, expires = body.decode().split("\x1f")
            if int(expires) < (now or time.time()) or role not in (ADMIN, USER):
                return None
        except ValueError:
            return None
        return Grant(user, role, via)


# --- activity log --------------------------------------------------------------

LOG_NAME = "pict-activity.log"
_log_lock = threading.Lock()


def activity_path() -> Path:
    from .settings import settings_path
    return settings_path().parent / LOG_NAME


def log_activity(user: str, event: str, detail: str = "", path: Path | None = None) -> None:
    """Add a line to the activity log: who signed in or was turned away, and who changed the settings."""
    path = path or activity_path()
    line = "\t".join([datetime.now().astimezone().isoformat(timespec="seconds"), user or "-", event,
                      detail.replace("\t", " ").replace("\n", " ")])
    try:
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except OSError:
        pass  # the log must never stop the tuner


def read_activity(limit: int = 200, path: Path | None = None) -> list[tuple[str, str, str, str]]:
    """The newest ``limit`` log lines, newest first, as (time, user, event, detail)."""
    path = path or activity_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        parts = (line.split("\t") + ["", "", "", ""])[:4]
        out.append(tuple(parts))
    return out
