import numpy as np

from pi_compression_tuner import PointConfig, Series, evaluate_config, tune_point
from pi_compression_tuner.moments import find_spikes, moments_frame, read_moments, suggest_moments


def _wave(noise=0.05, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(0.0, 86400.0, 10.0)
    return Series(t, 50 + 10 * np.sin(2 * np.pi * t / 21600) + rng.normal(0, noise, len(t)))


def test_isolated_spikes_are_found_but_not_steps_or_noise():
    s = _wave()
    v = s.v.copy()
    v[1000] += 3.0
    v[5000:] += 3.0  # a step is not a spike
    assert find_spikes(v, 0.05).tolist() == [1000]
    assert len(find_spikes(s.v, 0.05)) == 0


def test_spikes_are_reported():
    s = _wave()
    v = s.v.copy()
    v[[1000, 3000]] += 3.0
    r = tune_point("A", Series(s.t, v), PointConfig(compdev=0.001, span=100))
    assert r.spikes == 2
    assert any("isolated spikes" in n for n in r.notes)


def test_suggested_moments_are_the_peaks_and_valleys():
    s = _wave()
    m = suggest_moments(s, 0.05)
    # Four 6-hour cycles: peaks at 1.5 h + 6k h, valleys at 4.5 h + 6k h.
    expected = np.array([1.5, 4.5, 7.5, 10.5, 13.5, 16.5, 19.5, 22.5]) * 3600
    assert len(m) == len(expected)
    assert np.all(np.abs(m - expected) < 900)


def test_key_moments_tighten_the_proposal():
    s = _wave(noise=0.2)
    cfg = PointConfig(compdev=0.001, span=100)
    plain = tune_point("A", s, cfg)
    moments = suggest_moments(s, plain.noise)
    assert evaluate_config(s, plain.recommended.config, moments=moments, noise=plain.noise).moments_missed > 0
    kept = tune_point("A", s, cfg, moments=moments)
    assert kept.recommended.moments_missed == 0
    assert kept.moments == tuple(moments)
    assert kept.recommended.config.compdev < plain.recommended.config.compdev
    assert any(f"keeps all {len(moments)} key moments" in n for n in kept.notes)


def test_current_settings_that_miss_a_moment_are_losing_detail():
    s = _wave()
    moments = suggest_moments(s, 0.05)
    r = tune_point("A", s, PointConfig(compdev=3.0, span=100), moments=moments)
    assert r.current.moments_missed > 0
    assert r.recommended.moments_missed == 0
    assert any("miss" in n and "key moments" in n for n in r.notes)


def test_moments_round_trip_through_csv(tmp_path):
    moments = {"A": np.array([1767225600.0, 1767229200.0]), "B": np.array([1767232800.0])}
    path = tmp_path / "moments.csv"
    moments_frame(moments).to_csv(path, index=False)
    back = read_moments(path)
    assert set(back) == {"A", "B"}
    assert np.allclose(back["A"], moments["A"]) and np.allclose(back["B"], moments["B"])
