import numpy as np
import pytest

from pi_compression_tuner import PointConfig, Series, exception_filter, simulate_archive, swinging_door
from pi_compression_tuner.metrics import reconstruct


def test_exception_first_value_always_sent():
    t = np.arange(5.0)
    assert exception_filter(t, np.zeros(5), excdev=1.0, excmax=0)[0] == 0


def test_exception_drops_small_changes_and_sends_previous_value():
    t = np.arange(6.0)
    v = np.array([0.0, 0.1, 0.2, 0.3, 5.0, 5.1])
    sent = exception_filter(t, v, excdev=1.0, excmax=0).tolist()
    # 5.0 passes; 0.3 (the value before it) is sent too so the change keeps its start.
    assert sent == [0, 3, 4]


def test_exception_excmax_forces_a_report():
    t = np.arange(0.0, 100.0, 10.0)
    sent = exception_filter(t, np.ones(10), excdev=1.0, excmax=30).tolist()
    assert sent == [0, 3, 6, 9]


def test_exception_excmin_blocks_fast_changes():
    t = np.array([0.0, 1.0, 2.0, 10.0])
    v = np.array([0.0, 5.0, 10.0, 15.0])
    sent = exception_filter(t, v, excdev=1.0, excmin=5, excmax=0).tolist()
    # Values at t=1 and t=2 fall inside ExcMin, so not even the "previous value" is sent.
    assert sent == [0, 3]


def test_door_keeps_only_endpoints_of_a_line():
    t = np.arange(100.0)
    r = swinging_door(t, 2 * t + 1, compdev=0.01, compmax=1e9)
    assert r.archived.tolist() == [0]
    assert r.snapshot == 99


def test_door_archives_the_corner():
    t = np.arange(20.0)
    v = np.where(t < 10, t, 10.0)
    r = swinging_door(t, v, compdev=0.1, compmax=1e9)
    assert 9 in r.archived.tolist() or 10 in r.archived.tolist()


@pytest.mark.parametrize("compdev", [0.1, 0.5, 2.0])
def test_door_error_never_exceeds_compdev(compdev):
    rng = np.random.default_rng(42)
    t = np.cumsum(rng.uniform(0.5, 2.0, 5000))
    v = np.cumsum(rng.normal(0, 0.3, 5000))
    r = swinging_door(t, v, compdev=compdev, compmax=1e12)
    keep = np.append(r.archived, r.snapshot)
    recon = reconstruct(t[keep], v[keep], t)
    assert np.max(np.abs(v - recon)) <= compdev + 1e-9
    assert len(r.archived) < len(t)


def test_door_compmax_archives_a_flat_line_periodically():
    t = np.arange(0.0, 1000.0, 1.0)
    r = swinging_door(t, np.zeros_like(t), compdev=1.0, compmax=100)
    gaps = np.diff(t[r.archived])
    assert len(r.archived) >= 9 and np.all(gaps <= 101)


def test_compression_off_archives_everything():
    s = Series(np.arange(50.0), np.sin(np.arange(50.0)))
    sim = simulate_archive(s, PointConfig(compressing=False), apply_exception=False)
    assert len(sim.archived) == 50


def test_simulate_maps_indices_back_to_the_input():
    t = np.arange(10.0)
    v = np.array([0, 0, 0, 5, 5, 5, 0, 0, 0, 0], dtype=float)
    sim = simulate_archive(Series(t, v), PointConfig(excdev=1, compdev=0.1, excmax=0, compmax=1e9))
    assert set(sim.visible()) <= set(range(10))
    assert 3 in sim.visible() and 6 in sim.visible()
