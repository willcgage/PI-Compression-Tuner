import pandas as pd
from click.testing import CliRunner

from pi_compression_tuner.cli import main


def test_demo_then_analyze(tmp_path):
    runner = CliRunner()
    res = runner.invoke(main, ["demo", str(tmp_path / "d"), "--days", "1"])
    assert res.exit_code == 0, res.output
    out = tmp_path / "out"
    res = runner.invoke(main, ["analyze", str(tmp_path / "d" / "data.csv"), "--config", str(tmp_path / "d" / "config.csv"),
                               "--mode", "raw", "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert (out / "report.html").read_text().startswith("<!doctype html>")
    r = pd.read_csv(out / "results.csv")
    assert len(r) == 4
    from pi_compression_tuner import Classification
    assert set(r["Classification"]) <= {c.value for c in Classification}


def test_pi_check_reports_each_source(monkeypatch):
    import pi_compression_tuner.sources.afsdk as afsdk
    import pi_compression_tuner.sources.piwebapi as webapi
    from pi_compression_tuner.sources.base import PointRef

    class GoodWeb:
        def __init__(self, *a, **k):
            pass

        def check(self, server):
            pass

        def find_points(self, server, name_filter="*", max_count=1000):
            return [PointRef("SINUSOID")]

        def point(self, server, tag):
            return PointRef(tag)

        def attributes(self, ref):
            return {"compdev": 0.5, "excdev": 0.1}

        def event_count(self, ref, start, end):
            return 1440

        def recorded(self, ref, start, end):
            return [0.0] * 1439

    class BadSDK:
        def __init__(self, *a, **k):
            raise ImportError("OSIsoft.AFSDK was not found")

    monkeypatch.setattr(webapi, "PIWebAPI", GoodWeb)
    monkeypatch.setattr(afsdk, "AFSDK", BadSDK)
    res = CliRunner().invoke(main, ["pi", "check", "--server", "SRV", "--url", "https://pi/piwebapi", "--tag", "SINUSOID"])
    assert res.exit_code == 0, res.output
    assert "PI Web API: OK" in res.output and "SINUSOID" in res.output and "1440 archive events in the last day, 1439 numeric values read" in res.output
    assert "AF SDK: failed (ImportError" in res.output


def test_pi_check_fails_when_nothing_works(monkeypatch):
    import pi_compression_tuner.sources.afsdk as afsdk

    class BadSDK:
        def __init__(self, *a, **k):
            raise ImportError("no pythonnet")

    monkeypatch.setattr(afsdk, "AFSDK", BadSDK)
    res = CliRunner().invoke(main, ["pi", "check", "--server", "SRV"])
    assert res.exit_code == 1
    assert "skipped, no --url" in res.output


def test_pi_config_saves_settings_that_later_commands_use(monkeypatch):
    import pi_compression_tuner.sources.afsdk as afsdk
    import pi_compression_tuner.sources.piwebapi as webapi

    seen = {}

    class Web:
        def __init__(self, url, auth, verify):
            seen.update(url=url, auth=auth, verify=verify)

        def check(self, server):
            seen["server"] = server

        def find_points(self, server, name_filter="*", max_count=1000):
            return []

    class NoSDK:
        def __init__(self, *a, **k):
            raise ImportError("none")

    monkeypatch.setattr(webapi, "PIWebAPI", Web)
    monkeypatch.setattr(afsdk, "AFSDK", NoSDK)
    runner = CliRunner()
    res = runner.invoke(main, ["pi", "config", "--server", "PISRV", "--url", "https://pi/piwebapi", "--auth", "anonymous"])
    assert res.exit_code == 0 and "Saved" in res.output, res.output
    res = runner.invoke(main, ["pi", "check"])
    assert res.exit_code == 0, res.output
    assert seen == {"url": "https://pi/piwebapi", "auth": "anonymous", "verify": True, "server": "PISRV"}
    res = runner.invoke(main, ["pi", "check", "--insecure"])
    assert seen["verify"] is False
    assert "anonymous" in runner.invoke(main, ["pi", "config"]).output


def test_pi_commands_need_a_server():
    res = CliRunner().invoke(main, ["pi", "check"])
    assert res.exit_code == 2 and "pict pi config --server" in res.output


def test_duplicates_command(tmp_path):
    import numpy as np
    t = pd.date_range("2026-01-01", periods=500, freq="1min", tz="UTC")
    v = np.sin(np.arange(500) / 30)
    other = np.cos(np.arange(500) / 7) + np.arange(500) / 100
    df = pd.concat([pd.DataFrame({"Tag": tag, "Timestamp": t, "Value": vals})
                    for tag, vals in [("tag_123.value", v), ("tag_123.scaled", v), ("other", other)]])
    df.to_csv(tmp_path / "data.csv", index=False)
    res = CliRunner().invoke(main, ["duplicates", str(tmp_path / "data.csv"), "--out", str(tmp_path / "dup.csv")])
    assert res.exit_code == 0, res.output
    assert "Exact copy" in res.output and "1 group," in res.output
    got = pd.read_csv(tmp_path / "dup.csv")
    assert set(got[["Point A", "Point B"]].iloc[0]) == {"tag_123.value", "tag_123.scaled"}


def test_pi_record_saves_each_poll_so_a_crash_keeps_what_was_recorded(tmp_path, monkeypatch):
    import numpy as np

    import pi_compression_tuner.cli as cli
    from pi_compression_tuner.models import Series
    from pi_compression_tuner.sources.base import PointRef

    class Recorder:
        def point(self, server, tag):
            return PointRef(tag)

        def attributes(self, ref):
            return {"compdev": 0.5}

        def record_snapshots(self, refs, duration_s, poll_s=5.0, on_poll=None, on_batch=None, stop=None):
            t0 = 1767225600.0
            on_batch({"A": Series(np.array([t0, t0 + 1]), np.array([1.0, 2.0]))})
            on_batch({"A": Series(np.array([t0 + 2]), np.array([3.0])), "B": Series(np.array([t0]), np.array([7.0]))})
            # What the old version died of: an unhandled exception after hours of recording.
            raise RuntimeError("Python.Runtime.PythonException")

    monkeypatch.setattr(cli, "_connect", lambda *a: Recorder())
    out = tmp_path / "capture"
    res = CliRunner().invoke(main, ["pi", "record", "--server", "SRV", "--tag", "A", "--tag", "B", "--out", str(out)])
    assert isinstance(res.exception, RuntimeError)
    assert "Saved 4 events" in res.output
    from pi_compression_tuner.sources.csvfiles import read_series

    got = read_series(out / "data.csv")
    assert got["A"].v.tolist() == [1.0, 2.0, 3.0] and got["B"].v.tolist() == [7.0]
    assert got["A"].t[0] == 1767225600.0


def test_first_ctrl_c_while_recording_asks_to_stop_and_the_second_quits():
    import os
    import signal

    import pytest

    from pi_compression_tuner.cli import _ctrl_c_stops

    before = signal.getsignal(signal.SIGINT)
    with _ctrl_c_stops() as stopping:
        assert not stopping()
        os.kill(os.getpid(), signal.SIGINT)  # no KeyboardInterrupt: nothing can escape through .NET
        assert stopping()
        with pytest.raises(KeyboardInterrupt):
            os.kill(os.getpid(), signal.SIGINT)
    assert signal.getsignal(signal.SIGINT) is before
