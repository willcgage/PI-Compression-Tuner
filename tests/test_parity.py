"""Check the simulator against archives produced by a real PI Data Archive.

Each case is a folder under tests/parity/ containing:
  snapshot.csv  Timestamp,Value  every snapshot event (for example from `pict pi record`)
  archive.csv   Timestamp,Value  what PI archived over the same period with compression on
  config.csv    one row of PI attributes (PI Builder column names) in force during the capture
The test skips when no cases are present.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pi_compression_tuner import Series, simulate_archive
from pi_compression_tuner.sources.csvfiles import read_configs

CASES = sorted(p for p in (Path(__file__).parent / "parity").iterdir() if p.is_dir()) if (Path(__file__).parent / "parity").exists() else []


def _load(p):
    df = pd.read_csv(p)
    return Series.from_pandas(df.set_index(df.columns[0])[df.columns[1]])


@pytest.mark.skipif(not CASES, reason="no parity cases in tests/parity")
@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
def test_simulated_archive_matches_pi(case):
    snap, actual = _load(case / "snapshot.csv"), _load(case / "archive.csv")
    cfg = next(iter(read_configs(case / "config.csv").values()))
    sim = simulate_archive(snap, cfg, apply_exception=False)
    inside = (actual.t >= snap.t[0]) & (actual.t <= snap.t[-1])
    expected = set(np.round(actual.t[inside], 3))
    got = set(np.round(snap.t[sim.archived], 3))
    overlap = len(expected & got) / max(len(expected | got), 1)
    assert overlap >= 0.98, f"{case.name}: {overlap:.1%} of archived events match"
