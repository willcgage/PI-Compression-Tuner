"""Synthetic signals for trying the tool without a PI server."""

from __future__ import annotations

import numpy as np

from .models import PointConfig, Series

START = 1767225600.0  # 2026-01-01T00:00:00Z


def demo_data(days: float = 3, seed: int = 1, step_s: float = 5.0) -> tuple[dict[str, Series], dict[str, PointConfig]]:
    """Four points with different behavior and deliberately poor settings, sampled every ``step_s`` seconds."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, days * 86400, step_s)
    n = len(t)
    sigs = {
        "DEMO.TEMP.PV": (60 + 8 * np.sin(2 * np.pi * t / 86400) + rng.normal(0, 0.05, n), PointConfig(compdev=0.05, span=200)),
        "DEMO.FLOW.PV": (120 + np.cumsum(rng.normal(0, 0.4, n)) * 0.05 + rng.normal(0, 1.0, n), PointConfig(compdev=0.2, span=300)),
        "DEMO.PRESS.PV": (np.where((t // 7200) % 2 == 0, 10.0, 14.0) + rng.normal(0, 0.02, n), PointConfig(compdev=2.0, span=25)),
        "DEMO.LEVEL.PV": (50 + 20 * np.sin(2 * np.pi * t / 5400) + rng.normal(0, 0.3, n),
                          PointConfig(compdev=0.5, span=100, compressing=False)),
    }
    return ({k: Series(START + t, v) for k, (v, _) in sigs.items()}, {k: c for k, (_, c) in sigs.items()})


DEMO_INFO = {
    "DEMO.TEMP.PV": {"descriptor": "Reactor outlet temperature", "engunits": "degF", "pointtype": "Float32"},
    "DEMO.FLOW.PV": {"descriptor": "Feed flow", "engunits": "gpm", "pointtype": "Float32"},
    "DEMO.PRESS.PV": {"descriptor": "Header pressure", "engunits": "psig", "pointtype": "Float32"},
    "DEMO.LEVEL.PV": {"descriptor": "Surge tank level", "engunits": "%", "pointtype": "Float32"},
}


def demo_info() -> dict[str, dict]:
    """Descriptions and units for the demo points, so templates match them."""
    from .templates import point_info
    return {tag: point_info(tag, attrs) for tag, attrs in DEMO_INFO.items()}
