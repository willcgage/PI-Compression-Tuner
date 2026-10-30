import numpy as np

from pi_compression_tuner import PointConfig, Series, evaluate_config, tune_point
from pi_compression_tuner.moments import (alarm_crossings, find_spikes, implied_compdev, moments_frame, read_moments,
                                          suggest_moments)
from pi_compression_tuner.report import write_report
from pi_compression_tuner.sources.csvfiles import read_configs
from pi_compression_tuner.tune import TuneOptions


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


def test_alarm_crossings_are_found_once_each():
    s = _wave()
    # 50 + 10 sin over four 6-hour cycles crosses 55 going up at 0.5 h + 6k h and down at 2.5 h + 6k h.
    c = alarm_crossings(s, (55.0,), 0.05)
    expected = np.sort(np.concatenate([np.arange(4) * 6 + 0.5, np.arange(4) * 6 + 2.5])) * 3600
    assert len(c) == len(expected)
    assert np.all(np.abs(c - expected) < 120)
    assert len(alarm_crossings(s, (75.0,), 0.05)) == 0  # never reached


def test_a_signal_sitting_on_a_limit_is_not_marked_over_and_over():
    rng = np.random.default_rng(0)
    t = np.arange(0.0, 86400.0, 10.0)
    s = Series(t, 55 + rng.normal(0, 0.05, len(t)))
    assert len(alarm_crossings(s, (55.0,), 0.05)) == 0


def test_suggest_adds_alarm_crossings():
    s = _wave()
    plain = suggest_moments(s, 0.05)
    with_limits = suggest_moments(s, 0.05, limits=(55.0,))
    assert len(with_limits) == len(plain) + 8
    assert set(plain.tolist()) <= set(with_limits.tolist())


def test_implied_compdev_is_the_average_bulge_between_moments():
    t = np.arange(0.0, 3601.0, 10.0)
    # A triangle from 0 up to 10 and back: the straight line between the ends misses the apex by 10.
    v = np.where(t <= 1800, t / 180, (3600 - t) / 180)
    s = Series(t, v)
    assert abs(implied_compdev(s, [0.0, 3600.0]) - 10) < 0.2
    assert implied_compdev(s, [0.0, 1800.0, 3600.0]) < 0.2  # moments at the corners: nothing in between
    assert implied_compdev(s, [0.0]) is None


def test_moment_compdev_is_reported():
    s = _wave(noise=0.2)
    r = tune_point("A", s, PointConfig(compdev=0.001, span=100), moments=suggest_moments(s, 0.2))
    assert r.moment_compdev is not None and r.moment_compdev > 0
    assert any("imply CompDev" in n for n in r.notes)


def test_alarm_limits_are_read_from_settings(tmp_path):
    path = tmp_path / "config.csv"
    path.write_text("Name,CompDev,Lo,Hi,HiHi\nA,1,20,80,95\nB,1,,,\n")
    cfg = read_configs(path)
    assert cfg["A"].alarm_limits == (20.0, 80.0, 95.0)
    assert cfg["B"].alarm_limits == ()
    path.write_text("Name,CompDev,AlarmLimits\nA,1,20; 80\n")
    assert read_configs(path)["A"].alarm_limits == (20.0, 80.0)


def test_report_marks_key_moments_and_alarm_limits(tmp_path):
    s = _wave()
    cfg = PointConfig(compdev=0.001, span=100, alarm_limits=(55.0,))
    r = tune_point("A", s, cfg, moments=suggest_moments(s, 0.05, limits=cfg.alarm_limits))
    write_report([r], {"A": s}, TuneOptions(), tmp_path / "report.html")
    doc = (tmp_path / "report.html").read_text()
    assert doc.count('stroke="var(--mark)" stroke-width="2"') >= len(r.moments)
    assert 'stroke-dasharray="5 4"' in doc
    assert "Key moment" in doc and "Alarm limit" in doc
