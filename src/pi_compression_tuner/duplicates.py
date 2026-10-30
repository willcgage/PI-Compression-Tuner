"""Find PI points that archive the same signal.

One signal can end up in several points: ``tag_123.value`` and ``tag_123.scaled`` holding the same numbers, an
interface point configured twice, or a calculation that only rescales its input. Each copy costs archive space and
gives reports two names for one measurement. This compares the data already read for tuning:

1. Every moving series is resampled onto one time grid and correlated with every other, in blocks, to find
   candidate pairs cheaply. Pairs with the same base name are always checked as well.
2. Each candidate is checked on its own overlap: how many event timestamps the two share, the straight-line fit
   from one to the other, and what is left over after the fit.
3. Names and attributes (base name once suffixes such as ``.value`` and ``.scaled`` are dropped, instrument tag
   and point source, description) are reported as hints beside the data.

Read-only: it suggests points to look at. Nothing is changed in PI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
from typing import Callable

import numpy as np
import pandas as pd

from .models import Series


class Match(str, Enum):
    EXACT = "Exact copy"          # the same events: same timestamps, same values
    SAME = "Same signal"          # the same values over time, archived differently
    SCALED = "Scaled copy"        # one is a straight-line rescale of the other
    LOOSE = "Moves together"      # very closely correlated; may be redundant instruments or just related
    NAME = "Name only"            # look-alike names, but the data could not be compared


STRONG = (Match.EXACT, Match.SAME, Match.SCALED)

# Name endings that often mark a second point for the same signal. Dropped to find a base name.
COPY_WORDS = {"value", "val", "v", "pv", "scaled", "scale", "scl", "raw", "eu", "eng", "meas", "signal", "sig",
              "copy", "dup", "old", "new", "alt", "bak", "backup", "hist", "arch", "archive"}


@dataclass(frozen=True)
class DuplicateOptions:
    grid: int = 2048                 # time grid for the first, all-pairs pass
    candidate_r: float = 0.98        # |correlation| on the grid that earns a closer look
    loose_r: float = 0.995           # |correlation| reported as "Moves together"
    include_loose: bool = False      # report "Moves together" pairs too
    same_tol: float = 0.005          # RMS difference, as a share of range, still counted as the same signal
    scaled_tol: float = 0.01         # RMS fit residual, as a share of range, still counted as a scaled copy
    shared_min: float = 0.98         # share of events at shared timestamps for an exact copy
    time_tol_s: float = 0.5          # timestamps this close count as shared
    min_events: int = 10
    min_overlap: float = 0.5         # overlap needed, as a share of the shorter series' period
    max_candidates: int = 20_000
    verify_samples: int = 20_000     # most sample times used to check one pair


@dataclass
class DuplicatePair:
    a: str
    b: str
    match: Match
    r: float = float("nan")
    slope: float = float("nan")      # b ≈ slope × a + offset
    offset: float = float("nan")
    shared: float = float("nan")     # share of the smaller point's events with an event at the same time in the other
    rms_diff: float = float("nan")   # RMS of b − a, as a share of b's range
    max_diff: float = float("nan")   # largest |b − a|, engineering units
    hints: list[str] = field(default_factory=list)


@dataclass
class DuplicateGroup:
    members: list[str]
    match: Match
    keep: str                        # suggested point to keep: a plain name first, then the most detailed record
    redundant_per_day: float         # archive events per day stored by the other members
    pairs: list[DuplicatePair]


@dataclass
class DuplicateReport:
    groups: list[DuplicateGroup]
    loose: list[DuplicatePair]       # "Moves together" pairs, when asked for
    name_only: list[DuplicatePair]   # look-alike names whose data could not be compared
    compared: int                    # points with enough moving data to compare
    skipped: dict[str, str]          # tag -> why its data was not compared
    events_per_day: dict[str, float]

    @property
    def redundant_points(self) -> int:
        return sum(len(g.members) - 1 for g in self.groups)

    @property
    def redundant_per_day(self) -> float:
        return sum(g.redundant_per_day for g in self.groups)


# --- names ----------------------------------------------------------------

_TAIL = re.compile(r"^(.*?)[._\-\s/|:]+([a-z]+)$")


def base_name(tag: str) -> str:
    """The name with copy endings dropped: ``tag_123.value`` and ``TAG_123.Scaled`` both give ``tag_123``."""
    s = tag.strip().lower()
    while (m := _TAIL.match(s)) and m.group(2) in COPY_WORDS and m.group(1):
        s = m.group(1)
    return s


def name_hints(a: str, b: str, info_a: dict | None = None, info_b: dict | None = None) -> list[str]:
    ia, ib = info_a or {}, info_b or {}
    hints = []
    if base_name(a) == base_name(b):
        hints.append(f"Same base name '{base_name(a)}'")
    elif SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.85:
        hints.append("Similar names")
    ita, itb = (ia.get("instrumenttag") or "").strip(), (ib.get("instrumenttag") or "").strip()
    if ita and ita.lower() == itb.lower():
        same_ps = (ia.get("pointsource") or "").strip().lower() == (ib.get("pointsource") or "").strip().lower()
        hints.append(f"Same instrument tag{' and point source' if same_ps else ''} ({ita})")
    da, db = (ia.get("descriptor") or "").strip(), (ib.get("descriptor") or "").strip()
    if da and da.lower() == db.lower():
        hints.append("Same description")
    ua, ub = (ia.get("engunits") or "").strip(), (ib.get("engunits") or "").strip()
    if ua and ub and ua.lower() != ub.lower():
        hints.append(f"Units differ ({ua} and {ub})")
    return hints


# --- data -----------------------------------------------------------------

def _rate(s: Series) -> float:
    return len(s) / s.duration * 86400 if s.duration > 0 else float(len(s))


def _candidates(series: dict[str, Series], tags: list[str], opts: DuplicateOptions) -> list[tuple[str, str, float]]:
    """Pairs whose resampled values correlate at ``candidate_r`` or better, strongest first."""
    if len(tags) < 2:
        return []
    t0 = min(series[t].t[0] for t in tags)
    t1 = max(series[t].t[-1] for t in tags)
    grid = np.linspace(t0, t1, opts.grid)
    z = np.empty((len(tags), opts.grid), dtype=np.float32)
    keep = np.ones(len(tags), dtype=bool)
    for i, t in enumerate(tags):
        x = np.interp(grid, series[t].t, series[t].v)
        sd = x.std()
        if sd == 0:
            keep[i] = False
            z[i] = 0
        else:
            z[i] = (x - x.mean()) / (sd * np.sqrt(opts.grid))
    found = []
    block = 256
    for lo in range(0, len(tags), block):
        c = z[lo:lo + block] @ z.T
        rows, cols = np.nonzero(np.abs(c) >= opts.candidate_r)
        for i, j in zip(rows + lo, cols):
            if j > i and keep[i] and keep[j]:
                found.append((tags[i], tags[j], float(c[i - lo, j])))
    found.sort(key=lambda x: -abs(x[2]))
    return found[:opts.max_candidates]


def compare(a: Series, b: Series, opts: DuplicateOptions = DuplicateOptions()) -> dict | None:
    """Measures for one pair over their shared period, or None when they don't overlap enough to judge."""
    lo, hi = max(a.t[0], b.t[0]), min(a.t[-1], b.t[-1])
    shorter = min(a.duration, b.duration)
    if hi <= lo or (shorter > 0 and hi - lo < opts.min_overlap * shorter):
        return None
    ma, mb = (a.t >= lo) & (a.t <= hi), (b.t >= lo) & (b.t <= hi)
    ta, va_ev, tb, vb_ev = a.t[ma], a.v[ma], b.t[mb], b.v[mb]
    if len(ta) < 2 or len(tb) < 2:
        return None

    # Shared timestamps: for each event of the smaller set, the nearest event in the other.
    swap = len(ta) > len(tb)
    ts, vs, tbig, vbig = (tb, vb_ev, ta, va_ev) if swap else (ta, va_ev, tb, vb_ev)
    k = np.clip(np.searchsorted(tbig, ts), 1, len(tbig) - 1)
    k = np.where(np.abs(tbig[k - 1] - ts) <= np.abs(tbig[k] - ts), k - 1, k)
    hit = np.abs(tbig[k] - ts) <= opts.time_tol_s
    shared = float(hit.mean())
    pa, pb = (vbig[k][hit], vs[hit]) if swap else (vs[hit], vbig[k][hit])  # values at shared times, as (a, b)

    tt = np.union1d(ta, tb)
    if len(tt) > opts.verify_samples:
        tt = tt[np.linspace(0, len(tt) - 1, opts.verify_samples).astype(int)]
    xa, xb = np.interp(tt, a.t, a.v), np.interp(tt, b.t, b.v)
    rng = float(np.ptp(xb)) or float(np.ptp(xa))
    if rng == 0 or np.ptp(xa) == 0 or np.ptp(xb) == 0:
        return None
    r = float(np.corrcoef(xa, xb)[0, 1])
    slope, offset = np.polyfit(xa, xb, 1)
    diff = xb - xa
    resid = xb - (slope * xa + offset)
    tol = 2e-6 * max(np.abs(xa).max(), np.abs(xb).max()) + 1e-12  # float32 storage of the same number
    return {
        "r": r, "slope": float(slope), "offset": float(offset), "shared": shared,
        "rms_diff": float(np.sqrt(np.mean(diff ** 2)) / rng), "max_diff": float(np.abs(diff).max()),
        "rms_resid": float(np.sqrt(np.mean(resid ** 2)) / rng),
        "equal_at_shared": bool(len(pa) and np.mean(np.abs(pb - pa) <= tol) >= 0.995),
        "fit_at_shared": bool(len(pa) and np.mean(np.abs(pb - (slope * pa + offset)) <= tol + 1e-5 * rng) >= 0.995),
    }


def classify(m: dict, opts: DuplicateOptions = DuplicateOptions()) -> Match | None:
    exact_events = m["shared"] >= opts.shared_min
    if exact_events and m["equal_at_shared"]:
        return Match.EXACT
    if m["rms_diff"] <= opts.same_tol and m["r"] >= 0.999:
        return Match.SAME
    if (exact_events and m["fit_at_shared"]) or (abs(m["r"]) >= 0.9995 and m["rms_resid"] <= opts.scaled_tol):
        return Match.SCALED
    if abs(m["r"]) >= opts.loose_r:
        return Match.LOOSE
    return None


def _keeper(members: list[str], rates: dict[str, float]) -> str:
    """A plain name (nothing to drop) first, then the most events, then the shortest name."""
    return min(members, key=lambda t: (base_name(t) != t.strip().lower(), -rates.get(t, 0), len(t), t))


def _groups(pairs: list[DuplicatePair], rates: dict[str, float]) -> list[DuplicateGroup]:
    parent: dict[str, str] = {}

    def root(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for p in pairs:
        parent[root(p.a)] = root(p.b)
    members: dict[str, list[str]] = {}
    for t in parent:
        members.setdefault(root(t), []).append(t)
    order = list(Match)
    out = []
    for tags in members.values():
        tags = sorted(tags)
        own = [p for p in pairs if p.a in tags]
        keep = _keeper(tags, rates)
        out.append(DuplicateGroup(members=[keep] + [t for t in tags if t != keep],
                                  match=max((p.match for p in own), key=order.index),  # the weakest link
                                  keep=keep, redundant_per_day=sum(rates.get(t, 0) for t in tags if t != keep),
                                  pairs=own))
    out.sort(key=lambda g: (order.index(g.match), -g.redundant_per_day))
    return out


def find_duplicates(series: dict[str, Series], infos: dict[str, dict] | None = None,
                    opts: DuplicateOptions = DuplicateOptions(),
                    progress: Callable[[int, int], None] | None = None) -> DuplicateReport:
    infos = infos or {}
    rates = {t: _rate(s) for t, s in series.items() if len(s)}
    skipped, moving = {}, []
    for t, s in series.items():
        if len(s) < opts.min_events:
            skipped[t] = "too few events" if len(s) else "no numeric values"
        elif np.ptp(s.v) == 0:
            skipped[t] = "not moving"
        else:
            moving.append(t)

    todo = {(a, b): None for a, b, _ in _candidates(series, moving, opts)}
    by_base: dict[str, list[str]] = {}
    for t in series:
        by_base.setdefault(base_name(t), []).append(t)
    name_pairs = {tuple(sorted((x, y))) for ts in by_base.values() if len(ts) > 1
                  for i, x in enumerate(ts) for y in ts[i + 1:]}
    for a, b in sorted(name_pairs):
        if (a, b) not in todo and (b, a) not in todo:
            todo[(a, b)] = None

    strong, loose, name_only = [], [], []
    for n, (a, b) in enumerate(todo, 1):
        m = None
        if a in moving and b in moving:
            m = compare(series[a], series[b], opts)
        hints = name_hints(a, b, infos.get(a), infos.get(b))
        if m is None:
            if (a, b) in name_pairs or (b, a) in name_pairs:
                why = skipped.get(a) or skipped.get(b) or "the periods hardly overlap"
                name_only.append(DuplicatePair(a, b, Match.NAME, hints=hints + [f"Data not compared: {why}"]))
        else:
            verdict = classify(m, opts)
            if verdict is not None and (verdict in STRONG or opts.include_loose):
                p = DuplicatePair(a, b, verdict, r=m["r"], slope=m["slope"], offset=m["offset"], shared=m["shared"],
                                  rms_diff=m["rms_diff"], max_diff=m["max_diff"], hints=hints)
                (strong if verdict in STRONG else loose).append(p)
        if progress:
            progress(n, len(todo))
    loose.sort(key=lambda p: -abs(p.r))
    return DuplicateReport(groups=_groups(strong, rates), loose=loose, name_only=name_only, compared=len(moving),
                           skipped=skipped, events_per_day=rates)


# --- words and tables -----------------------------------------------------

def fit_text(p: DuplicatePair) -> str:
    if np.isnan(p.slope):
        return ""
    offset = round(p.offset, 9)  # float noise from the fit
    term = "" if offset == 0 else f" {'+' if offset > 0 else '−'} {abs(offset):.6g}"
    return f"{p.b} = {p.slope:.6g} × {p.a}{term}"


def describe(p: DuplicatePair) -> str:
    """One pair's finding in plain words."""
    if p.match is Match.EXACT:
        text = (f"{p.b} stores the same events as {p.a}: {p.shared:.1%} of events fall at the same time, "
                "with the same value.")
    elif p.match is Match.SAME:
        text = (f"{p.b} follows {p.a} value for value (RMS difference {p.rms_diff:.2%} of range), "
                f"though only {p.shared:.0%} of events share a timestamp; they are likely archived with different "
                "exception or compression settings.")
    elif p.match is Match.SCALED:
        text = f"{p.b} is a straight-line rescale of {p.a}: {fit_text(p)} (correlation {p.r:.5f})."
    elif p.match is Match.LOOSE:
        text = (f"{p.a} and {p.b} move together (correlation {p.r:.4f}) but are not copies. They may be redundant "
                "instruments on the same service, or simply related.")
    else:
        text = f"{p.a} and {p.b} have look-alike names, but their data could not be compared."
    return text + (f" {'; '.join(p.hints)}." if p.hints else "")


def groups_frame(report: DuplicateReport) -> pd.DataFrame:
    rows = [{"Group": i, "Finding": g.match.value, "Points": len(g.members), "Suggested keep": g.keep,
             "Copies": ", ".join(g.members[1:]), "Copy events/day": g.redundant_per_day}
            for i, g in enumerate(report.groups, 1)]
    return pd.DataFrame(rows, columns=["Group", "Finding", "Points", "Suggested keep", "Copies", "Copy events/day"])


def pairs_frame(report: DuplicateReport) -> pd.DataFrame:
    """Every pair found, for the CSV export: groups first, then looser matches and name-only pairs."""
    rows = []
    tagged = [(i, p) for i, g in enumerate(report.groups, 1) for p in g.pairs]
    tagged += [("", p) for p in report.loose + report.name_only]
    for gid, p in tagged:
        rows.append({"Group": gid, "Finding": p.match.value, "Point A": p.a, "Point B": p.b,
                     "Correlation": p.r, "Slope": p.slope, "Offset": p.offset, "Shared timestamps": p.shared,
                     "RMS difference (share of range)": p.rms_diff, "Largest difference": p.max_diff,
                     "A events/day": report.events_per_day.get(p.a), "B events/day": report.events_per_day.get(p.b),
                     "Hints": "; ".join(p.hints)})
    cols = ["Group", "Finding", "Point A", "Point B", "Correlation", "Slope", "Offset", "Shared timestamps",
            "RMS difference (share of range)", "Largest difference", "A events/day", "B events/day", "Hints"]
    return pd.DataFrame(rows, columns=cols)
