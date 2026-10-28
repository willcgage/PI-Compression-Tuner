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
    assert set(r["Classification"]) <= {"Good", "Undercompressed", "Overcompressed", "Hard to compress",
                                         "Zero/span issue", "Flatline", "Insufficient data"}
