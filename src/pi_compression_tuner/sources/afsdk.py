"""Read-only AF SDK source, used through pythonnet on a Windows machine with PI AF Client installed.

Only reads are made: point lookup, attribute reads, recorded values, event-count
summaries and a snapshot data-pipe signup. Nothing edits points or writes values.
"""

from __future__ import annotations

import os
import sys
import time

import pandas as pd

from ..models import PointConfig, Series
from .base import PointRef
from .piwebapi import config_from_attributes

DEFAULT_ASSEMBLY_DIRS = (
    r"C:\Program Files (x86)\PIPC\AF\PublicAssemblies\4.0",
    r"C:\Program Files\PIPC\AF\PublicAssemblies\4.0",
)
_ATTRS = ("excdev", "excmin", "excmax", "compdev", "compmin", "compmax",
          "compressing", "step", "zero", "span", "pointtype", "descriptor", "engunits", "ptclassname", "pointsource")


def load_afsdk(assembly_dir: str | None = None):
    """Load OSIsoft.AFSDK through pythonnet and return the namespaces this module needs.

    Raises ImportError with a readable reason when pythonnet or the AF SDK is missing.
    """
    try:
        import clr  # pythonnet
    except ImportError as e:
        raise ImportError("AF SDK needs pythonnet: pip install 'pi-compression-tuner[afsdk]'") from e
    dirs = [assembly_dir] if assembly_dir else [d for d in (os.environ.get("PI_AFSDK_DIR"), *DEFAULT_ASSEMBLY_DIRS) if d]
    for d in dirs:
        if d and os.path.isdir(d) and d not in sys.path:
            sys.path.append(d)
    try:
        clr.AddReference("OSIsoft.AFSDK")
    except Exception as e:
        raise ImportError("OSIsoft.AFSDK was not found. Install PI AF Client or set PI_AFSDK_DIR.") from e
    import OSIsoft.AF.Data as Data
    import OSIsoft.AF.PI as PI
    import OSIsoft.AF.Time as Time
    import System
    from System.Collections.Generic import List
    return {"PI": PI, "Data": Data, "Time": Time, "System": System, "List": List}


def _ts(af_value) -> str:
    return af_value.Timestamp.UtcTime.ToString("o")


def _num(af_value):
    """The value as a float, or None for bad values, digital states and other non-numeric values."""
    if not af_value.IsGood:
        return None
    v = af_value.Value
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    # pythonnet can hand back a boxed .NET number (System.Single, Int32, ...) that float() refuses;
    # AFValue.ValueAsDouble converts those. Enumeration values (digital states) are not numbers.
    try:
        if "Enumeration" in str(v.GetType().Name):
            return None
        return float(af_value.ValueAsDouble())
    except Exception:
        return None


def _series(af_values) -> Series:
    ts, vs = [], []
    for av in af_values:
        v = _num(av)
        if v is not None:
            ts.append(_ts(av))
            vs.append(v)
    if not ts:
        return Series([], [])
    return Series.from_pandas(pd.Series(vs, index=pd.to_datetime(ts, utc=True, format="ISO8601")))


class AFSDK:
    """AF SDK client with the same methods as :class:`~.piwebapi.PIWebAPI`."""

    def __init__(self, assembly_dir: str | None = None, sdk=None):
        self.sdk = sdk or load_afsdk(assembly_dir)
        self._servers = {}
        self._points = {}

    def _server(self, name: str):
        if name not in self._servers:
            srv = self.sdk["PI"].PIServers()[name]
            if srv is None:
                raise LookupError(f"PI Data Archive {name!r} is not in the known servers table")
            srv.Connect()
            self._servers[name] = srv
        return self._servers[name]

    def _pt(self, ref: PointRef):
        return self._points[ref.path]

    def _ref(self, server: str, pt, descriptor: str = "", engunits: str = "", pointtype: str = "",
             pointclass: str = "") -> PointRef:
        ref = PointRef(pt.Name, "", f"\\\\{server}\\{pt.Name}", descriptor, engunits, pointtype, pointclass)
        self._points[ref.path] = pt
        return ref

    def check(self, server: str) -> None:
        self._server(server)

    def find_points(self, server: str, name_filter: str = "*", max_count: int = 1000) -> list[PointRef]:
        PIPoint = self.sdk["PI"].PIPoint
        names = ["descriptor", "engunits", "pointtype", "ptclassname"]
        load = None
        if "List" in self.sdk:
            load = self.sdk["List"][self.sdk["System"].String]()
            for a in names:
                load.Add(a)
        out = []
        for pt in PIPoint.FindPIPoints(self._server(server), name_filter, None, load):
            out.append(self._ref(server, pt, *(str(pt.GetAttribute(a) or "") for a in names)))
            if len(out) >= max_count:
                break
        return out

    def point(self, server: str, tag: str) -> PointRef:
        return self._ref(server, self.sdk["PI"].PIPoint.FindPIPoint(self._server(server), tag))

    def attributes(self, ref: PointRef) -> dict:
        pt = self._pt(ref)
        pt.LoadAttributes(list(_ATTRS))
        out = {}
        for a in _ATTRS:
            v = pt.GetAttribute(a)
            out[a] = v if v is None or isinstance(v, (bool, int, float, str)) else str(v)
        return out

    def config(self, ref: PointRef) -> PointConfig:
        return config_from_attributes(self.attributes(ref))

    def recorded(self, ref: PointRef, start: str, end: str, page_size: int = 150000) -> Series:
        Time, Data = self.sdk["Time"], self.sdk["Data"]
        pt = self._pt(ref)
        frames, cursor = [], start
        while True:
            vals = list(pt.RecordedValues(Time.AFTimeRange(cursor, end), Data.AFBoundaryType.Inside, None, False, page_size))
            if frames and vals:
                vals = vals[1:]  # the first value repeats the previous page's last event
            frames.extend(vals)
            if len(vals) < page_size - 1:
                break
            cursor = _ts(vals[-1])
        return _series(frames)

    def event_count(self, ref: PointRef, start: str, end: str) -> int:
        Time, Data = self.sdk["Time"], self.sdk["Data"]
        res = self._pt(ref).Summary(Time.AFTimeRange(start, end), Data.AFSummaryTypes.Count,
                                    Data.AFCalculationBasis.EventWeighted, Data.AFTimestampCalculation.Auto)
        v = res[Data.AFSummaryTypes.Count]
        return int(float(v.Value)) if v.IsGood else 0

    def record_snapshots(self, refs: list[PointRef], duration_s: float, poll_s: float = 5.0, on_poll=None) -> dict[str, Series]:
        """Record every snapshot event through a PIDataPipe (snapshot pipe). Point settings are not changed."""
        PI, Data, List = self.sdk["PI"], self.sdk["Data"], self.sdk["List"]
        pipe = PI.PIDataPipe(Data.AFDataPipeType.Snapshot)
        pts = List[PI.PIPoint]()
        for r in refs:
            pts.Add(self._pt(r))
        pipe.AddSignups(pts)
        events: dict[str, list] = {r.name: [] for r in refs}
        deadline = time.monotonic() + duration_s
        batch = 10000
        try:
            while True:
                while True:  # drain the pipe: a full batch means more events are waiting
                    res = list(pipe.GetUpdateEvents(batch))
                    for ev in res:
                        if str(ev.Action) in ("Add", "Update"):
                            events.setdefault(ev.Value.PIPoint.Name, []).append(ev.Value)
                    if len(res) < batch:
                        break
                if on_poll:
                    on_poll({k: len(v) for k, v in events.items()})
                if time.monotonic() >= deadline:
                    break
                time.sleep(poll_s)
        except KeyboardInterrupt:
            pass
        finally:
            pipe.Close()  # removes the signups, as in AVEVA's AF SDK course (Exercise 5)
            pipe.Dispose()
        return {k: _series(v) for k, v in events.items()}
