"""Find recommended settings for a point and classify its current configuration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum

import numpy as np

from .metrics import Score, estimate_noise, fidelity_score, reconstruct
from .models import PointConfig, Series
from .simulate import simulate_archive

SECONDS_PER_DAY = 86400.0


class Classification(str, Enum):
    GOOD = "Good"
    UNDERCOMPRESSED = "Undercompressed"
    OVERCOMPRESSED = "Overcompressed"
    HARD_TO_COMPRESS = "Hard to compress"
    ZERO_SPAN_ISSUE = "Zero/span issue"
    FLATLINE = "Flatline"
    INSUFFICIENT_DATA = "Insufficient data"


@dataclass(frozen=True)
class TuneOptions:
    """Knobs for the recommendation search.

    ``input_mode`` says what the data is:
      * ``"snapshot"``: the snapshot stream, already filtered by the interface's exception
        reporting. Only compression is simulated and ExcDev is left as configured.
      * ``"raw"``: every value the interface read, before exception. Both stages are
        simulated and ExcDev is recommended as ``excdev_ratio`` x CompDev.
    """

    target: float = 0.97
    input_mode: str = "snapshot"
    window_s: float = 3600.0
    percentile: float = 10.0
    noise_band: float = 2.0
    excdev_ratio: float = 0.5
    max_error: float | None = None           # engineering units
    max_error_pct_span: float | None = None  # percent of span
    under_ratio: float = 2.0
    hard_archive_ratio: float = 0.5
    min_events: int = 50
    noise_multiples: tuple = (0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 6, 8, 12, 16, 24, 32)
    span_percents: tuple = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5)
    refine_steps: int = 6

    def __post_init__(self):
        if self.input_mode not in ("snapshot", "raw"):
            raise ValueError("input_mode must be 'snapshot' or 'raw'")
        if not 0 < self.target <= 1:
            raise ValueError("target must be in (0, 1]")


@dataclass(frozen=True)
class Evaluation:
    config: PointConfig
    archived_events: int
    events_per_day: float
    archive_ratio: float  # archived events / input events
    score: Score


@dataclass
class TuneResult:
    tag: str
    classification: Classification
    current: Evaluation | None
    recommended: Evaluation | None
    noise: float
    input_events: int
    safe_to_bulk_apply: bool = False
    notes: list = field(default_factory=list)

    @property
    def changes(self) -> bool:
        if self.recommended is None or self.current is None:
            return False
        a, b = self.current.config, self.recommended.config
        return not (math.isclose(a.compdev, b.compdev) and math.isclose(a.excdev, b.excdev) and a.compressing == b.compressing)


def _floor_sig(x: float, sig: int = 2) -> float:
    """Round down to ``sig`` significant figures, so a rounded recommendation never loosens the setting."""
    if x <= 0 or not math.isfinite(x):
        return 0.0
    digits = sig - 1 - math.floor(math.log10(x))
    mag = 10.0 ** -digits
    return round(math.floor(x / mag + 1e-9) * mag, max(digits, 0))


def _round_sig(x: float, sig: int = 2) -> float:
    if x <= 0 or not math.isfinite(x):
        return 0.0
    return round(x, sig - 1 - math.floor(math.log10(x)))


class _Evaluator:
    def __init__(self, series: Series, opts: TuneOptions, noise: float, step: bool):
        self.series = series
        self.opts = opts
        self.noise = noise
        self.step = step
        self.days = max(series.duration, 1.0) / SECONDS_PER_DAY

    def __call__(self, config: PointConfig) -> Evaluation:
        s = self.series
        sim = simulate_archive(s, config, apply_exception=self.opts.input_mode == "raw")
        vis = sim.visible()
        recon = reconstruct(s.t[vis], s.v[vis], s.t, step=self.step)
        score = fidelity_score(s, recon, self.noise, self.opts.window_s, self.opts.percentile, self.opts.noise_band)
        n_arch = len(sim.archived)
        return Evaluation(config, n_arch, n_arch / self.days, n_arch / max(len(s), 1), score)


def _error_limit(opts: TuneOptions, span: float) -> float:
    limits = []
    if opts.max_error is not None:
        limits.append(opts.max_error)
    if opts.max_error_pct_span is not None and span > 0:
        limits.append(opts.max_error_pct_span / 100.0 * span)
    return min(limits) if limits else math.inf


def tune_point(tag: str, series: Series, current: PointConfig, opts: TuneOptions = TuneOptions()) -> TuneResult:
    """Evaluate the current settings for one point and search for recommended ones."""
    n = len(series)
    noise = estimate_noise(series.v) if n else 0.0
    notes: list[str] = []

    if n < opts.min_events:
        return TuneResult(tag, Classification.INSUFFICIENT_DATA, None, None, noise, n,
                          notes=[f"Only {n} events in the window; at least {opts.min_events} are needed."])
    if np.ptp(series.v) == 0 or noise == 0:
        return TuneResult(tag, Classification.FLATLINE, None, None, noise, n,
                          notes=["The value never changes in this window. Check the instrument or interface."])

    evaluate = _Evaluator(series, opts, noise, current.step)
    current_eval = evaluate(current)
    limit = _error_limit(opts, current.span)

    def feasible(ev: Evaluation) -> bool:
        return ev.score.fidelity >= opts.target and ev.score.max_error <= limit

    # Candidate CompDev values: multiples of the measured noise, plus common span percentages.
    cands = {noise * m for m in opts.noise_multiples}
    if current.span > 0:
        cands |= {current.span * p / 100.0 for p in opts.span_percents}
    lo_bound = current.excdev if opts.input_mode == "snapshot" else 0.0
    cands = sorted(c for c in cands if c > 0 and c >= lo_bound * 0.999)
    if not cands:
        cands = [max(lo_bound, noise)]

    def config_for(compdev: float) -> PointConfig:
        excdev = opts.excdev_ratio * compdev if opts.input_mode == "raw" else current.excdev
        return replace(current, excdev=excdev, compdev=compdev, compressing=True)

    evals = [(c, evaluate(config_for(c))) for c in cands]
    ok = [i for i, (_, ev) in enumerate(evals) if feasible(ev)]

    recommended = None
    if ok:
        best = max(ok)
        lo_c, lo_ev = evals[best]
        if best + 1 < len(evals):
            hi_c = evals[best + 1][0]
            for _ in range(opts.refine_steps):
                mid = math.sqrt(lo_c * hi_c)
                ev = evaluate(config_for(mid))
                if feasible(ev):
                    lo_c, lo_ev = mid, ev
                else:
                    hi_c = mid
        # Round to a readable value, but only keep it if it still meets the target.
        recommended = lo_ev
        for sig in (2, 3):
            rc = _floor_sig(lo_c, sig)
            if rc <= 0 or rc == lo_c:
                break
            cfg = config_for(rc)
            if opts.input_mode == "raw":
                cfg = cfg.with_deviations(_round_sig(cfg.excdev, sig), rc)
            ev = evaluate(cfg)
            if feasible(ev):
                recommended = ev
                break

    zero_span_issue = current.span <= 0
    if current.span > 0:
        lo, hi = current.zero - 0.1 * current.span, current.zero + 1.1 * current.span
        outside = np.mean((series.v < lo) | (series.v > hi))
        if outside > 0.01:
            zero_span_issue = True
            notes.append(f"{outside:.0%} of values fall outside Zero/Span ({current.zero:g} to {current.zero + current.span:g}).")
    if zero_span_issue and current.span <= 0:
        notes.append("Span is zero or negative, so percent settings are meaningless.")

    if recommended is None:
        cls = Classification.HARD_TO_COMPRESS
        notes.append(f"No tested setting reaches {opts.target:.0%} fidelity"
                     + ("" if math.isinf(limit) else f" within the {limit:g} error limit") + ".")
    elif zero_span_issue:
        cls = Classification.ZERO_SPAN_ISSUE
    elif recommended.archive_ratio > opts.hard_archive_ratio:
        cls = Classification.HARD_TO_COMPRESS
        notes.append(f"Meeting the target keeps {recommended.archive_ratio:.0%} of events, so compression gains little.")
    elif not feasible(current_eval):
        cls = Classification.OVERCOMPRESSED
    elif current_eval.archived_events > opts.under_ratio * max(recommended.archived_events, 1):
        cls = Classification.UNDERCOMPRESSED
    else:
        cls = Classification.GOOD
        recommended = current_eval

    if opts.input_mode == "snapshot":
        notes.append("Scored against the snapshot stream, so ExcDev was not tuned.")
        if recommended is not None and recommended.config.compdev < 2 * current.excdev:
            notes.append("Recommended CompDev is under twice ExcDev; a raw capture could show whether ExcDev should come down too.")
    if not current.compressing:
        notes.append("Compression is currently off for this point.")

    safe = cls in (Classification.UNDERCOMPRESSED, Classification.OVERCOMPRESSED)
    return TuneResult(tag, cls, current_eval, recommended, noise, n, safe, notes)
