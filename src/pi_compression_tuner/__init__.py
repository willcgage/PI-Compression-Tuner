"""Recommend PI Point exception and compression settings from observed signal behaviour."""

from .models import PointConfig, Series
from .simulate import exception_filter, swinging_door, simulate_archive
from .tune import TuneOptions, TuneResult, Classification, evaluate_config, tradeoff_curve, tune_point

__all__ = [
    "PointConfig",
    "Series",
    "exception_filter",
    "swinging_door",
    "simulate_archive",
    "TuneOptions",
    "TuneResult",
    "Classification",
    "tune_point",
    "evaluate_config",
    "tradeoff_curve",
]

__version__ = "0.1.0"
