import numpy as np

from pi_compression_tuner import Classification, PointConfig, Series, TuneOptions, tune_point
from pi_compression_tuner.export import results_frame


def _sine(days=1, noise=0.05, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, days * 86400, 10.0)
    return Series(t, 50 + 10 * np.sin(2 * np.pi * t / 21600) + rng.normal(0, noise, len(t)))


def test_tight_settings_are_undercompressed():
    r = tune_point("A", _sine(), PointConfig(compdev=0.001, span=100))
    assert r.classification == Classification.STORING_NOISE
    assert r.recommended.config.compdev > 0.001
    assert r.recommended.score.retention >= 0.95
    assert r.ready_to_export and r.changes


def test_wide_settings_are_overcompressed():
    r = tune_point("A", _sine(), PointConfig(compdev=8, span=100))
    assert r.classification == Classification.LOSING_DETAIL
    assert r.recommended.config.compdev < 8
    assert r.recommended.archived_events > r.current.archived_events


def test_recommendation_meets_target_and_error_limit():
    opts = TuneOptions(target=0.99, max_error=0.2)
    r = tune_point("A", _sine(), PointConfig(compdev=0.001, span=100), opts)
    assert r.recommended.score.retention >= 0.99
    assert r.recommended.score.max_error <= 0.2


def test_raw_mode_recommends_excdev_from_ratio():
    opts = TuneOptions(input_mode="raw", excdev_ratio=0.5)
    r = tune_point("A", _sine(), PointConfig(excdev=0, compdev=0.001, span=100), opts)
    cfg = r.recommended.config
    assert abs(cfg.excdev - cfg.compdev / 2) <= 0.06 * cfg.compdev


def test_flatline_and_short_series():
    t = np.arange(0.0, 1000.0, 1.0)
    assert tune_point("F", Series(t, np.ones_like(t)), PointConfig()).classification == Classification.NOT_MOVING
    assert tune_point("S", Series(t[:10], t[:10]), PointConfig()).classification == Classification.TOO_FEW_EVENTS


def test_values_outside_span_are_flagged():
    r = tune_point("Z", _sine(), PointConfig(compdev=0.001, zero=0, span=10))
    assert r.classification == Classification.RANGE_MISMATCH
    assert not r.ready_to_export


def test_white_noise_is_hard_to_compress():
    rng = np.random.default_rng(3)
    t = np.arange(0.0, 86400.0, 10.0)
    s = Series(t, np.cumsum(rng.normal(0, 1, len(t))) * 0 + rng.normal(50, 5, len(t)) * (1 + np.sin(t / 1000)))
    r = tune_point("N", s, PointConfig(compdev=0.01, span=200))
    assert r.classification in (Classification.LITTLE_TO_GAIN, Classification.STORING_NOISE)


def test_raw_mode_searches_excdev_ratio():
    fixed = tune_point("A", _sine(noise=0.3), PointConfig(compdev=0.001, span=100),
                       TuneOptions(input_mode="raw", excdev_ratio=0.5))
    searched = tune_point("A", _sine(noise=0.3), PointConfig(compdev=0.001, span=100), TuneOptions(input_mode="raw"))
    assert searched.recommended.score.retention >= 0.95
    assert searched.recommended.archived_events <= fixed.recommended.archived_events
    assert any("tuned together" in n for n in searched.notes)


def test_raw_mode_excdev_never_below_noise():
    r = tune_point("A", _sine(noise=0.3), PointConfig(compdev=0.001, span=100),
                   TuneOptions(input_mode="raw", excdev_ratio=0.01))
    cfg = r.recommended.config
    assert cfg.excdev >= min(0.9 * r.noise, cfg.compdev)
    assert cfg.excdev <= cfg.compdev


def test_proposal_stays_below_a_failing_gap(monkeypatch):
    """A passing pocket at a looser CompDev must not pull the proposal past settings that fail."""
    import pi_compression_tuner.tune as tune
    from pi_compression_tuner.metrics import Score

    real = tune._Evaluator.__call__

    def bumpy(self, config):
        ev = real(self, config)
        # Pretend the score passes below 1.0, fails from 1.0 to 3.0, then passes again.
        ret = 0.99 if config.compdev < 1.0 or config.compdev > 3.0 else 0.5
        return tune.Evaluation(ev.config, ev.archived_events, ev.events_per_day, ev.archive_ratio,
                               Score(ret, ret, 0.0, 0.0, 1))

    monkeypatch.setattr(tune._Evaluator, "__call__", bumpy)
    r = tune_point("A", _sine(), PointConfig(compdev=0.001, span=100))
    assert r.recommended.config.compdev < 1.0
    assert any("not smooth" in n for n in r.notes)


def test_results_report_compression_ratio_and_rms_error():
    r = tune_point("A", _sine(), PointConfig(compdev=0.001, span=100))
    assert np.isclose(r.recommended.compression_ratio, len(_sine()) / r.recommended.archived_events)
    row = results_frame([r]).iloc[0]
    assert row["RecommendedCompressionRatio"] > row["CurrentCompressionRatio"]
    assert row["RecommendedRMSError"] > 0


def test_search_reaches_past_the_noise_grid_when_the_signal_jumps():
    # Mostly tiny steps (small measured noise) with frequent large jumps, on a range far outside
    # Zero/Span. The noise multiples alone top out near 32x noise while retention is still 100%.
    rng = np.random.default_rng(3)
    n = 6000
    d = np.where(rng.random(n) < 0.7, rng.normal(0, 1, n), rng.normal(0, 600, n))
    s = Series(np.arange(n) * 60.0, np.round(138000 + np.cumsum(d)))
    r = tune_point("IOR", s, PointConfig(excdev=0.1, compdev=0.2, zero=0, span=100), TuneOptions(target=0.96))
    assert r.noise < 2
    assert r.recommended.config.compdev > 40 * r.noise
    assert 0.96 <= r.recommended.score.retention < 0.99


def test_range_mismatch_proposes_zero_and_span():
    from pi_compression_tuner.tune import suggest_zero_span

    assert suggest_zero_span(1038, 1084) == (0.0, 1200.0)
    assert suggest_zero_span(25.25, 161.6) == (0.0, 200.0)
    assert suggest_zero_span(-12, 40) == (-15.0, 65.0)
    rng = np.random.default_rng(1)
    t = np.arange(0.0, 86400, 6.0)
    s = Series(t, 1060 + 20 * np.sin(2 * np.pi * t / 7200) + rng.normal(0, 0.5, len(t)))
    r = tune_point("P", s, PointConfig(excdev=0.1, compdev=0.2, span=100))
    assert r.classification == Classification.RANGE_MISMATCH and not r.ready_to_export
    cfg = r.recommended.config
    assert (cfg.zero, cfg.span) == (0.0, 1200.0) and r.changes
    assert any("Suggested Zero 0 and Span 1200" in n for n in r.notes)


def test_excdev_too_wide_suggests_a_narrower_excdev():
    from pi_compression_tuner.tune import evaluate_config

    rng = np.random.default_rng(0)
    t = np.arange(0.0, 86400, 6.0)
    s = Series(t, 14 + 0.25 * np.sin(2 * np.pi * t / 7200) + rng.normal(0, 0.005, len(t)))
    r = tune_point("E", s, PointConfig(excdev=0.1, compdev=0.2, span=100))
    assert r.classification == Classification.EXCDEV_TOO_WIDE
    assert r.recommended is None and not r.changes and not r.ready_to_export
    sg = r.suggested
    assert sg.excdev < 0.1 and sg.compdev == 2 * sg.excdev
    # On the raw signal, the suggestion keeps the detail the current ExcDev throws away.
    assert evaluate_config(s, sg, TuneOptions(input_mode="raw")).score.retention >= 0.95
    # With ExcDev already fine, nothing passing is still "Little to gain", not an ExcDev problem.
    assert tune_point("E", s, PointConfig(excdev=0.001, compdev=0.2, span=100)).classification != Classification.EXCDEV_TOO_WIDE
