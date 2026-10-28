# PI-Compression-Tuner

Recommends exception and compression settings for AVEVA PI Points, based on how each signal actually behaves.

This is the MVP: it **only makes recommendations**. Nothing in it writes values or point attributes to PI. Approved changes go out through a PI Builder sheet that a person reviews and publishes.

## How it works

1. **Get a signal** that has not yet been compressed:
   - the snapshot stream, recorded read-only through the PI Web API (`pict piwebapi record`);
   - archived data for points whose compression is off or very tight (`pict piwebapi fetch`);
   - or any CSV export.
2. **Replay PI's data reduction** at candidate settings. The replay covers exception reporting (ExcDev, ExcMin, ExcMax, and the "previous value" rule) and swinging-door compression (CompDev, CompMin, CompMax). It then rebuilds the trend PI would draw, using linear interpolation, or held values for step points.
3. **Score each candidate** for fidelity, maximum error, and archive events per day.
4. **Recommend** the loosest CompDev that still meets the target fidelity and any error limit. The value is rounded to two significant figures, but only when the rounded value still meets the target.
5. **Classify** each point:

| Class | Meaning | In bulk apply? |
|---|---|---|
| Undercompressed | Keeps at least twice as many events as the target needs | yes |
| Overcompressed | Current settings miss the target or the error limit | yes |
| Good | Within target; no change | n/a |
| Hard to compress | No setting reaches the target, or reaching it keeps over half the events | no, review |
| Zero/span issue | Span is zero or negative, or more than 1% of values fall outside Zero and Span | no, review |
| Flatline | The value never changes | no, review |
| Insufficient data | Fewer than 50 events | no |

## Fidelity

Commercial tools don't publish their fidelity formulas, so this one is defined here. For the raw input `x`, the reconstructed archive `r`, the measured noise `σ` (robust MAD of first differences), and a noise band `c` (default 2):

```
e'  = max(0, |x - r| - c·σ)                       error beyond the noise band
F_w = 1 - Σe'² / max(Σ(x - x̄)², n·σ²)             per time window (default 1 h)
F   = min(10th percentile of F_w, whole-series F)  headline score
```

- Windows where the signal only moves inside the noise band are skipped, because there is nothing in them to lose.
- The low window percentile catches detail lost during a few busy hours, which a whole-series score hides behind slow drift.
- Use `--max-error` (engineering units) or `--max-error-pct-span` to add a hard limit on the largest error.

**In `snapshot` mode** the reference is the snapshot stream, which has already passed exception. Only CompDev is tuned in this mode. **In `raw` mode** both stages are replayed, and ExcDev is recommended as `--excdev-ratio` × CompDev (default 0.5).

## Install

```bash
pip install -e ".[dev]"            # add ,kerberos for Windows integrated auth to the PI Web API
pytest
```

## Try it without PI

```bash
pict demo demo
pict analyze demo/data.csv --config demo/config.csv --mode raw --out demo/out
# open demo/out/report.html
```

## Use with PI Web API (read-only)

```bash
# 1. Triage: rank points by archive events per day
pict piwebapi triage --url https://pihost/piwebapi --server PISRV --filter "FIC*" --start "*-1d"

# 2. Record the snapshot stream for the candidates (no point settings are changed)
pict piwebapi record --url https://pihost/piwebapi --server PISRV --filter "FIC*" --duration 2d --out capture

# 3. Analyse
pict analyze capture/data.csv --config capture/config.csv --mode snapshot --server PISRV --out results
```

- `--auth` takes `kerberos` (the default), `basic` (which reads `PI_WEBAPI_USER` and `PI_WEBAPI_PASSWORD` from the environment), or `anonymous`. Use `--ca-bundle` for an internal CA.
- `record` uses PI Web API stream updates. It registers a marker per point, which creates a server-side subscription but changes no data or settings.

## Outputs

- `report.html`: a summary, a results table, and a trend overlay for each point (input, current, and recommended), zoomed on the hour with the largest error.
- `results.csv`: current versus recommended settings, fidelity, maximum error, events per day, and notes.
- `pibuilder.csv`: one row per changed point, in PI Builder format. Rows that are safe to bulk apply are pre-selected with `x`. Review it in Excel, then publish with PI Builder.

## Input formats

- **Data**, long format: `Tag,Timestamp,Value`. Wide format: `Timestamp,<tag1>,<tag2>,...`. Non-numeric values (system states) are dropped.
- **Config**: one row per point, with PI attribute names as headers (case-insensitive): `Name, ExcDev, ExcMin, ExcMax, CompDev, CompMin, CompMax, Compressing, Step, Zero, Span`. `ExcDevPercent` and `CompDevPercent` are used only when the deviation in engineering units is missing.

## Checking the simulator against real PI

`tests/test_parity.py` compares the simulated archive with the archive a real PI Data Archive produced. Add cases under `tests/parity/<case>/`, each with:

- `snapshot.csv`: all snapshot events;
- `archive.csv`: PI's archived events for the same period;
- `config.csv`: the point's settings during that period.

Treat the simulator as validated only after this test passes on real data from your site.

## Not in the MVP

- Raw capture runs that temporarily lower ExcDev.
- Writing settings to PI, with change history and undo.
- An AF SDK adapter.
- A web UI.

The design notes are in the research page linked from the project.
