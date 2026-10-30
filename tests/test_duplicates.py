import numpy as np

from pi_compression_tuner.duplicates import (DuplicateOptions, Match, base_name, describe, find_duplicates,
                                             groups_frame, name_hints, pairs_frame)
from pi_compression_tuner.models import Series


def _signal(n=3000, seed=0, days=2):
    rng = np.random.default_rng(seed)
    t = np.sort(rng.uniform(0, days * 86400, n))
    v = 50 + 10 * np.sin(t / 7000 + seed) + np.cumsum(rng.normal(0, 0.05, n))
    return t, v


def test_base_name_drops_copy_endings():
    assert base_name("tag_123.value") == base_name("TAG_123.Scaled") == "tag_123"
    assert base_name("FIC101.PV") == "fic101"
    assert base_name("FIC101.SP") == "fic101.sp"  # a setpoint is its own signal
    assert base_name("value") == "value"


def test_value_and_scaled_copy_is_an_exact_copy():
    t, v = _signal()
    t2, v2 = _signal(seed=5)
    series = {"tag_123.value": Series(t, v), "tag_123.scaled": Series(t, v.astype(np.float32)),
              "other": Series(t2, v2)}
    rep = find_duplicates(series)
    assert len(rep.groups) == 1
    g = rep.groups[0]
    assert g.match is Match.EXACT
    assert set(g.members) == {"tag_123.value", "tag_123.scaled"}
    assert g.redundant_per_day > 0
    assert "Same base name 'tag_123'" in g.pairs[0].hints
    assert "same events" in describe(g.pairs[0])


def test_same_signal_archived_differently():
    t, v = _signal(6000)
    keep = np.arange(0, len(t), 3)  # a thinned copy: fewer events, same values where it has them
    rep = find_duplicates({"A": Series(t, v), "B": Series(t[keep] + 2.0, np.interp(t[keep] + 2.0, t, v))})
    assert [g.match for g in rep.groups] == [Match.SAME]


def test_scaled_copy_reports_the_fit():
    t, v = _signal()
    rep = find_duplicates({"FT1.eu": Series(t, v), "FT1.raw": Series(t, (v - 20) * 81.92)})
    p = rep.groups[0].pairs[0]
    assert rep.groups[0].match is Match.SCALED
    a_to_b = p.slope if p.a == "FT1.eu" else 1 / p.slope
    assert abs(a_to_b - 81.92) < 1e-3


def test_related_but_different_signals_are_not_duplicates():
    t, v = _signal()
    rng = np.random.default_rng(9)
    noisy = v + rng.normal(0, 0.5, len(v))
    rep = find_duplicates({"TI1": Series(t, v), "TI2": Series(t, noisy)})
    assert rep.groups == [] and rep.loose == []
    loose = find_duplicates({"TI1": Series(t, v), "TI2": Series(t, noisy)},
                            opts=DuplicateOptions(include_loose=True, loose_r=0.99))
    assert [p.match for p in loose.loose] == [Match.LOOSE]


def test_three_copies_make_one_group_and_keep_the_plain_name():
    t, v = _signal()
    rep = find_duplicates({"P5.value": Series(t, v), "P5": Series(t, v), "P5.copy": Series(t, v)})
    assert len(rep.groups) == 1 and rep.groups[0].keep == "P5"
    assert rep.redundant_points == 2


def test_flat_lookalikes_are_name_only():
    t = np.linspace(0, 86400, 100)
    rep = find_duplicates({"V7.value": Series(t, np.ones(100)), "V7.scaled": Series(t, np.ones(100))})
    assert rep.groups == []
    assert [p.match for p in rep.name_only] == [Match.NAME]
    assert rep.skipped == {"V7.value": "not moving", "V7.scaled": "not moving"}


def test_name_hints_use_attributes():
    hints = name_hints("A1", "B2", {"instrumenttag": "40001", "pointsource": "OPC", "engunits": "psi"},
                       {"instrumenttag": "40001", "pointsource": "opc", "engunits": "kPa"})
    assert "Same instrument tag and point source (40001)" in hints
    assert "Units differ (psi and kPa)" in hints


def test_frames():
    t, v = _signal()
    rep = find_duplicates({"x.value": Series(t, v), "x.scaled": Series(t, v)})
    assert list(groups_frame(rep)["Finding"]) == ["Exact copy"]
    assert len(pairs_frame(rep)) == 1
