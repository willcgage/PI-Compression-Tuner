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
