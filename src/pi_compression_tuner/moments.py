"""Key moments an archive must keep, and isolated spikes worth a second look.

A key moment is a timestamp an engineer marks on the trend (a peak, a trip, a step)
that the proposed archive must reproduce within the noise band. It is checked against
the smoothed signal, so one noisy event does not decide it. Moments can be
marked by hand or suggested from the signal's main turning points.

A spike is a single event far from both neighbors while the neighbors agree with
each other. Spikes are only counted and reported. They may be real events or
instrument faults, and only a person can tell which.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .models import Series


def find_spikes(v: np.ndarray, noise: float, k: float = 6.0) -> np.ndarray:
    """Indices of isolated one-event spikes at least ``k`` noise widths tall."""
    if len(v) < 3 or noise <= 0:
        return np.empty(0, dtype=np.int64)
    up, down = v[1:-1] - v[:-2], v[1:-1] - v[2:]
    height = np.minimum(np.abs(up), np.abs(down))
    same_side = np.sign(up) == np.sign(down)
    settles = np.abs(v[2:] - v[:-2]) < 0.5 * height
    return np.flatnonzero(same_side & settles & (height > k * noise)) + 1


def smooth(v: np.ndarray) -> np.ndarray:
    """Centered 5-event running median: the signal with single-event noise and spikes removed."""
    if len(v) < 5:
        return v.astype(float)
    return pd.Series(v).rolling(5, center=True, min_periods=1).median().to_numpy()


def suggest_moments(series: Series, noise: float, max_moments: int = 12) -> np.ndarray:
    """Timestamps of the signal's main peaks, valleys and steps.

    Peaks and valleys come from a zigzag over the smoothed signal: a turn counts once
    the signal moves back by a quarter of its observed range (or 10 noise widths, if
    larger). Steps are single-event jumps of the same size. The ``max_moments`` with
    the largest swings are kept. Isolated spikes are left out, since they may be
    instrument faults.
    """
    v = smooth(series.v)
    if len(v) < 3:
        return np.empty(0)
    h = max(0.25 * float(np.ptp(v)), 10 * noise)
    if h <= 0:
        return np.empty(0)
    found: dict[int, float] = {}
    hi_i = lo_i = 0
    trend = 0  # +1 rising, -1 falling, 0 unknown
    for i in range(1, len(v)):
        if v[i] > v[hi_i]:
            hi_i = i
        if v[i] < v[lo_i]:
            lo_i = i
        if trend >= 0 and v[hi_i] - v[i] >= h:
            if trend > 0:
                found[hi_i] = v[hi_i] - v[i]
            trend, lo_i = -1, i
        elif trend <= 0 and v[i] - v[lo_i] >= h:
            if trend < 0:
                found[lo_i] = v[i] - v[lo_i]
            trend, hi_i = 1, i
    jumps = np.abs(np.diff(v))
    for i in np.flatnonzero(jumps >= h):
        found[int(i + 1)] = max(found.get(int(i + 1), 0.0), float(jumps[i]))
    if not found:
        return np.empty(0)
    idx = np.array(sorted(found, key=lambda k: -found[k])[:max_moments])
    return np.sort(series.t[idx])


def moment_index(series: Series, times) -> np.ndarray:
    """Index of the event nearest each timestamp, without duplicates."""
    times = np.asarray(times, dtype=float)
    if len(times) == 0 or len(series) == 0:
        return np.empty(0, dtype=np.int64)
    n = len(series)
    pos = np.searchsorted(series.t, times)
    lo, hi = np.clip(pos - 1, 0, n - 1), np.clip(pos, 0, n - 1)
    nearest = np.where(np.abs(times - series.t[lo]) <= np.abs(series.t[hi] - times), lo, hi)
    return np.unique(nearest)


def read_moments(path: str | Path) -> dict[str, np.ndarray]:
    """Read key moments from a CSV with Tag and Timestamp columns, as timestamps per tag."""
    df = pd.read_csv(path)
    cols = {c.strip().lower(): c for c in df.columns}
    tag = next((cols[n] for n in ("tag", "name", "tagname", "pipoint", "point") if n in cols), None)
    time = next((cols[n] for n in ("timestamp", "time", "datetime", "date") if n in cols), None)
    if tag is None or time is None:
        raise ValueError("Key moments CSV needs Tag and Timestamp columns.")
    stamps = pd.to_datetime(df[time], utc=True)
    secs = ((stamps - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(seconds=1)).to_numpy(dtype=float)
    out: dict[str, np.ndarray] = {}
    for name, g in pd.Series(secs).groupby(df[tag].astype(str).to_numpy()):
        out[str(name)] = np.sort(g.to_numpy())
    return out


def moments_frame(moments: dict[str, np.ndarray]) -> pd.DataFrame:
    """Key moments as a Tag, Timestamp table, the format :func:`read_moments` reads."""
    rows = [(tag, pd.to_datetime(t, unit="s", utc=True).isoformat()) for tag, ts in moments.items() for t in ts]
    return pd.DataFrame(rows, columns=["Tag", "Timestamp"])
