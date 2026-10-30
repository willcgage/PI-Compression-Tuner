import numpy as np

from pi_compression_tuner.models import PointConfig, Series
from pi_compression_tuner.templates import (Template, match, norm_units, point_info, starter_templates,
                                            tune_with_template)
from pi_compression_tuner.tune import TuneOptions


def info(tag="T1", descriptor="", engunits="", pointtype="Float32", **kw):
    return point_info(tag, {"descriptor": descriptor, "engunits": engunits, "pointtype": pointtype, **kw})


def test_units_are_compared_loosely():
    assert norm_units("°F") == norm_units("degF") == norm_units("deg F") == norm_units("F")
    assert norm_units("psig") != norm_units("psia")


def test_rules_and_within_or_across():
    t = Template("Flow", match_descriptor=["flow"], match_units=["gpm", "lb/h"])
    assert t.matches(info(descriptor="Feed Flow", engunits="GPM"))
    assert not t.matches(info(descriptor="Feed Flow", engunits="psig"))
    assert not t.matches(info(descriptor="Overflow alarm", engunits="gpm"))  # whole words only
    assert Template("Any", match_descriptor=["*flow*"]).matches(info(descriptor="Overflow alarm"))
    assert not Template("Empty").matches(info(descriptor="flow"))  # no rules match nothing


def test_priority_and_enabled():
    lo = Template("Specific", priority=1, match_units=["degF"])
    hi = Template("General", priority=50, match_pointtype=["float*"])
    assert match([hi, lo], info(engunits="degF")).name == "Specific"
    lo.enabled = False
    assert match([hi, lo], info(engunits="degF")).name == "General"
    assert match([hi, lo], info(pointtype="Digital")) is None


def test_starter_templates_cover_common_points():
    ts = starter_templates()
    cases = {
        ("Reactor temperature", "degF", "Float32"): "Temperature, °F",
        ("Steam header pressure", "psig", "Float32"): "Pressure, psi",
        ("Feed flow", "kg/h", "Float32"): "Flow",
        ("Tank 3 level", "%", "Float32"): "Level",
        ("FCV-101 valve position", "%", "Float32"): "Valve and controller output",
        ("Pump run status", "", "Digital"): "Digital and status",
        ("Lab sample result, sulfur", "ppm", "Float32"): "Lab and manual entries",
        ("Outlet sulfur", "ppm", "Float32"): "Analyzer and composition",
        ("Feeder 2 load", "MW", "Float32"): "Electrical power",
    }
    for (desc, units, ptype), want in cases.items():
        got = match(ts, info(descriptor=desc, engunits=units, pointtype=ptype))
        assert got and got.name == want, (desc, got and got.name)
    assert match(ts, info(tag="FIC101.SP", descriptor="Feed flow")).name == "Setpoints and operator inputs"
    assert match(ts, info(descriptor="Something else", engunits="widgets")) is None


def test_baseline_uses_units_or_percent_of_span_and_half_excdev():
    cur = PointConfig(compdev=0.1, excdev=0.05, span=200)
    b = Template("x", match_units=["a"], compdev_pct=1, compmax=3600).baseline(cur)
    assert (b.compdev, b.excdev, b.compmax) == (2.0, 1.0, 3600)
    b = Template("x", match_units=["a"], compdev=0.5, excdev=0.1).baseline(cur)
    assert (b.compdev, b.excdev, b.compmax) == (0.5, 0.1, cur.compmax)
    assert Template("x", match_units=["a"], compdev_pct=1).baseline(PointConfig(span=0)).compdev == 0  # no span: unchanged


def test_tune_with_template_applies_overrides_and_notes():
    rng = np.random.default_rng(0)
    t = np.arange(0, 86400, 10.0)
    s = Series(t, 60 + 5 * np.sin(2 * np.pi * t / 21600) + rng.normal(0, 0.05, len(t)))
    cur = PointConfig(compdev=0.01, span=200, compmax=3600)
    tmpl = Template("Temp", match_units=["degF"], compdev=0.5, compmax=28800, target_pct=99, step=False)
    r = tune_with_template("T", s, cur, TuneOptions(target=0.9), info(engunits="degF"), [tmpl])
    assert r.template == "Temp" and r.baseline.compdev == 0.5
    assert r.notes[0].startswith("Matched the “Temp” template")
    assert any("CompMax is 1 h; the template suggests 8 h" in n for n in r.notes)
    assert r.recommended is None or r.recommended.score.retention >= 0.99
    plain = tune_with_template("T", s, cur, TuneOptions(target=0.9), info(engunits="psig"), [tmpl])
    assert plain.template == "" and plain.baseline is None


def test_template_from_dict_accepts_text_lists():
    t = Template.from_dict({"name": "A", "match_units": "psig, psia", "unknown": 1})
    assert t.match_units == ["psig", "psia"]


def test_admin_tables_round_trip():
    import pytest
    pytest.importorskip("streamlit")
    from pi_compression_tuner.settings import PISettings
    from pi_compression_tuner.ui.admin import servers_frame, servers_from_frame, templates_frame, templates_from_frame
    ts = starter_templates()
    assert templates_from_frame(templates_frame(ts)) == ts
    servers = [PISettings(server="A", name="Plant", verify=False), PISettings(server="B", source="afsdk")]
    assert servers_from_frame(servers_frame(servers)) == servers
    df = templates_frame(ts[:1])
    df.loc[0, "compdev"] = -1
    with pytest.raises(ValueError, match="can't be negative"):
        templates_from_frame(df)
