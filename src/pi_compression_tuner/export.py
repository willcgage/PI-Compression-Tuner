"""Write tuning results as a results table and as a PI Builder import sheet.

The PI Builder sheet is for a person to review in Excel and publish themselves.
This tool never writes settings to PI.
"""

from __future__ import annotations

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
        row["Notes"] = " ".join(r.notes)
        rows.append(row)
    df = pd.DataFrame(rows)
    # Points without settings (too few events, not moving) leave gaps: keep the columns numeric, with NaN.
    numeric = [c for c in df.columns if c.startswith(("Current", "Recommended", "TemplateE", "TemplateC"))
               or c == "EventChangePercent"]
    df[numeric] = df[numeric].apply(pd.to_numeric, errors="coerce").astype(float)
    return df


def write_results_csv(results: list[TuneResult], path: str | Path) -> None:
    results_frame(results).to_csv(path, index=False, float_format="%.6g")


def pibuilder_frame(results: list[TuneResult], server: str = "", include_excdev: bool = False,
                    selected: set[str] | None = None) -> pd.DataFrame:
    """PI Builder rows for every point whose recommendation changes its settings.

    A row is marked ``x`` (selected for publishing) when its tag is in ``selected``,
    or, when ``selected`` is None, when it is safe to bulk apply. Unselected rows stay
    in the sheet so they can be reviewed one at a time.
    """
    rows = []
    for r in results:
        if not r.changes:
            continue
        cfg = r.recommended.config
        pick = (r.tag in selected) if selected is not None else r.ready_to_export
        row = {"Selected(x)": "x" if pick else "", "Parent": server, "Name": r.tag, "ObjectType": "PIPoint"}
        if include_excdev:
            row["ExcDev"] = cfg.excdev
        row["CompDev"] = cfg.compdev
        if not r.current.config.compressing:
            row["Compressing"] = 1
        rows.append(row)
    cols = ["Selected(x)", "Parent", "Name", "ObjectType"] + (["ExcDev"] if include_excdev else []) + ["CompDev", "Compressing"]
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
