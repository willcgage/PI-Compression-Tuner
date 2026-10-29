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

    class BadSDK:
        def __init__(self, *a, **k):
            raise ImportError("OSIsoft.AFSDK was not found")

    monkeypatch.setattr(webapi, "PIWebAPI", GoodWeb)
    monkeypatch.setattr(afsdk, "AFSDK", BadSDK)
    res = CliRunner().invoke(main, ["pi", "check", "--server", "SRV", "--url", "https://pi/piwebapi", "--tag", "SINUSOID"])
    assert res.exit_code == 0, res.output
    assert "PI Web API: OK" in res.output and "SINUSOID" in res.output and "1440 archive events" in res.output
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
