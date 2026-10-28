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
            "SafeToBulkApply": r.safe_to_bulk_apply,
            "Change": r.changes,
            "InputEvents": r.input_events,
            "Noise": r.noise,
        }
        for prefix, ev in (("Current", cur), ("Recommended", rec)):
            row.update({
                f"{prefix}ExcDev": ev.config.excdev if ev else None,
                f"{prefix}CompDev": ev.config.compdev if ev else None,
                f"{prefix}CompDevPercent": ev.config.compdevpercent if ev else None,
                f"{prefix}Fidelity": ev.score.fidelity if ev else None,
                f"{prefix}MaxError": ev.score.max_error if ev else None,
                f"{prefix}EventsPerDay": ev.events_per_day if ev else None,
            })
        if cur and rec and cur.archived_events:
            row["EventChangePercent"] = 100.0 * (rec.archived_events - cur.archived_events) / cur.archived_events
        else:
            row["EventChangePercent"] = None
        row["Notes"] = " ".join(r.notes)
        rows.append(row)
    return pd.DataFrame(rows)


def write_results_csv(results: list[TuneResult], path: str | Path) -> None:
    results_frame(results).to_csv(path, index=False, float_format="%.6g")


def write_pibuilder_csv(results: list[TuneResult], path: str | Path, server: str = "", include_excdev: bool = False) -> int:
    """Write a PI Builder sheet with one row per point whose recommendation changes its settings.

    Rows that are safe to bulk apply are pre-selected with an ``x``. Other rows are
    included unselected so they can be reviewed one at a time. Returns the row count.
    """
    rows = []
    for r in results:
        if not r.changes:
            continue
        cfg = r.recommended.config
        row = {"Selected(x)": "x" if r.safe_to_bulk_apply else "", "Parent": server, "Name": r.tag, "ObjectType": "PIPoint"}
        if include_excdev:
            row["ExcDev"] = cfg.excdev
        row["CompDev"] = cfg.compdev
        if not r.current.config.compressing:
            row["Compressing"] = 1
        rows.append(row)
    cols = ["Selected(x)", "Parent", "Name", "ObjectType"] + (["ExcDev"] if include_excdev else []) + ["CompDev", "Compressing"]
    df = pd.DataFrame(rows, columns=cols)
    if "Compressing" in df and df["Compressing"].isna().all():
        df = df.drop(columns="Compressing")
    df.to_csv(path, index=False, float_format="%.6g")
    return len(df)
