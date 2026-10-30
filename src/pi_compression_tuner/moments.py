"""Key moments an archive must keep, and isolated spikes worth a second look.

A key moment is a timestamp an engineer marks on the trend (a peak, a trip, a step)
that the proposed archive must reproduce within the noise band. It is checked against
the smoothed signal, so one noisy event does not decide it. Moments can be
marked by hand or suggested from the signal's main turning points and its alarm-limit
crossings.

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


def alarm_crossings(series: Series, limits, noise: float, max_moments: int = 24) -> np.ndarray:
    """Timestamps where the smoothed signal crosses an alarm limit.

    A crossing counts once the signal gets two noise widths past the limit, so a signal
    sitting on the limit is not marked over and over. Each crossing is placed at the
    first event on the far side. With more than ``max_moments``, an evenly spread
    subset is kept.
    """
    v = smooth(series.v)
    if len(v) < 3 or not len(limits):
        return np.empty(0)
    h = max(2 * noise, 1e-12)
    found: set[int] = set()
    for lim in limits:
        side = np.where(v > lim + h, 1, np.where(v < lim - h, -1, 0))
        known = np.flatnonzero(side)
        if len(known) < 2:
            continue
        settled = side[known]
        flips = known[1:][settled[1:] != settled[:-1]]  # where the signal settles on the other side
        above = v > lim
        raw = np.flatnonzero(above[1:] != above[:-1]) + 1  # every crossing, noise included
        for i in flips:
            k = np.searchsorted(raw, i, side="right") - 1
            found.add(int(raw[k]) if k >= 0 else int(i))
    if not found:
        return np.empty(0)
    idx = np.array(sorted(found))
    if len(idx) > max_moments:
        idx = idx[np.unique(np.linspace(0, len(idx) - 1, max_moments).round().astype(int))]
    return series.t[idx]


def suggest_moments(series: Series, noise: float, max_moments: int = 12, limits=()) -> np.ndarray:
    """Timestamps of the signal's main peaks, valleys and steps, and its alarm-limit crossings.

    Peaks and valleys come from a zigzag over the smoothed signal: a turn counts once
    the signal moves back by a quarter of its observed range (or 10 noise widths, if
    larger). Steps are single-event jumps of the same size. The ``max_moments`` with
    the largest swings are kept. Isolated spikes are left out, since they may be
    instrument faults. Crossings of ``limits`` are added (see :func:`alarm_crossings`).
    """
    v = smooth(series.v)
    if len(v) < 3:
        return np.empty(0)
    crossings = alarm_crossings(series, limits, noise, 2 * max_moments)
    h = max(0.25 * float(np.ptp(v)), 10 * noise)
    found: dict[int, float] = {}
    if h > 0:
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
    idx = np.array(sorted(found, key=lambda k: -found[k])[:max_moments], dtype=np.int64)
    return np.unique(np.concatenate([series.t[idx], crossings]))


def implied_compdev(series: Series, moments) -> float | None:
    """The CompDev the key moments alone imply, as a sanity check on a proposal.

    For each pair of consecutive moments, the largest distance of the smoothed signal
    from the straight line between them, averaged over the pairs (Method 1 of
    Silveira et al., 2012). A CompDev near it archives roughly the shape the moments
    draw. None with fewer than two moments, or with no events between them.
    """
    idx = moment_index(series, moments)
    if len(idx) < 2:
        return None
    v = smooth(series.v)
    devs = []
    for a, b in zip(idx[:-1], idx[1:]):
        if b - a < 2:
            continue
        t = series.t[a:b + 1]
        line = v[a] + (v[b] - v[a]) * (t - t[0]) / (t[-1] - t[0])
        devs.append(float(np.max(np.abs(v[a:b + 1] - line))))
    return float(np.mean(devs)) if devs else None


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
