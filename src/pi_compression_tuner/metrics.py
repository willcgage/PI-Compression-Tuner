"""Reconstruction, noise estimation and the fidelity score.

Fidelity is our own, published metric. Neither commercial tool publishes theirs.
It is the share of the signal's variance that the archive keeps, ignoring
errors that fall inside the measured noise band, so that dropping noise does not
count as losing information:

    e_i  = x_i - r_i                          raw minus reconstructed archive
    e'_i = max(0, |e_i| - c * sigma_noise)    error beyond the noise band
    F_w  = 1 - sum(e'^2) / max(sum((x - mean x)^2), n * sigma_noise^2)

F_w is computed per time window. Windows where the signal only moves inside the
noise band (sum((x - mean x)^2) < n * (c * sigma_noise)^2) carry nothing to lose
and are skipped. The headline score is the lower of a low percentile of the
remaining window scores and the whole-series score. The window percentile matters
because a whole-series score hides detail lost during a few busy hours: slow drift
dominates the total variance.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .models import Series

_MAD_TO_SIGMA = 1.4826


def reconstruct(t_arch: np.ndarray, v_arch: np.ndarray, t_query: np.ndarray, step: bool = False) -> np.ndarray:
    """Values a PI trend would show at ``t_query``, given the archived events.

    Uses linear interpolation, or the last value held forward for step points.
    """
    if len(t_arch) == 0:
        return np.full(len(t_query), np.nan)
    if step:
        pos = np.searchsorted(t_arch, t_query, side="right") - 1
        return v_arch[np.clip(pos, 0, len(v_arch) - 1)]
    return np.interp(t_query, t_arch, v_arch)


def estimate_noise(v: np.ndarray) -> float:
    """Robust estimate of measurement noise (one standard deviation).

    Uses the median absolute deviation of first differences, which ignores slow trends
    and occasional steps. Signals that are mostly flat with occasional changes have a
    MAD of zero, so the estimate falls back to the median of the non-zero differences.
    On a snapshot stream this measures the resolution left after exception, not the
    instrument noise itself.
    """
    if len(v) < 3:
        return 0.0
    d = np.diff(v)
    mad = np.median(np.abs(d - np.median(d)))
    if mad > 0:
        return float(_MAD_TO_SIGMA * mad / np.sqrt(2))
    nz = np.abs(d[d != 0])
    if len(nz) == 0:
        return 0.0
    return float(_MAD_TO_SIGMA * np.median(nz) / np.sqrt(2))


@dataclass(frozen=True)
class Score:
    fidelity: float          # headline: low percentile of window scores
    fidelity_overall: float  # whole-series score, for reference
    max_error: float         # engineering units
    rms_error: float         # engineering units
    windows: int             # active (non-quiet) windows scored


def fidelity_score(
    raw: Series,
    recon: np.ndarray,
    noise: float,
    window_s: float = 3600.0,
    percentile: float = 10.0,
    noise_band: float = 2.0,
    min_window_events: int = 10,
) -> Score:
    """Score a reconstructed archive against the raw signal it came from."""
    x = raw.v
    e = x - recon
    abs_e = np.abs(e)
    excess = np.maximum(0.0, abs_e - noise_band * noise)
    floor_var = noise * noise

    quiet_var = (noise_band * noise) ** 2

    def window_f(sl: slice) -> tuple[float, bool]:
        xs = x[sl]
        sst = float(np.sum((xs - xs.mean()) ** 2))
        quiet = sst < len(xs) * quiet_var
        denom = max(sst, len(xs) * floor_var)
        sse = float(np.sum(excess[sl] ** 2))
        if denom <= 0:
            return (1.0 if sse == 0 else 0.0), quiet
        return max(0.0, 1.0 - sse / denom), quiet

    overall, _ = window_f(slice(None))
    scores = []
    if len(x) and window_s > 0:
        bins = np.floor((raw.t - raw.t[0]) / window_s).astype(np.int64)
        edges = np.flatnonzero(np.diff(bins)) + 1
        starts = np.concatenate(([0], edges))
        ends = np.concatenate((edges, [len(x)]))
        for s, en in zip(starts, ends):
            if en - s >= min_window_events:
                f, quiet = window_f(slice(s, en))
                if not quiet:
                    scores.append(f)
    headline = min(float(np.percentile(scores, percentile)), overall) if scores else overall
    return Score(
        fidelity=headline,
        fidelity_overall=overall,
        max_error=float(abs_e.max()) if len(abs_e) else 0.0,
        rms_error=float(np.sqrt(np.mean(e * e))) if len(e) else 0.0,
        windows=len(scores),
    )
