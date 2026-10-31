"""Write tuning results as a results table and as a PI Builder import sheet.

The PI Builder sheet is for a person to review in Excel and publish themselves.
This tool never writes settings to PI.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd

from .tune import TuneResult


def results_frame(results: list[TuneResult]) -> pd.DataFrame:
    rows = []
    for r in results:
        cur, rec = r.current, r.recommended
        row = {
            "Tag": r.tag,
            "Classification": r.classification.value,
            "ReadyToExport": r.ready_to_export,
            "Change": r.changes,
            "InputEvents": r.input_events,
            "Noise": r.noise,
            "Spikes": r.spikes,
            "KeyMoments": len(r.moments),
            "MomentCompDev": r.moment_compdev,
        }
        for prefix, ev in (("Current", cur), ("Recommended", rec)):
            row.update({
                f"{prefix}ExcDev": ev.config.excdev if ev else None,
                f"{prefix}CompDev": ev.config.compdev if ev else None,
                f"{prefix}CompDevPercent": ev.config.compdevpercent if ev else None,
                f"{prefix}Retention": ev.score.retention if ev else None,
                f"{prefix}MaxError": ev.score.max_error if ev else None,
                f"{prefix}RMSError": ev.score.rms_error if ev else None,
                f"{prefix}EventsPerDay": ev.events_per_day if ev else None,
                f"{prefix}CompressionRatio": ev.compression_ratio if ev else None,
                f"{prefix}MomentsMissed": ev.moments_missed if ev else None,
            })
        if cur and rec and cur.archived_events:
            row["EventChangePercent"] = 100.0 * (rec.archived_events - cur.archived_events) / cur.archived_events
        else:
            row["EventChangePercent"] = None
        b = r.baseline
        row["Template"] = r.template
        row.update({"TemplateExcDev": b.excdev if b else None, "TemplateCompDev": b.compdev if b else None,
                    "TemplateCompMax": b.compmax if b else None, "TemplateExcMax": b.excmax if b else None})
        sg = r.suggested
        row.update({"SuggestedExcDev": sg.excdev if sg else None, "SuggestedCompDev": sg.compdev if sg else None})
        shown = rec.config if (rec and r.changes) else sg
        row.update({"ProposedZero": shown.zero if shown else None, "ProposedSpan": shown.span if shown else None})
        row["Notes"] = " ".join(r.notes)
        rows.append(row)
    df = pd.DataFrame(rows)
    # Points without settings (too few events, not moving) leave gaps: keep the columns numeric, with NaN.
    numeric = [c for c in df.columns if c.startswith(("Current", "Recommended", "TemplateE", "TemplateC",
                                                         "Suggested", "Proposed")) or c == "EventChangePercent"]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce").astype(float)
    return df


def write_results_csv(results: list[TuneResult], path: str | Path) -> None:
    results_frame(results).to_csv(path, index=False, float_format="%.6g")


def pibuilder_frame(results: list[TuneResult], server: str = "", include_excdev: bool = False,
                    selected: set[str] | None = None) -> pd.DataFrame:
    """PI Builder rows for every point whose recommendation changes its settings, plus unscored suggestions
    (a narrower ExcDev, or Zero/Span for a point without a proposal).

    A row is marked ``x`` (selected for publishing) when its tag is in ``selected``,
    or, when ``selected`` is None, when it is safe to bulk apply. Unselected rows stay
    in the sheet so they can be reviewed one at a time.

    When any row changes Zero or Span, every row carries Zero, Span and the deviations as percent
    of the new span (CompDevPercent, ExcDevPercent). PI keeps the percent when Span changes, so
    the sheet gives the same result whichever column PI Builder applies first.
    """
    picked = []
    for r in results:
        if r.changes:
            cfg = r.recommended.config
        elif r.suggested is not None:
            cfg = r.suggested
        else:
            continue
        pick = (r.tag in selected) if selected is not None else (r.ready_to_export and r.changes)
        picked.append((r, cfg, pick))
    ranged = any(not (math.isclose(cfg.zero, r.current.config.zero) and math.isclose(cfg.span, r.current.config.span))
                 for r, cfg, _ in picked)
    with_exc = include_excdev or any(not math.isclose(cfg.excdev, r.current.config.excdev) for r, cfg, _ in picked)
    exc_col, comp_col = ("ExcDevPercent", "CompDevPercent") if ranged else ("ExcDev", "CompDev")
    rows = []
    for r, cfg, pick in picked:
        row = {"Selected(x)": "x" if pick else "", "Parent": server, "Name": r.tag, "ObjectType": "PIPoint"}
        if ranged:
            row["Zero"], row["Span"] = cfg.zero, cfg.span
        if with_exc:
            row[exc_col] = cfg.excdevpercent if ranged else cfg.excdev
        row[comp_col] = cfg.compdevpercent if ranged else cfg.compdev
        if not r.current.config.compressing:
            row["Compressing"] = 1
        rows.append(row)
    cols = (["Selected(x)", "Parent", "Name", "ObjectType"] + (["Zero", "Span"] if ranged else [])
            + ([exc_col] if with_exc else []) + [comp_col, "Compressing"])
    df = pd.DataFrame(rows, columns=cols)
    if df["Compressing"].isna().all():
        df = df.drop(columns="Compressing")
    else:
        df["Compressing"] = df["Compressing"].astype("Int64")
    return df


def write_pibuilder_csv(results: list[TuneResult], path: str | Path, server: str = "", include_excdev: bool = False,
                        selected: set[str] | None = None) -> int:
    """Write :func:`pibuilder_frame` to ``path`` and return the row count."""
    df = pibuilder_frame(results, server, include_excdev, selected)
    df.to_csv(path, index=False, float_format="%.6g")
    return len(df)
