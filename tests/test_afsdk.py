"""AF SDK adapter against a fake of the few AF SDK types it touches (pythonnet isn't available here)."""

from datetime import datetime, timezone
from types import SimpleNamespace as NS

from pi_compression_tuner.sources.afsdk import AFSDK


class FakeTime:
    def __init__(self, dt):
        self.UtcTime = NS(ToString=lambda fmt: dt.isoformat())


class FakeValue:
    def __init__(self, sec, value, good=True):
        self.Timestamp = FakeTime(datetime.fromtimestamp(sec, timezone.utc))
        self.Value = value
        self.IsGood = good


class FakePoint:
    Name = "TAG1"

    def __init__(self, values):
        self.values = values
        self.loaded = None

    def LoadAttributes(self, names):
        self.loaded = names

    def GetAttribute(self, name):
        return {"compdev": 0.5, "excdev": 0.25, "span": 200.0, "compressing": 1, "step": 0}.get(name)

    def RecordedValues(self, time_range, boundary, filt, include_filtered, max_count):
        start = time_range.start
        i0 = 0 if start == "*-1d" else next(i for i, v in enumerate(self.values) if v.Timestamp.UtcTime.ToString("o") == start)
        return self.values[i0:i0 + max_count]


class FakeServer:
    def Connect(self):
        pass


def _sdk(point):
    PIPoint = NS(FindPIPoint=lambda srv, tag: point, FindPIPoints=lambda srv, f, a, b: [point])
    return {
        "PI": NS(PIServers=lambda: {"SRV": FakeServer()}, PIPoint=PIPoint),
        "Time": NS(AFTimeRange=lambda s, e: NS(start=s, end=e)),
        "Data": NS(AFBoundaryType=NS(Inside="Inside")),
    }


def test_point_attributes_and_paged_recorded_values():
    vals = [FakeValue(1767225600 + i, float(i)) for i in range(9)]
    vals.insert(4, FakeValue(1767225600 + 3.5, "Shutdown"))  # digital state is dropped
    pt = FakePoint(vals)
    sdk = AFSDK(sdk=_sdk(pt))
    sdk.check("SRV")
    ref = sdk.point("SRV", "TAG1")
    cfg = sdk.config(ref)
    assert (cfg.compdev, cfg.excdev, cfg.span, cfg.compressing) == (0.5, 0.25, 200.0, True)
    s = sdk.recorded(ref, "*-1d", "*", page_size=3)
    assert s.v.tolist() == [float(i) for i in range(9)]
    assert [r.name for r in sdk.find_points("SRV", "*")] == ["TAG1"]


class NetList(list):
    """Stands in for System.Collections.Generic.List: NetList[T]() and .Add."""

    Add = list.append


def test_snapshot_pipe_drains_full_batches_and_closes():
    pt = FakePoint([])
    pt.Name = "TAG1"
    evs = []
    for i in range(25000):
        v = FakeValue(1767225600 + i, float(i))
        v.PIPoint = pt
        evs.append(NS(Action="Add", Value=v))
    calls = []

    class Pipe:
        def __init__(self, kind):
            pass

        def AddSignups(self, pts):
            pass

        def GetUpdateEvents(self, n):
            calls.append(n)
            out, evs[:] = evs[:n], evs[n:]
            return out

        def Close(self):
            calls.append("close")

        def Dispose(self):
            calls.append("dispose")

    sdk = _sdk(pt)
    sdk["PI"].PIDataPipe = Pipe
    sdk["Data"].AFDataPipeType = NS(Snapshot="Snapshot")
    sdk["List"] = NetList
    src = AFSDK(sdk=sdk)
    ref = src.point("SRV", "TAG1")
    out = src.record_snapshots([ref], duration_s=0, poll_s=0)
    assert len(out["TAG1"]) == 25000
    assert calls == [10000, 10000, 10000, "close", "dispose"]


def test_boxed_dotnet_numbers_are_read_and_digital_states_dropped():
    from pi_compression_tuner.sources.afsdk import _num

    class Boxed:  # a .NET System.Single that float() refuses, as pythonnet can return
        def GetType(self):
            return NS(Name="Single")

        def __float__(self):
            raise TypeError("float() argument must be a string or a real number")

    class State:
        def GetType(self):
            return NS(Name="AFEnumerationValue")

    def val(v, good=True, as_double=None):
        return NS(IsGood=good, Value=v, ValueAsDouble=lambda: as_double)

    assert _num(val(Boxed(), as_double=12.5)) == 12.5
    assert _num(val(3)) == 3.0
    assert _num(val(State(), as_double=248.0)) is None
    assert _num(val(1.0, good=False)) is None
    assert _num(val(True)) is None


def test_find_points_passes_point_source_and_filters_the_rest():
    from pi_compression_tuner.sources.base import PointFilter

    class Pt:
        def __init__(self, name, desc, units):
            self.Name, self.a = name, {"descriptor": desc, "engunits": units, "pointtype": "Float32"}

        def GetAttribute(self, a):
            return self.a.get(a)

    pts = [Pt("A", "Feed flow", "gpm"), Pt("B", "Tank level", "%"), Pt("C", "Steam flow", "klb/h")]
    seen = {}

    def find(srv, name, source, load):
        seen.update(name=name, source=source)
        return iter(pts)

    sdk = _sdk(None)
    sdk["PI"].PIPoint.FindPIPoints = find
    src = AFSDK(sdk=sdk)
    got = src.find_points("SRV", "", 10, where=PointFilter(pointsource="OPC*", descriptor="flow"))
    assert [r.name for r in got] == ["A", "C"] and seen == {"name": "*", "source": "OPC*"}
    assert got[0].engunits == "gpm" and src.last_search["scanned"] == 3
    assert [r.name for r in src.find_points("SRV", "*", 10)] == ["A", "B", "C"] and seen["source"] is None


def test_snapshot_pipe_hands_each_poll_over_and_stops_when_asked():
    pt = FakePoint([])
    pt.Name = "TAG1"
    polls = [[1, 2, 3], [4, 5], []]
    closed = []

    class Pipe:
        def __init__(self, kind):
            pass

        def AddSignups(self, pts):
            pass

        def GetUpdateEvents(self, n):
            out = []
            for i in polls.pop(0) if polls else []:
                v = FakeValue(1767225600 + i, float(i))
                v.PIPoint = pt
                out.append(NS(Action="Add", Value=v))
            return out

        def Close(self):
            closed.append("close")

        def Dispose(self):
            closed.append("dispose")

    sdk = _sdk(pt)
    sdk["PI"].PIDataPipe = Pipe
    sdk["Data"].AFDataPipeType = NS(Snapshot="Snapshot")
    sdk["List"] = NetList
    src = AFSDK(sdk=sdk)
    ref = src.point("SRV", "TAG1")
    batches = []
    # A day-long recording that a Ctrl+C (the stop flag) ends after the second poll.
    out = src.record_snapshots([ref], duration_s=86400, poll_s=0, on_batch=lambda b: batches.append(b["TAG1"].v.tolist()),
                               stop=lambda: len(batches) >= 2)
    assert batches == [[1.0, 2.0, 3.0], [4.0, 5.0]]
    assert out["TAG1"].v.tolist() == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert closed == ["close", "dispose"]


def test_attributes_missing_from_the_point_class_come_back_blank():
    class KeyNotFound(Exception):
        pass

    class BasePoint(FakePoint):
        def GetAttribute(self, name):
            if name == "instrumenttag":
                raise KeyNotFound("The PI Point attribute 'instrumenttag' was not found.")
            return super().GetAttribute(name)

    sdk = AFSDK(sdk=_sdk(BasePoint([])))
    ref = sdk.point("SRV", "TAG1")
    a = sdk.attributes(ref)
    assert a["instrumenttag"] is None and a["compdev"] == 0.5
    assert sdk.config(ref).compdev == 0.5


def test_attributes_load_one_by_one_when_the_bulk_load_fails():
    class Picky(FakePoint):
        def LoadAttributes(self, names):
            if len(names) > 1:
                raise ValueError("bad name in list")
            self.loaded = (self.loaded or []) + list(names)

    pt = Picky([])
    sdk = AFSDK(sdk=_sdk(pt))
    assert sdk.attributes(sdk.point("SRV", "TAG1"))["excdev"] == 0.25
    assert "instrumenttag" in pt.loaded and len(pt.loaded) > 1
