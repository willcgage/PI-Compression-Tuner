"""Operations shared by the CLI and the UI."""

from __future__ import annotations

from typing import Callable, Iterable

import pandas as pd

from ..models import Series
from .base import PISource, PointRef

CONFIG_COLUMNS = ("ExcDev", "ExcMin", "ExcMax", "CompDev", "CompMin", "CompMax", "Compressing", "Step", "Zero", "Span", "PointType",
                  "Descriptor", "EngUnits", "PtClassName", "PointSource", "InstrumentTag")


def resolve_points(src: PISource, server: str, tags: Iterable[str] = (), name_filter: str | None = None,
                   limit: int = 100) -> list[PointRef]:
    tags = [t for t in tags if t]
    if tags:
        return [src.point(server, t) for t in tags]
    if not name_filter:
        raise ValueError("give tags or a name filter")
    return src.find_points(server, name_filter, max_count=limit)


def configs_frame(src: PISource, refs: list[PointRef]) -> pd.DataFrame:
    rows = []
    for ref in refs:
        a = src.attributes(ref)
        rows.append({"Name": ref.name, **{k: a.get(k.lower()) for k in CONFIG_COLUMNS}})
    return pd.DataFrame(rows, columns=["Name", *CONFIG_COLUMNS])


def fetch_recorded(src: PISource, refs: list[PointRef], start: str, end: str,
                   progress: Callable[[int, int], None] | None = None) -> dict[str, Series]:
    out = {}
    for i, ref in enumerate(refs):
        out[ref.name] = src.recorded(ref, start, end)
        if progress:
            progress(i + 1, len(refs))
    return out


def series_frame(series: dict[str, Series]) -> pd.DataFrame:
    frames = [pd.DataFrame({"Tag": name, "Timestamp": pd.to_datetime(s.t, unit="s", utc=True), "Value": s.v})
              for name, s in series.items() if len(s)]
    return pd.concat(frames) if frames else pd.DataFrame(columns=["Tag", "Timestamp", "Value"])
