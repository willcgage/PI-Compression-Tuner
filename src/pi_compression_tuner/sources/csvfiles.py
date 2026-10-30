"""Read signal data and point settings from CSV files.

Data files can be long (one row per event, with tag, timestamp and value columns)
or wide (a timestamp column, then one column per tag). Settings files use PI
attribute names as column headers, as PI Builder exports them, matched without
regard to case.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ..models import PointConfig, Series

_TAG_COLS = ("tag", "name", "tagname", "pipoint", "point")
_TIME_COLS = ("timestamp", "time", "datetime", "date")
_VALUE_COLS = ("value", "val")


def _find(cols: dict[str, str], names: tuple[str, ...]) -> str | None:
    for n in names:
        if n in cols:
            return cols[n]
    return None


def read_series(path: str | Path) -> dict[str, Series]:
    """Read a data CSV into one :class:`Series` per tag."""
    df = pd.read_csv(path)
    cols = {c.strip().lower(): c for c in df.columns}
    tcol = _find(cols, _TIME_COLS) or df.columns[0]
    tagcol = _find(cols, _TAG_COLS)
    vcol = _find(cols, _VALUE_COLS)
    out: dict[str, Series] = {}
    if tagcol and vcol:
        for tag, g in df.groupby(tagcol, sort=False):
            out[str(tag)] = Series.from_pandas(g.set_index(tcol)[vcol])
    else:
        indexed = df.set_index(tcol)
        for col in indexed.columns:
            out[str(col)] = Series.from_pandas(indexed[col].dropna())
    return out


def _as_bool(x) -> bool:
    if isinstance(x, str):
        return x.strip().lower() in ("1", "true", "yes", "on")
    return bool(x) if pd.notna(x) else False


def read_configs(path: str | Path) -> dict[str, PointConfig]:
    """Read point settings. Missing attributes take PI defaults.

    ExcDevPercent and CompDevPercent are used only where ExcDev and CompDev are absent.
    """
    df = pd.read_csv(path)
    cols = {c.strip().lower(): c for c in df.columns}
    tagcol = _find(cols, _TAG_COLS)
    if tagcol is None:
        raise ValueError(f"{path}: no tag column (expected one of {', '.join(_TAG_COLS)})")
    defaults = PointConfig()
    out: dict[str, PointConfig] = {}

    def num(row, name, default):
        c = cols.get(name)
        if c is None or pd.isna(row[c]):
            return default
        return float(row[c])

    for _, row in df.iterrows():
        span = num(row, "span", defaults.span)
        excdev = num(row, "excdev", None)
        if excdev is None:
            pct = num(row, "excdevpercent", None)
            excdev = pct / 100.0 * span if pct is not None else defaults.excdev
        compdev = num(row, "compdev", None)
        if compdev is None:
            pct = num(row, "compdevpercent", None)
            compdev = pct / 100.0 * span if pct is not None else defaults.compdev
        out[str(row[tagcol])] = PointConfig(
            excdev=excdev,
            excmin=num(row, "excmin", defaults.excmin),
            excmax=num(row, "excmax", defaults.excmax),
            compdev=compdev,
            compmin=num(row, "compmin", defaults.compmin),
            compmax=num(row, "compmax", defaults.compmax),
            compressing=_as_bool(row[cols["compressing"]]) if "compressing" in cols else defaults.compressing,
            step=_as_bool(row[cols["step"]]) if "step" in cols else defaults.step,
            zero=num(row, "zero", defaults.zero),
            span=span,
        )
    return out


_INFO_COLS = {"descriptor": ("descriptor", "description"), "engunits": ("engunits", "units", "engineeringunits"),
              "pointtype": ("pointtype",), "ptclassname": ("ptclassname", "pointclass"), "pointsource": ("pointsource",)}


def read_point_info(path: str | Path) -> dict[str, dict]:
    """What templates match on (Descriptor, EngUnits, PointType, PtClassName, PointSource), from a settings CSV.

    Points get an entry only when the file has at least one of these columns.
    """
    from ..templates import point_info
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    cols = {c.strip().lower(): c for c in df.columns}
    tagcol = _find(cols, _TAG_COLS)
    found = {k: _find(cols, names) for k, names in _INFO_COLS.items()}
    if tagcol is None or not any(found.values()):
        return {}
    return {str(row[tagcol]): point_info(str(row[tagcol]), {k: row[c] for k, c in found.items() if c})
            for _, row in df.iterrows()}
