import numpy as np

from pi_compression_tuner import Classification, PointConfig, Series, TuneOptions, tune_point


def _sine(days=1, noise=0.05, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, days * 86400, 10.0)
    return Series(t, 50 + 10 * np.sin(2 * np.pi * t / 21600) + rng.normal(0, noise, len(t)))


def test_tight_settings_are_undercompressed():
    r = tune_point("A", _sine(), PointConfig(compdev=0.001, span=100))
    assert r.classification == Classification.UNDERCOMPRESSED
    assert r.recommended.config.compdev > 0.001
    assert r.recommended.score.fidelity >= 0.97
    assert r.safe_to_bulk_apply and r.changes


def test_wide_settings_are_overcompressed():
    r = tune_point("A", _sine(), PointConfig(compdev=8, span=100))
    assert r.classification == Classification.OVERCOMPRESSED
    assert r.recommended.config.compdev < 8
    assert r.recommended.archived_events > r.current.archived_events


def test_recommendation_meets_target_and_error_limit():
    opts = TuneOptions(target=0.99, max_error=0.2)
    r = tune_point("A", _sine(), PointConfig(compdev=0.001, span=100), opts)
    assert r.recommended.score.fidelity >= 0.99
    assert r.recommended.score.max_error <= 0.2


def test_raw_mode_recommends_excdev_from_ratio():
    opts = TuneOptions(input_mode="raw", excdev_ratio=0.5)
    r = tune_point("A", _sine(), PointConfig(excdev=0, compdev=0.001, span=100), opts)
    cfg = r.recommended.config
    assert abs(cfg.excdev - cfg.compdev / 2) <= 0.06 * cfg.compdev


def test_flatline_and_short_series():
    t = np.arange(0.0, 1000.0, 1.0)
    assert tune_point("F", Series(t, np.ones_like(t)), PointConfig()).classification == Classification.FLATLINE
    assert tune_point("S", Series(t[:10], t[:10]), PointConfig()).classification == Classification.INSUFFICIENT_DATA


def test_values_outside_span_are_flagged():
    r = tune_point("Z", _sine(), PointConfig(compdev=0.001, zero=0, span=10))
    assert r.classification == Classification.ZERO_SPAN_ISSUE
    assert not r.safe_to_bulk_apply


def test_white_noise_is_hard_to_compress():
    rng = np.random.default_rng(3)
    t = np.arange(0.0, 86400.0, 10.0)
    s = Series(t, np.cumsum(rng.normal(0, 1, len(t))) * 0 + rng.normal(50, 5, len(t)) * (1 + np.sin(t / 1000)))
    r = tune_point("N", s, PointConfig(compdev=0.01, span=200))
    assert r.classification in (Classification.HARD_TO_COMPRESS, Classification.UNDERCOMPRESSED)
