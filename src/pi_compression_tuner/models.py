"""Core data types: a PI Point's data-reduction settings and a time series."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PointConfig:
    """The PI Point attributes that control exception and compression.

    Deviations are in engineering units. Times are in seconds.
    Defaults match the PI Data Archive defaults for a new float point.

    ``alarm_limits`` are not PI Point attributes: they are the point's alarm thresholds
    (Lo, Hi, LoLo, HiHi, in any order) from wherever the site keeps them, used only to
    suggest key moments where the signal crosses one.
    """

    excdev: float = 0.0
    excmin: float = 0.0
    excmax: float = 600.0
    compdev: float = 0.0
    compmin: float = 0.0
    compmax: float = 28800.0
    compressing: bool = True
    step: bool = False
    zero: float = 0.0
    span: float = 100.0
    alarm_limits: tuple = ()

    @classmethod
    def from_percent(cls, excdevpercent: float, compdevpercent: float, span: float = 100.0, **kw) -> "PointConfig":
        """Build a config from ExcDevPercent / CompDevPercent, which PI defines as percent of Span."""
        return cls(excdev=excdevpercent / 100.0 * span, compdev=compdevpercent / 100.0 * span, span=span, **kw)

    @property
    def excdevpercent(self) -> float:
        return 100.0 * self.excdev / self.span if self.span else float("nan")

    @property
    def compdevpercent(self) -> float:
        return 100.0 * self.compdev / self.span if self.span else float("nan")

    def with_deviations(self, excdev: float, compdev: float) -> "PointConfig":
        return replace(self, excdev=excdev, compdev=compdev)


@dataclass(frozen=True)
class Series:
    """A time series: timestamps as float seconds since the epoch, values as floats."""

    t: np.ndarray
    v: np.ndarray

    def __post_init__(self):
        t = np.asarray(self.t, dtype=float)
        v = np.asarray(self.v, dtype=float)
        if t.shape != v.shape or t.ndim != 1:
            raise ValueError("t and v must be 1-D arrays of equal length")
        keep = np.isfinite(t) & np.isfinite(v)
        t, v = t[keep], v[keep]
        order = np.argsort(t, kind="stable")
        object.__setattr__(self, "t", t[order])
        object.__setattr__(self, "v", v[order])

    def __len__(self) -> int:
        return len(self.t)

    @property
    def duration(self) -> float:
        return float(self.t[-1] - self.t[0]) if len(self.t) > 1 else 0.0

    def take(self, idx: np.ndarray) -> "Series":
        return Series(self.t[idx], self.v[idx])

    @classmethod
    def from_pandas(cls, s: pd.Series) -> "Series":
        """Build from a pandas Series indexed by timestamps. Non-numeric values (PI system states) are dropped."""
        idx = pd.to_datetime(s.index, utc=True)
        t = (idx - pd.Timestamp(0, tz="UTC")) / pd.Timedelta(seconds=1)
        v = pd.to_numeric(s.values, errors="coerce")
        return cls(np.asarray(t, dtype=float), np.asarray(v, dtype=float))
