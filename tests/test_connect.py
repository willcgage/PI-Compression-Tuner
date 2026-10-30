import pytest

from pi_compression_tuner.sources import connect as connect_mod


class Boom(Exception):
    pass


def test_auto_falls_back_to_afsdk(monkeypatch):
    import pi_compression_tuner.sources.afsdk as afsdk
    import pi_compression_tuner.sources.piwebapi as webapi

    class BadWeb:
        def __init__(self, *a, **k):
            pass

        def check(self, server):
            raise Boom("401 Unauthorized")

    class GoodSDK:
        def __init__(self, *a, **k):
            pass

        def check(self, server):
            pass

    monkeypatch.setattr(webapi, "PIWebAPI", BadWeb)
    monkeypatch.setattr(afsdk, "AFSDK", GoodSDK)
    conn = connect_mod.connect("SRV", url="https://pi/piwebapi")
    assert conn.kind == "afsdk" and "401" in conn.note


def test_webapi_only_does_not_fall_back(monkeypatch):
    import pi_compression_tuner.sources.piwebapi as webapi

    class BadWeb:
        def __init__(self, *a, **k):
            pass

        def check(self, server):
            raise Boom("down")

    monkeypatch.setattr(webapi, "PIWebAPI", BadWeb)
    with pytest.raises(Boom):
        connect_mod.connect("SRV", source="webapi", url="https://pi/piwebapi")


def test_both_failing_reports_both_reasons(monkeypatch):
    import pi_compression_tuner.sources.piwebapi as webapi

    class BadWeb:
        def __init__(self, *a, **k):
            pass

        def check(self, server):
            raise Boom("timeout")

    monkeypatch.setattr(webapi, "PIWebAPI", BadWeb)
    # pythonnet / AF SDK are not installed here, so the fallback fails too.
    with pytest.raises(ConnectionError, match="timeout.*AF SDK fallback failed"):
        connect_mod.connect("SRV", url="https://pi/piwebapi")
