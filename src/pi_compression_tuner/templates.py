"""Point templates: starting-point settings for kinds of PI Points, matched on what the point is.

A template says which points it covers (tag name, description, engineering units, point type,
point class, point source) and what a reasonable starting point is for them: CompDev, ExcDev and
the time limits, plus optional tuning overrides (target retention, largest error). The tuner still
proposes CompDev from the data; the template supplies the baseline it is compared against, the time
limits the data can't tell us, and per-kind targets.

Matching: every rule group a template fills in must match (or, with ``match_any``, at least one group);
within a group, any entry matches. A template with no rules matches nothing. The enabled template with the lowest priority number wins.

The starter list comes from published PI guidance (see docs/templates.md for the sources). It is a
starting point for a site's own list, which is kept in the admin settings and edited there.
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field, fields, replace
from fnmatch import fnmatchcase

from .models import PointConfig

RULE_FIELDS = ("match_tag", "match_descriptor", "match_units", "match_pointtype", "match_pointclass", "match_pointsource")
SETTING_FIELDS = ("compdev", "compdev_pct", "excdev", "excdev_pct", "compmin", "compmax", "excmin", "excmax",
                  "step", "compressing")
TUNING_FIELDS = ("target_pct", "max_error", "max_error_pct")


@dataclass
class Template:
    name: str
    enabled: bool = True
    priority: int = 100
    note: str = ""
    # which points it covers
    match_any: bool = False                                     # any filled-in group is enough, not all of them
    match_tag: list[str] = field(default_factory=list)          # wildcards on the tag name, e.g. *.SP
    match_descriptor: list[str] = field(default_factory=list)   # words in the description, or wildcards
    match_units: list[str] = field(default_factory=list)        # engineering units, e.g. degF, psig
    match_pointtype: list[str] = field(default_factory=list)    # float32, float64, int32, digital, ...
    match_pointclass: list[str] = field(default_factory=list)   # classic, base, ...
    match_pointsource: list[str] = field(default_factory=list)  # interface point source, e.g. OPC
    # starting-point settings; None leaves the point's own value
    compdev: float | None = None        # engineering units
    compdev_pct: float | None = None    # percent of span, used when compdev is not set
    excdev: float | None = None         # engineering units
    excdev_pct: float | None = None     # percent of span; with neither set, ExcDev is half of CompDev
    compmin: float | None = None        # seconds
    compmax: float | None = None
    excmin: float | None = None
    excmax: float | None = None
    step: bool | None = None
    compressing: bool | None = None
    # tuning overrides; None uses the run's own setting
    target_pct: float | None = None     # target retention, percent
    max_error: float | None = None      # engineering units
    max_error_pct: float | None = None  # percent of span

    def has_rules(self) -> bool:
        return any(getattr(self, f) for f in RULE_FIELDS)

    def matches(self, info: dict) -> bool:
        """``info`` holds the point's ``tag``, ``descriptor``, ``engunits``, ``pointtype``, ``ptclassname`` and ``pointsource``."""
        if not self.has_rules():
            return False
        checks = (
            (self.match_tag, lambda p: _wild(p, info.get("tag", ""))),
            (self.match_descriptor, lambda p: _words(p, info.get("descriptor", ""))),
            (self.match_units, lambda p: norm_units(p) == norm_units(info.get("engunits", ""))),
            (self.match_pointtype, lambda p: _wild(p, info.get("pointtype", ""))),
            (self.match_pointclass, lambda p: _wild(p, info.get("ptclassname", ""))),
            (self.match_pointsource, lambda p: _wild(p, info.get("pointsource", ""))),
        )
        hits = [any(test(p) for p in rules if p.strip()) for rules, test in checks if any(p.strip() for p in rules)]
        return any(hits) if self.match_any else all(hits)

    def baseline(self, current: PointConfig) -> PointConfig:
        """The template's settings applied over the point's current ones."""
        span = current.span if current.span > 0 else math.nan
        compdev = self.compdev if self.compdev is not None else (
            self.compdev_pct / 100 * span if self.compdev_pct is not None else None)
        if compdev is not None and not math.isfinite(compdev):
            compdev = None
        excdev = self.excdev if self.excdev is not None else (
            self.excdev_pct / 100 * span if self.excdev_pct is not None else None)
        if excdev is not None and not math.isfinite(excdev):
            excdev = None
        if excdev is None and compdev is not None:
            excdev = compdev / 2
        changes = {k: v for k, v in {
            "compdev": compdev, "excdev": excdev, "compmin": self.compmin, "compmax": self.compmax,
            "excmin": self.excmin, "excmax": self.excmax, "step": self.step, "compressing": self.compressing,
        }.items() if v is not None}
        return replace(current, **changes)

    def has_settings(self) -> bool:
        return any(getattr(self, f) is not None for f in SETTING_FIELDS)

    def summary(self) -> str:
        """The template's settings in a few words, for tables."""
        parts = []
        if self.compdev is not None:
            parts.append(f"CompDev {self.compdev:g}")
        elif self.compdev_pct is not None:
            parts.append(f"CompDev {self.compdev_pct:g}% of span")
        if self.excdev is not None:
            parts.append(f"ExcDev {self.excdev:g}")
        elif self.excdev_pct is not None:
            parts.append(f"ExcDev {self.excdev_pct:g}% of span")
        elif self.compdev is not None or self.compdev_pct is not None:
            parts.append("ExcDev ½ CompDev")
        for k, label in (("compmax", "CompMax"), ("excmax", "ExcMax"), ("compmin", "CompMin"), ("excmin", "ExcMin")):
            v = getattr(self, k)
            if v is not None:
                parts.append(f"{label} {_duration(v)}")
        if self.step is not None:
            parts.append(f"Step {'on' if self.step else 'off'}")
        if self.compressing is not None:
            parts.append(f"Compression {'on' if self.compressing else 'off'}")
        if self.target_pct is not None:
            parts.append(f"target {self.target_pct:g}%")
        if self.max_error is not None:
            parts.append(f"error ≤ {self.max_error:g}")
        if self.max_error_pct is not None:
            parts.append(f"error ≤ {self.max_error_pct:g}% of span")
        return " · ".join(parts)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Template":
        known = {f.name: f for f in fields(cls)}
        out = {}
        for k, v in d.items():
            if k not in known:
                continue
            if k in RULE_FIELDS:
                v = [str(x) for x in v] if isinstance(v, (list, tuple)) else split_list(str(v))
            out[k] = v
        if not str(out.get("name", "")).strip():
            raise ValueError("a template needs a name")
        return cls(**out)


def _duration(s: float) -> str:
    for unit, n in (("d", 86400), ("h", 3600), ("min", 60)):
        if s >= n and s % n == 0:
            return f"{s / n:g} {unit}"
    return f"{s:g} s"


def split_list(text: str) -> list[str]:
    """Comma- or line-separated text as a list, for editing rule groups as text."""
    return [p.strip() for p in re.split(r"[,\n]", text or "") if p.strip()]


def norm_units(u: str) -> str:
    """Engineering units compared loosely: case, spaces, dots, ° and 'deg' are ignored (°F = degF = deg F = F)."""
    u = (u or "").strip().lower().replace("°", "").replace(" ", "").replace(".", "")
    return u[3:] if u.startswith("deg") and len(u) > 3 else u


def _wild(pattern: str, value: str) -> bool:
    return fnmatchcase((value or "").lower(), pattern.strip().lower())


def _words(pattern: str, text: str) -> bool:
    """A plain word or phrase matches as whole words; text with * or ? matches the whole description."""
    pattern, text = pattern.strip().lower(), (text or "").lower()
    if any(c in pattern for c in "*?"):
        return fnmatchcase(text, pattern)
    return re.search(rf"(?<![a-z0-9]){re.escape(pattern)}(?![a-z0-9])", text) is not None


def match(templates: list[Template], info: dict) -> Template | None:
    """The enabled template with the lowest priority number that matches, or None."""
    for t in sorted((t for t in templates if t.enabled), key=lambda t: t.priority):
        if t.matches(info):
            return t
    return None


def point_info(tag: str, attrs: dict | None = None, ref=None) -> dict:
    """What templates match on, from a point's attributes (lowercase PI names) or a search result."""
    attrs = attrs or {}

    def get(key, ref_attr=""):
        v = attrs.get(key)
        if v in (None, "") and ref is not None and ref_attr:
            v = getattr(ref, ref_attr, "")
        return "" if v is None else str(v)

    return {"tag": tag, "descriptor": get("descriptor", "descriptor"), "engunits": get("engunits", "engunits"),
            "pointtype": get("pointtype", "pointtype"), "ptclassname": get("ptclassname", "pointclass"),
            "pointsource": get("pointsource"), "instrumenttag": get("instrumenttag")}


def tune_options_for(template: Template | None, opts):
    """The run's tuning options with the template's overrides applied."""
    if template is None:
        return opts
    changes = {}
    if template.target_pct is not None:
        changes["target"] = template.target_pct / 100
    if template.max_error is not None:
        changes["max_error"] = template.max_error
    if template.max_error_pct is not None:
        changes["max_error_pct_span"] = template.max_error_pct
    return replace(opts, **changes) if changes else opts


def tune_with_template(tag, series, current: PointConfig, opts, info: dict | None, templates: list[Template],
                       moments=()):
    """Tune one point, with the matching template's overrides, baseline and notes attached to the result."""
    from .tune import tune_point
    t = match(templates, info) if info else None
    result = tune_point(tag, series, current, tune_options_for(t, opts), moments=moments)
    if t is None:
        return result
    result.template = t.name
    result.baseline = t.baseline(current) if t.has_settings() else None
    notes = [f"Matched the “{t.name}” template" + (f": {t.summary()}." if t.summary() else ".")]
    b = result.baseline
    if b is not None:
        if t.compressing is not None and current.compressing != t.compressing:
            notes.append(f"The template has compression {'on' if t.compressing else 'off'} for this kind of point; "
                         f"it is {'on' if current.compressing else 'off'} now.")
        if t.step is not None and current.step != t.step:
            notes.append(f"The template has Step {'on' if t.step else 'off'}; it is {'on' if current.step else 'off'} now.")
        for k, label in (("compmax", "CompMax"), ("excmax", "ExcMax")):
            if getattr(t, k) is not None and not math.isclose(getattr(current, k), getattr(b, k)):
                notes.append(f"{label} is {_duration(getattr(current, k))}; the template suggests {_duration(getattr(b, k))}.")
    if t.note:
        notes.append(t.note)
    result.notes = notes + result.notes
    return result


# --- starter list ------------------------------------------------------------
# Values follow published PI guidance: ExcDev at half of CompDev, CompMax of one 8-hour shift,
# ExcMax of 10 minutes, 1-2% of span for flows and levels, unit-based deviations for temperature,
# pressure and electrical signals, Step on and compression off for lab and manual entries, and
# record-every-change for digital states. See docs/templates.md.

_SHIFT, _TEN_MIN = 28800.0, 600.0


def starter_templates() -> list[Template]:
    T = Template
    return [
        T("Digital and status", priority=10, match_pointtype=["digital"],
          compdev=0, excdev=0, compmax=86400, excmax=28800,
          note="Digital states are kept on every change; tuning CompDev does not apply."),
        T("Lab and manual entries", priority=20,
          match_descriptor=["lab", "laboratory", "sample result", "manual", "manually entered", "hand entered"],
          compressing=False, step=True, excdev=0, compdev=0,
          note="Lab and manual values arrive rarely and each one matters, so keep every value."),
        T("Setpoints and operator inputs", priority=30,
          match_tag=["*.SP", "*_SP", "*.SV", "*.TARGET"], match_descriptor=["setpoint", "set point", "target"],
          match_any=True,
          step=True, compdev=0, excdev=0, compmax=_SHIFT, excmax=_TEN_MIN,
          note="Setpoints change in steps when someone changes them; keep every change."),
        T("Totalizers and counters", priority=40,
          match_descriptor=["total", "totalizer", "totaliser", "accumulated", "counter", "count"],
          compdev_pct=0.1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Valve and controller output", priority=50,
          match_descriptor=["valve", "position", "output", "opening", "damper"], match_units=["%"],
          compdev=0.2, excdev=0.1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("pH", priority=60, match_units=["pH"], compdev=0.1, excdev=0.05, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Temperature, °F", priority=60, match_units=["degF", "F", "°F"],
          compdev=0.5, excdev=0.25, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Temperature, °C or K", priority=60, match_units=["degC", "C", "°C", "K"],
          compdev=0.3, excdev=0.15, compmax=_SHIFT, excmax=_TEN_MIN,
          note="0.3 °C is the °F template's 0.5 °F expressed in °C."),
        T("Pressure, psi", priority=60, match_units=["psi", "psig", "psia", "psid"],
          compdev=0.5, excdev=0.25, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Pressure, other units", priority=65,
          match_units=["bar", "barg", "bara", "mbar", "kPa", "kPag", "kPaa", "MPa", "Pa", "inH2O", "inWC", "inHg",
                       "mmH2O", "mmHg", "kg/cm2", "kg/cm2g", "torr", "oz/sqin"],
          compdev_pct=1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Flow", priority=70, match_any=True, match_descriptor=["flow", "flowrate", "flow rate"],
          match_units=["gpm", "gph", "lb/h", "lb/hr", "klb/h", "klb/hr", "kg/h", "kg/hr", "kg/s", "t/h", "m3/h", "m3/hr",
                       "nm3/h", "scfm", "scfh", "mscfd", "mmscfd", "bpd", "bbl/d", "l/min", "l/s", "lpm", "acfm"],
          compdev_pct=1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Level", priority=70, match_descriptor=["level", "lvl"],
          compdev_pct=1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Electrical power", priority=80, match_units=["MW", "kW", "MVAR", "kVAR", "MVA", "kVA"],
          compdev=0.2, excdev=0.1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Electrical current", priority=80, match_units=["A", "amps", "amp"],
          compdev=1, excdev=0.5, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Voltage", priority=80, match_units=["V", "volts"],
          compdev=2, excdev=1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Rotational speed", priority=80, match_units=["rpm"],
          compdev=2, excdev=1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Vibration", priority=80, match_units=["mils", "mil", "mils pk", "um", "in/s", "mm/s", "ips"],
          compdev_pct=1, compmax=_SHIFT, excmax=_TEN_MIN),
        T("Analyzer and composition", priority=85,
          match_units=["ppm", "ppb", "mol%", "%LEL", "%vol", "wt%", "Btu/scf", "mg/l", "ntu", "uS/cm"],
          compdev_pct=0.5, compmax=_SHIFT, excmax=_TEN_MIN,
          note="No published per-unit value for analyzers; 0.5% of span is a cautious start."),
        T("Any other analog point", priority=1000, enabled=False,
          match_pointtype=["float16", "float32", "float64", "int16", "int32"],
          compdev_pct=0.5, compmax=_SHIFT, excmax=_TEN_MIN,
          note="Catch-all, off by default: a single setting for every point is what tuning replaces."),
    ]
