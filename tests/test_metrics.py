import numpy as np

from pi_compression_tuner import Series
from pi_compression_tuner.metrics import estimate_noise, fidelity_score, reconstruct


def test_noise_estimate_ignores_trend():
    rng = np.random.default_rng(0)
    t = np.arange(20000.0)
    v = 0.01 * t + rng.normal(0, 0.5, len(t))
    assert abs(estimate_noise(v) - 0.5) < 0.05


def test_noise_estimate_of_flat_signal_is_zero():
    assert estimate_noise(np.ones(100)) == 0.0


def test_noise_estimate_falls_back_for_mostly_flat_signals():
    v = np.zeros(100)
    v[50:] = 1.0
    assert estimate_noise(v) > 0


def test_reconstruct_step_holds_last_value():
    out = reconstruct(np.array([0.0, 10.0]), np.array([1.0, 2.0]), np.array([5.0, 10.0, 15.0]), step=True)
    assert out.tolist() == [1.0, 2.0, 2.0]


def test_perfect_reconstruction_scores_one():
    t = np.arange(0.0, 7200.0, 5.0)
    v = np.sin(t / 300)
    sc = fidelity_score(Series(t, v), v.copy(), noise=0.01)
    assert sc.fidelity == 1.0 and sc.max_error == 0.0


def test_losing_detail_lowers_the_score():
    t = np.arange(0.0, 7200.0, 5.0)
    v = np.sin(t / 300)
    sc = fidelity_score(Series(t, v), np.zeros_like(v), noise=0.01)
    assert sc.fidelity < 0.1


def test_noise_only_windows_are_skipped():
    rng = np.random.default_rng(1)
    t = np.arange(0.0, 3 * 3600.0, 5.0)
    v = rng.normal(0, 1, len(t))
    sc = fidelity_score(Series(t, v), np.zeros_like(v), noise=1.0)
    assert sc.windows == 0
