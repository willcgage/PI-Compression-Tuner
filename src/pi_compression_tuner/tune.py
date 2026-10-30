"""Find recommended settings for a point and classify its current configuration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from enum import Enum

import numpy as np
import pandas as pd

from .metrics import Score, estimate_noise, retention_score, reconstruct
from .moments import find_spikes, moment_index, smooth
from .models import PointConfig, Series
from .simulate import simulate_archive

SECONDS_PER_DAY = 86400.0


class Classification(str, Enum):
    ON_TARGET = "On target"
    STORING_NOISE = "Storing noise"
    LOSING_DETAIL = "Losing detail"
    LITTLE_TO_GAIN = "Little to gain"
    RANGE_MISMATCH = "Range mismatch"
    NOT_MOVING = "Not moving"
    TOO_FEW_EVENTS = "Too few events"


@dataclass(frozen=True)
class TuneOptions:
    """Knobs for the recommendation search.

    ``input_mode`` says what the data is:
      * ``"snapshot"``: the snapshot stream, already filtered by the interface's exception
        reporting. Only compression is simulated and ExcDev is left as configured.
      * ``"raw"``: every value the interface read, before exception. Both stages are
        simulated and ExcDev is tuned with CompDev. ExcDev is ``excdev_ratio`` x CompDev; when
        ``excdev_ratio`` is None, each ratio in ``excdev_ratios`` is tried and the one that
        meets the target with the fewest archive events wins. ExcDev is never set below
        ``excdev_noise_floor`` x the measured noise (capped at CompDev), so the interface
        does not pass noise.

    ``robust_margin``: a refined CompDev is kept only if a setting this much tighter also
    meets the target, so a proposal never sits in a narrow passing pocket of a bumpy score.
    """

    target: float = 0.95
    input_mode: str = "snapshot"
    window_s: float = 3600.0
    percentile: float = 10.0
    noise_band: float = 2.0
    excdev_ratio: float | None = None
    excdev_ratios: tuple = (0.1, 0.25, 0.5, 0.75, 1.0)
    excdev_noise_floor: float = 1.0
    robust_margin: float = 0.9
    moment_range_pct: float = 2.0
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
    moments_missed: int = 0  # key moments the archive misses by more than the tolerance
    moments_total: int = 0
    worst_at: int = -1       # index of the largest error

    @property
    def compression_ratio(self) -> float:
        """Input events per archived event, the usual compression ratio in the literature."""
        return 1.0 / self.archive_ratio if self.archive_ratio > 0 else math.inf


@dataclass
class TuneResult:
    tag: str
    classification: Classification
    current: Evaluation | None
    recommended: Evaluation | None
    noise: float
    input_events: int
    ready_to_export: bool = False
    notes: list = field(default_factory=list)
    moments: tuple = ()  # key moment timestamps the result was tuned to keep
    spikes: int = 0      # isolated one-event spikes in the input

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
    def __init__(self, series: Series, opts: TuneOptions, noise: float, step: bool,
                 moments=(), moment_tol: float | None = None):
        self.series = series
        self.opts = opts
        self.noise = noise
        self.step = step
        self.days = max(series.duration, 1.0) / SECONDS_PER_DAY
        self.moment_idx = moment_index(series, moments)
        self.moment_tol = opts.noise_band * noise if moment_tol is None else moment_tol
        self.smoothed = smooth(series.v) if len(self.moment_idx) else None

    def __call__(self, config: PointConfig) -> Evaluation:
        s = self.series
        sim = simulate_archive(s, config, apply_exception=self.opts.input_mode == "raw")
        vis = sim.visible()
        recon = reconstruct(s.t[vis], s.v[vis], s.t, step=self.step)
        score = retention_score(s, recon, self.noise, self.opts.window_s, self.opts.percentile, self.opts.noise_band)
        n_arch = len(sim.archived)
        err = np.abs(s.v - recon)
        missed = 0
        if len(self.moment_idx):
            # Kept when the archive lands near the event itself or near the smoothed signal there.
            at = recon[self.moment_idx]
            miss = np.minimum(err[self.moment_idx], np.abs(self.smoothed[self.moment_idx] - at))
            missed = int(np.sum(miss > self.moment_tol * (1 + 1e-9)))
        return Evaluation(config, n_arch, n_arch / self.days, n_arch / max(len(s), 1), score,
                          missed, len(self.moment_idx), int(np.argmax(err)) if len(err) else -1)


def evaluate_config(series: Series, config: PointConfig, opts: TuneOptions = TuneOptions(),
                    noise: float | None = None, moments=()) -> Evaluation:
    """Score one set of settings against a series, for what-if checks of custom values."""
    if noise is None:
        noise = estimate_noise(series.v)
    return _Evaluator(series, opts, noise, config.step, moments, _moment_tol(opts, noise, config.span, float(np.ptp(series.v)) if len(series) else 0.0))(config)


def tradeoff_curve(series: Series, current: PointConfig, opts: TuneOptions = TuneOptions(), noise: float | None = None,
                   points: int = 18, excdev_ratio: float | None = None) -> list[Evaluation]:
    """Retention and archive volume across a log-spaced sweep of CompDev, for plotting the trade-off.

    The sweep runs from a quarter of the noise to 40 times it (or to 5% of span, if larger),
    with ExcDev handled as in :func:`tune_point`. In raw mode ExcDev follows ``excdev_ratio``
    (pass the proposal's ratio), else ``opts.excdev_ratio``, else 0.5.
    """
    if noise is None:
        noise = estimate_noise(series.v)
    if noise <= 0 or len(series) < 2:
        return []
    hi = max(40 * noise, 0.05 * current.span if current.span > 0 else 0)
    lo = max(0.25 * noise, current.excdev if opts.input_mode == "snapshot" else 0)
    evaluate = _Evaluator(series, opts, noise, current.step)
    ratio = excdev_ratio if excdev_ratio is not None else (opts.excdev_ratio if opts.excdev_ratio is not None else 0.5)
    out = []
    for c in np.geomspace(max(lo, 1e-12), max(hi, lo * 1.01), points):
        excdev = _raw_excdev(float(c), ratio, noise, opts) if opts.input_mode == "raw" else current.excdev
        out.append(evaluate(replace(current, excdev=excdev, compdev=float(c), compressing=True)))
    return out


def _raw_excdev(compdev: float, ratio: float, noise: float, opts: TuneOptions) -> float:
    """ExcDev for a raw-mode candidate: ratio x CompDev, at least the noise floor, at most CompDev."""
    return min(max(ratio * compdev, opts.excdev_noise_floor * noise), compdev)


def _error_limit(opts: TuneOptions, span: float) -> float:
    limits = []
    if opts.max_error is not None:
        limits.append(opts.max_error)
    if opts.max_error_pct_span is not None and span > 0:
        limits.append(opts.max_error_pct_span / 100.0 * span)
    return min(limits) if limits else math.inf


def _moment_tol(opts: TuneOptions, noise: float, span: float, observed_range: float) -> float:
    """How far the archive may miss a key moment, measured against the smoothed signal.

    One noise width past the noise band, or ``moment_range_pct`` of the observed range if
    larger, so a peak may lose its noisy tip but not its height. A tighter error limit wins.
    """
    tol = max((opts.noise_band + 1) * noise, opts.moment_range_pct / 100.0 * observed_range)
    return min(tol, _error_limit(opts, span))


def tune_point(tag: str, series: Series, current: PointConfig, opts: TuneOptions = TuneOptions(),
               moments=()) -> TuneResult:
    """Evaluate the current settings for one point and search for recommended ones.

    ``moments`` are timestamps (seconds since the epoch) of key moments: events the
    proposed archive must reproduce within the noise band (or the error limit, if
    tighter). Each snaps to the nearest input event.
    """
    n = len(series)
    noise = estimate_noise(series.v) if n else 0.0
    notes: list[str] = []
    moments = tuple(float(series.t[i]) for i in moment_index(series, moments)) if n else ()

    if n < opts.min_events:
        return TuneResult(tag, Classification.TOO_FEW_EVENTS, None, None, noise, n,
                          notes=[f"Only {n} events in the window; at least {opts.min_events} are needed."])
    if np.ptp(series.v) == 0 or noise == 0:
        return TuneResult(tag, Classification.NOT_MOVING, None, None, noise, n,
                          notes=["The value never changes in this window. Check the instrument or interface."])

    evaluate = _Evaluator(series, opts, noise, current.step, moments, _moment_tol(opts, noise, current.span, float(np.ptp(series.v))))
    current_eval = evaluate(current)
    limit = _error_limit(opts, current.span)
    spikes = find_spikes(series.v, noise)

    def feasible(ev: Evaluation) -> bool:
        return ev.score.retention >= opts.target and ev.score.max_error <= limit and ev.moments_missed == 0

    # Candidate CompDev values: multiples of the measured noise, plus common span percentages.
    cands = {noise * m for m in opts.noise_multiples}
    if current.span > 0:
        cands |= {current.span * p / 100.0 for p in opts.span_percents}
    lo_bound = current.excdev if opts.input_mode == "snapshot" else 0.0
    cands = sorted(c for c in cands if c > 0 and c >= lo_bound * 0.999)
    if not cands:
        cands = [max(lo_bound, noise)]

    def search(ratio: float | None):
        """Loosest robust CompDev that meets the target at one ExcDev ratio.

        Returns (evaluation or None, CompDev of a looser passing setting beyond a failing gap or None).
        """
        def config_for(compdev: float) -> PointConfig:
            excdev = _raw_excdev(compdev, ratio, noise, opts) if opts.input_mode == "raw" else current.excdev
            return replace(current, excdev=excdev, compdev=compdev, compressing=True)

        evals = [(c, evaluate(config_for(c))) for c in cands]
        passing = [feasible(ev) for _, ev in evals]
        if not any(passing):
            return None, None
        # The score is not always smooth in CompDev, so stay in the first passing run of the
        # grid and only report passing settings beyond a failing gap.
        best = passing.index(True)
        while best + 1 < len(evals) and passing[best + 1]:
            best += 1
        beyond = [c for (c, _), ok in zip(evals[best + 1:], passing[best + 1:]) if ok]
        grid_c, grid_ev = evals[best]
        lo_c, lo_ev = grid_c, grid_ev
        if best + 1 < len(evals):
            hi_c = evals[best + 1][0]
            for _ in range(opts.refine_steps):
                mid = math.sqrt(lo_c * hi_c)
                ev = evaluate(config_for(mid))
                if feasible(ev):
                    lo_c, lo_ev = mid, ev
                else:
                    hi_c = mid
            # Keep the refined value only if a slightly tighter setting passes too.
            if lo_c != grid_c and opts.robust_margin * lo_c > grid_c \
                    and not feasible(evaluate(config_for(opts.robust_margin * lo_c))):
                lo_c, lo_ev = grid_c, grid_ev
        # Round to a readable value, but only keep it if it still meets the target.
        found = lo_ev
        for sig in (2, 3):
            rc = _floor_sig(lo_c, sig)
            if rc <= 0 or rc == lo_c:
                break
            cfg = config_for(rc)
            if opts.input_mode == "raw":
                cfg = cfg.with_deviations(min(_round_sig(cfg.excdev, sig), rc), rc)
            ev = evaluate(cfg)
            if feasible(ev):
                found = ev
                break
        return found, (max(beyond) if beyond else None)

    if opts.input_mode == "raw":
        ratios = (opts.excdev_ratio,) if opts.excdev_ratio is not None else opts.excdev_ratios
    else:
        ratios = (None,)
    recommended, beyond, best_ratio = None, None, None
    for ratio in ratios:
        ev, bey = search(ratio)
        if ev is not None and (recommended is None
                               or (ev.archived_events, -ev.score.retention)
                               < (recommended.archived_events, -recommended.score.retention)):
            recommended, beyond, best_ratio = ev, bey, ratio
    if beyond is not None:
        notes.append(f"The score is not smooth for this point: CompDev {beyond:.3g} also meets the target, "
                     "but settings between it and the proposal do not, so the proposal stays below that gap.")

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
        cls = Classification.LITTLE_TO_GAIN
        notes.append(f"No tested setting reaches {opts.target:.0%} retention"
                     + ("" if math.isinf(limit) else f" within the {limit:g} error limit")
                     + (" while keeping every key moment" if moments else "") + ".")
    elif zero_span_issue:
        cls = Classification.RANGE_MISMATCH
    elif recommended.archive_ratio > opts.hard_archive_ratio:
        cls = Classification.LITTLE_TO_GAIN
        notes.append(f"Meeting the target keeps {recommended.archive_ratio:.0%} of events, so compression gains little.")
    elif not feasible(current_eval):
        cls = Classification.LOSING_DETAIL
    elif current_eval.archived_events > opts.under_ratio * max(recommended.archived_events, 1):
        cls = Classification.STORING_NOISE
    else:
        cls = Classification.ON_TARGET
        recommended = current_eval

    if opts.input_mode == "snapshot":
        notes.append("Scored against the snapshot stream, so ExcDev was not tuned.")
        if recommended is not None and recommended.config.compdev < 2 * current.excdev:
            notes.append("Recommended CompDev is under twice ExcDev; a raw capture could show whether ExcDev should come down too.")
    if opts.input_mode == "raw" and recommended is not None and recommended is not current_eval:
        if len(ratios) > 1:
            notes.append(f"ExcDev and CompDev were tuned together; ExcDev at {best_ratio:g} × CompDev "
                         "kept the fewest events.")
        if recommended.config.excdev > best_ratio * recommended.config.compdev * 1.001:
            notes.append(f"ExcDev was raised to the measured noise ({noise:.3g}) so the interface does not pass noise.")
    if opts.input_mode == "raw" and current.excdev < 0.5 * noise:
        notes.append(f"Current ExcDev ({current.excdev:g}) is under half the measured noise ({noise:.3g}), "
                     "so the interface passes noise on to the server.")
    if not current.compressing:
        notes.append("Compression is currently off for this point.")
    if moments:
        if current_eval.moments_missed:
            notes.append(f"Current settings miss {current_eval.moments_missed} of {len(moments)} key moments.")
        if recommended is not None:
            notes.append(f"The proposal keeps all {len(moments)} key moments.")
    if len(spikes):
        notes.append(f"{len(spikes)} isolated spike{'s' if len(spikes) > 1 else ''} (one event jumping "
                     "6 or more noise widths and straight back). Check whether they are real events or "
                     "instrument faults; compression usually archives them.")
    judged = recommended or current_eval
    if len(spikes) and judged.worst_at in set(spikes.tolist()):
        when = pd.to_datetime(series.t[judged.worst_at], unit="s", utc=True).strftime("%Y-%m-%d %H:%M:%S")
        notes.append(f"The largest error is at an isolated spike ({when} UTC). Check whether it is a real event "
                     "or an instrument fault; mark it as a key moment to keep it.")

    safe = cls in (Classification.STORING_NOISE, Classification.LOSING_DETAIL)
    return TuneResult(tag, cls, current_eval, recommended, noise, n, safe, notes, moments, len(spikes))
