"""Read-only PI Web API client.

Only GET requests are made, plus one POST that registers a stream-updates marker
(which creates a server-side subscription but changes no PI data or settings).
Nothing here edits points or writes values.
"""

from __future__ import annotations

import os
import time
from urllib.parse import quote

import pandas as pd
import requests

from ..models import PointConfig, Series
from .base import PointRef

CONFIG_ATTRIBUTES = (
    "excdev", "excmin", "excmax", "compdev", "compmin", "compmax",
    "compressing", "step", "zero", "span", "pointtype", "descriptor", "engunits",
)


class PIWebAPI:
    """Minimal PI Web API client. ``PointRef.web_id`` holds the point's WebId.

    ``auth`` is ``"kerberos"`` (Windows integrated / Negotiate), ``"basic"`` (reads
    ``PI_WEBAPI_USER`` and ``PI_WEBAPI_PASSWORD`` from the environment) or ``"anonymous"``.
    ``verify`` is passed to requests: True, False, or a CA bundle path. True checks against the
    operating system's trust store.
    """

    def __init__(self, base_url: str, auth: str = "kerberos", verify: bool | str = True, timeout: float = 60.0,
                 session: requests.Session | None = None):
        self.base = base_url.rstrip("/")
        self.timeout = timeout
        self.s = session or requests.Session()
        self.s.verify = verify
        if verify is True and session is None:
            _use_system_trust()
        self.s.headers.update({"Accept": "application/json", "X-Requested-With": "pi-compression-tuner"})
        if session is None:
            self.s.auth = _make_auth(auth)

    # -- low level -------------------------------------------------------
    def _get(self, endpoint: str, /, **params) -> dict:
        r = self.s.get(f"{self.base}/{endpoint.lstrip('/')}", params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def check(self, server: str) -> None:
        """Raise if the Web API or the Data Archive can't be reached."""
        self.data_server_webid(server)

    # -- lookup ----------------------------------------------------------
    def data_server_webid(self, server: str) -> str:
        return self._get("dataservers", path=f"\\\\{server}")["WebId"]

    def find_points(self, server: str, name_filter: str = "*", max_count: int = 1000) -> list[PointRef]:
        ds = self.data_server_webid(server)
        out: list[PointRef] = []
        start = 0
        page = min(max_count, 1000)
        while len(out) < max_count:
            items = self._get(f"dataservers/{ds}/points", nameFilter=name_filter, startIndex=start, maxCount=page,
                              selectedFields="Items.Name;Items.WebId;Items.Path;Items.Descriptor;Items.EngineeringUnits"
                              ).get("Items", [])
            out += [PointRef(i["Name"], i["WebId"], i.get("Path", ""), i.get("Descriptor") or "",
                             i.get("EngineeringUnits") or "") for i in items]
            if len(items) < page:
                break
            start += page
        return out[:max_count]

    def point(self, server: str, tag: str) -> PointRef:
        p = self._get("points", path=f"\\\\{server}\\{tag}", selectedFields="Name;WebId;Path")
        return PointRef(p["Name"], p["WebId"], p.get("Path", ""))

    # -- attributes ------------------------------------------------------
    def attributes(self, ref: PointRef) -> dict:
        items = self._get(f"points/{ref.web_id}/attributes").get("Items", [])
        return {i["Name"].lower(): i.get("Value") for i in items}

    def config(self, ref: PointRef) -> PointConfig:
        return config_from_attributes(self.attributes(ref))

    # -- data ------------------------------------------------------------
    def recorded(self, ref: PointRef, start: str, end: str, page_size: int = 150000) -> Series:
        """Archived events between ``start`` and ``end`` (PI time strings such as ``*-7d``)."""
        frames = []
        cursor = start
        while True:
            items = self._get(f"streams/{ref.web_id}/recorded", startTime=cursor, endTime=end, maxCount=page_size,
                              boundaryType="Inside", selectedFields="Items.Timestamp;Items.Value;Items.Good").get("Items", [])
            if frames and items:
                items = items[1:]  # the first item repeats the previous page's last event
            frames.extend(items)
            if len(items) < page_size - 1:
                break
            cursor = items[-1]["Timestamp"]
        return series_from_items(frames)

    def event_count(self, ref: PointRef, start: str, end: str) -> int:
        items = self._get(f"streams/{ref.web_id}/summary", startTime=start, endTime=end, summaryType="Count",
                          calculationBasis="EventWeighted").get("Items", [])
        for i in items:
            if i.get("Type") == "Count":
                return int(i["Value"]["Value"])
        return 0

    def record_snapshots(self, refs: list[PointRef], duration_s: float, poll_s: float = 5.0,
                         on_poll=None) -> dict[str, Series]:
        """Record every snapshot event for the points over ``duration_s`` seconds.

        Uses PI Web API stream updates: one marker per point, polled every ``poll_s``.
        The snapshot stream is after exception and before compression, which is the
        signal compression should be tuned against. Nothing about the points is changed.
        """
        markers = {}
        for ref in refs:
            r = self.s.post(f"{self.base}/streams/{ref.web_id}/updates", timeout=self.timeout)
            r.raise_for_status()
            markers[ref.name] = r.json()["LatestMarker"]
        events: dict[str, list] = {ref.name: [] for ref in refs}
        deadline = time.monotonic() + duration_s
        try:
            while True:
                for name, marker in list(markers.items()):
                    body = self._get(f"streams/updates/{quote(marker, safe='')}")
                    events[name].extend(e for e in body.get("Events", []) if e.get("Action", "Add") in ("Add", "Update"))
                    markers[name] = body.get("LatestMarker", marker)
                if on_poll:
                    on_poll({k: len(v) for k, v in events.items()})
                if time.monotonic() >= deadline:
                    break
                time.sleep(poll_s)
        except KeyboardInterrupt:
            pass  # stop early and keep what was recorded
        return {name: series_from_items(items) for name, items in events.items()}


def _use_system_trust() -> None:
    """Check certificates against the operating system's trust store (on Windows, the one the
    browser uses) instead of only certifi's public list, so a site's internal CA is accepted."""
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()


def _make_auth(kind: str):
    if kind == "anonymous":
        return None
    if kind == "basic":
        user, pw = os.environ.get("PI_WEBAPI_USER"), os.environ.get("PI_WEBAPI_PASSWORD")
        if not user or pw is None:
            raise RuntimeError("Basic auth needs PI_WEBAPI_USER and PI_WEBAPI_PASSWORD in the environment.")
        return (user, pw)
    if kind == "kerberos":
        try:
            from requests_negotiate_sspi import HttpNegotiateAuth  # Windows
            return HttpNegotiateAuth()
        except ImportError:
            pass
        try:
            from requests_kerberos import HTTPKerberosAuth, OPTIONAL
            return HTTPKerberosAuth(mutual_authentication=OPTIONAL)
        except ImportError:
            raise RuntimeError("Kerberos auth needs the optional dependency: pip install 'pi-compression-tuner[kerberos]'")
    raise ValueError(f"unknown auth kind: {kind}")


def series_from_items(items: list[dict]) -> Series:
    """Convert PI Web API value items to a Series. Bad-quality and digital-state values are dropped."""
    ts, vs = [], []
    for i in items:
        val = i.get("Value")
        if i.get("Good") is False or isinstance(val, dict) or isinstance(val, bool):
            continue
        try:
            vs.append(float(val))
        except (TypeError, ValueError):
            continue
        ts.append(i["Timestamp"])
    if not ts:
        return Series([], [])
    return Series.from_pandas(pd.Series(vs, index=pd.to_datetime(ts, utc=True, format="ISO8601")))


def config_from_attributes(attrs: dict) -> PointConfig:
    d = PointConfig()

    def f(name, default):
        v = attrs.get(name)
        try:
            return float(v) if v is not None else default
        except (TypeError, ValueError):
            return default

    return PointConfig(
        excdev=f("excdev", d.excdev), excmin=f("excmin", d.excmin), excmax=f("excmax", d.excmax),
        compdev=f("compdev", d.compdev), compmin=f("compmin", d.compmin), compmax=f("compmax", d.compmax),
        compressing=bool(f("compressing", 1)), step=bool(f("step", 0)),
        zero=f("zero", d.zero), span=f("span", d.span),
    )
