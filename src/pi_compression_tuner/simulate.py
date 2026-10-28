"""Replays of PI's two data-reduction stages.

Exception reporting runs at the interface, before values reach the snapshot.
Compression (the swinging-door algorithm) runs on the PI Data Archive, between
the snapshot and the archive. Both functions return the indices of the input
events that pass, so the caller can map results back onto the original series.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import PointConfig, Series


def exception_filter(t: np.ndarray, v: np.ndarray, excdev: float, excmin: float = 0.0, excmax: float = 600.0) -> np.ndarray:
    """Indices of the values an interface would send to the snapshot.

    Rules, following PI interface (UniInt) exception reporting:
      * the first value is always sent;
      * a value inside ExcMin seconds of the last sent value is never sent;
      * a value that differs from the last sent value by more than ExcDev is sent,
        and the value received just before it is sent too (if it was not already),
        so the archive keeps the start of the change;
      * a value is sent once ExcMax seconds have passed since the last sent value
        (ExcMax <= 0 disables this).
    """
    n = len(t)
    if n == 0:
        return np.empty(0, dtype=np.int64)
    tl = t.tolist()
    vl = v.tolist()
    out = [0]
    last_t, last_v = tl[0], vl[0]
    for i in range(1, n):
        dt = tl[i] - last_t
        if dt < excmin:
            continue
        if abs(vl[i] - last_v) > excdev:
            prev = i - 1
            if prev > out[-1] and tl[prev] - last_t >= excmin:
                out.append(prev)
            out.append(i)
            last_t, last_v = tl[i], vl[i]
        elif excmax > 0 and dt >= excmax:
            out.append(i)
            last_t, last_v = tl[i], vl[i]
    return np.asarray(out, dtype=np.int64)


@dataclass(frozen=True)
class DoorResult:
    """Output of :func:`swinging_door`.

    ``archived`` holds the indices written to the archive. ``snapshot`` is the index of
    the value still held in the snapshot at the end of the data (or -1), which PI shows
    on trends but has not archived yet.
    """

    archived: np.ndarray
    snapshot: int


def swinging_door(t: np.ndarray, v: np.ndarray, compdev: float, compmin: float = 0.0, compmax: float = 28800.0) -> DoorResult:
    """Indices of snapshot events that PI's swinging-door compression would archive.

    The newest snapshot is held. When the next event arrives, PI checks whether a
    straight line from the last archived event to the new event passes within
    CompDev of every event held since the last archive. If it does, the new event
    replaces the held one. If not, the held event is archived and the test restarts
    from it. Events inside CompMin seconds of the last archive are never archived,
    and the held event is archived once CompMax seconds have passed.
    """
    n = len(t)
    if n == 0:
        return DoorResult(np.empty(0, dtype=np.int64), -1)
    tl = t.tolist()
    vl = v.tolist()
    archived = [0]
    a_t, a_v = tl[0], vl[0]
    held = -1
    lo, hi = float("-inf"), float("inf")

    def bounds(i):
        dt = tl[i] - a_t
        return (vl[i] - compdev - a_v) / dt, (vl[i] + compdev - a_v) / dt

    i = 1
    while i < n:
        dt = tl[i] - a_t
        if dt <= 0:
            i += 1
            continue
        if held < 0:
            held = i
            lo, hi = bounds(i)
            i += 1
            continue
        if dt < compmin:
            # Too soon after the last archive to archive anything: just hold the newer value.
            l2, h2 = bounds(i)
            lo, hi = max(lo, l2), min(hi, h2)
            held = i
            i += 1
            continue
        slope = (vl[i] - a_v) / dt
        timed_out = compmax > 0 and dt > compmax
        if timed_out or not (lo <= slope <= hi):
            archived.append(held)
            a_t, a_v = tl[held], vl[held]
            held = -1
            lo, hi = float("-inf"), float("inf")
            continue  # re-test the same event against the new archived point
        l2, h2 = bounds(i)
        lo, hi = max(lo, l2), min(hi, h2)
        held = i
        i += 1
    return DoorResult(np.asarray(archived, dtype=np.int64), held)


@dataclass(frozen=True)
class ArchiveSim:
    """A simulated archive. ``archived`` and ``snapshot`` index into the input series."""

    archived: np.ndarray
    snapshot: int
    sent: int  # events that reached the snapshot after exception

    def visible(self) -> np.ndarray:
        """Archived events plus the held snapshot: what a PI trend would draw."""
        if self.snapshot >= 0:
            return np.append(self.archived, self.snapshot)
        return self.archived


def simulate_archive(series: Series, config: PointConfig, apply_exception: bool = True) -> ArchiveSim:
    """Run a series through exception (optionally) and compression with the given settings.

    Use ``apply_exception=False`` when the input is already a snapshot stream, meaning
    the interface has applied exception before the data was recorded.
    """
    t, v = series.t, series.v
    if apply_exception:
        sent_idx = exception_filter(t, v, config.excdev, config.excmin, config.excmax)
    else:
        sent_idx = np.arange(len(t), dtype=np.int64)
    if not config.compressing:
        return ArchiveSim(sent_idx, -1, len(sent_idx))
    door = swinging_door(t[sent_idx], v[sent_idx], config.compdev, config.compmin, config.compmax)
    snap = int(sent_idx[door.snapshot]) if door.snapshot >= 0 else -1
    return ArchiveSim(sent_idx[door.archived], snap, len(sent_idx))
