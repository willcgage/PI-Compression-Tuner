"""What every PI source provides. Sources are read-only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..models import PointConfig, Series


@dataclass(frozen=True)
class PointRef:
    """A PI Point found through a source. ``web_id`` is the PI Web API WebId; AF SDK leaves it empty."""

    name: str
    web_id: str = ""
    path: str = ""


class PISource(Protocol):
    def check(self, server: str) -> None: ...
    def find_points(self, server: str, name_filter: str = "*", max_count: int = 1000) -> list[PointRef]: ...
    def point(self, server: str, tag: str) -> PointRef: ...
    def attributes(self, ref: PointRef) -> dict: ...
    def config(self, ref: PointRef) -> PointConfig: ...
    def recorded(self, ref: PointRef, start: str, end: str) -> Series: ...
    def event_count(self, ref: PointRef, start: str, end: str) -> int: ...
    def record_snapshots(self, refs: list[PointRef], duration_s: float, poll_s: float = 5.0,
                         on_poll: Callable[[dict], None] | None = None) -> dict[str, Series]: ...
