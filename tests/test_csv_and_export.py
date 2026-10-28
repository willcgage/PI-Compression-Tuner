import pandas as pd

from pi_compression_tuner import PointConfig, Series, tune_point
from pi_compression_tuner.export import write_pibuilder_csv, write_results_csv
from pi_compression_tuner.sources.csvfiles import read_configs, read_series


def test_long_and_wide_data(tmp_path):
    long = tmp_path / "long.csv"
    long.write_text("Tag,Timestamp,Value\nA,2026-01-01T00:00:00Z,1\nA,2026-01-01T00:00:05Z,2\nB,2026-01-01T00:00:00Z,3\n")
    wide = tmp_path / "wide.csv"
    wide.write_text("Time,A,B\n2026-01-01T00:00:00Z,1,3\n2026-01-01T00:00:05Z,2,\n")
    for p in (long, wide):
        s = read_series(p)
        assert len(s["A"]) == 2 and len(s["B"]) == 1
        assert s["A"].t[1] - s["A"].t[0] == 5


def test_system_states_are_dropped(tmp_path):
    p = tmp_path / "d.csv"
    p.write_text("Tag,Timestamp,Value\nA,2026-01-01T00:00:00Z,1\nA,2026-01-01T00:00:05Z,Shutdown\n")
    assert len(read_series(p)["A"]) == 1


def test_config_percent_fallback_and_case(tmp_path):
    p = tmp_path / "c.csv"
    p.write_text("name,COMPDEVPERCENT,ExcDev,span,compressing\nA,2,0.5,50,0\n")
    c = read_configs(p)["A"]
    assert c.compdev == 1.0 and c.excdev == 0.5 and c.span == 50 and c.compressing is False


def test_exports(tmp_path):
    import numpy as np
    t = np.arange(0.0, 86400.0, 10.0)
    s = Series(t, 50 + 10 * np.sin(t / 3000))
    results = [tune_point("A", s, PointConfig(compdev=0.0001, span=100))]
    write_results_csv(results, tmp_path / "r.csv")
    n = write_pibuilder_csv(results, tmp_path / "b.csv", server="PISRV")
    r = pd.read_csv(tmp_path / "r.csv")
    b = pd.read_csv(tmp_path / "b.csv")
    assert r.loc[0, "Classification"] == "Storing noise"
    assert n == 1 and b.loc[0, "Name"] == "A" and b.loc[0, "Parent"] == "PISRV" and b.loc[0, "Selected(x)"] == "x"
    assert list(b.columns[:4]) == ["Selected(x)", "Parent", "Name", "ObjectType"]
