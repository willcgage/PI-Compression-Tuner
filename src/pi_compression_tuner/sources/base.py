"""What every PI source provides. Sources are read-only."""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Callable, Protocol

from ..models import PointConfig, Series


@dataclass(frozen=True)
class PointRef:
    """A PI Point found through a source. ``web_id`` is the PI Web API WebId; AF SDK leaves it empty.

    ``descriptor``, ``engunits``, ``pointtype`` and ``pointclass`` are filled in by searches, to help pick points
    and match templates; they are not part of its identity.
    """

    name: str
    web_id: str = ""
    path: str = ""
    descriptor: str = field(default="", compare=False)
    engunits: str = field(default="", compare=False)
    pointtype: str = field(default="", compare=False)
    pointclass: str = field(default="", compare=False)


MAX_SCAN = 100_000  # most point names a search with attribute filters reads before it stops


def text_matches(pattern: str, value: str, contains: bool = False) -> bool:
    """Case-insensitive match. ``*`` and ``?`` are wildcards; plain text must equal the value, or with
    ``contains`` appear anywhere in it. An empty pattern matches everything."""
    pattern = (pattern or "").strip().lower()
    value = (value or "").strip().lower()
    if not pattern:
        return True
    if any(c in pattern for c in "*?"):
        return fnmatchcase(value, pattern)
    return pattern in value if contains else pattern == value


@dataclass(frozen=True)
class PointFilter:
    """What a point search asks for besides the tag mask. Descriptor text matches anywhere in the description."""

    pointsource: str = ""
    engunits: str = ""
    descriptor: str = ""

    def __bool__(self) -> bool:
        return any(x.strip() for x in (self.pointsource, self.engunits, self.descriptor))

    def keeps(self, ref: PointRef) -> bool:
        """Descriptor and units checks (point source is checked separately, where it is read)."""
        return text_matches(self.engunits, ref.engunits) and text_matches(self.descriptor, ref.descriptor, contains=True)


class PISource(Protocol):
    def check(self, server: str) -> None: ...
    def find_points(self, server: str, name_filter: str = "*", max_count: int = 1000,
                    where: PointFilter = PointFilter()) -> list[PointRef]: ...
    def point(self, server: str, tag: str) -> PointRef: ...
    def attributes(self, ref: PointRef) -> dict: ...
    def config(self, ref: PointRef) -> PointConfig: ...
    def recorded(self, ref: PointRef, start: str, end: str) -> Series: ...
    def event_count(self, ref: PointRef, start: str, end: str) -> int: ...
    def record_snapshots(self, refs: list[PointRef], duration_s: float, poll_s: float = 5.0,
                         on_poll: Callable[[dict], None] | None = None) -> dict[str, Series]: ...
