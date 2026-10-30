# PI-Compression-Tuner

Recommends exception and compression settings for AVEVA PI Points, based on how each signal actually behaves.

This is the MVP: it **only makes recommendations**. Use it from the web UI (`pict ui`) or the command line (`pict`). Nothing in it writes values or point attributes to PI. Approved changes go out through a PI Builder sheet that a person reviews and publishes.

## How it works

1. **Get a signal** that has not yet been compressed:
   - the snapshot stream, recorded read-only through the PI Web API or AF SDK (`pict pi record`);
   - archived data for points whose compression is off or very tight (`pict pi fetch`);
   - or any CSV export.
2. **Replay PI's data reduction** at candidate settings. The replay covers exception reporting (ExcDev, ExcMin, ExcMax, and the "previous value" rule) and swinging-door compression (CompDev, CompMin, CompMax). It then rebuilds the trend PI would draw, using linear interpolation, or held values for step points.
3. **Score each candidate** for retention, maximum and RMS error, archive events per day, and compression ratio (input events per archived event).
4. **Propose** the loosest CompDev that still meets the target retention (default 95%) and any error limit.
   - The score is not always smooth in CompDev: a looser setting can pass again after tighter ones fail. The search stays in the first passing range and notes any passing setting beyond a gap.
   - A refined value is kept only if a setting 10% tighter also passes, so the proposal never sits in a narrow passing pocket.
   - The value is rounded to two significant figures, but only when the rounded value still meets the target.
5. **Give each point a verdict:**

| Verdict | Meaning | Ready to export? |
|---|---|---|
| Storing noise | The archive keeps at least twice as many events as the target needs | yes |
| Losing detail | Current settings miss the target retention or the error limit | yes |
| On target | Within target; no change | n/a |
| Little to gain | No setting reaches the target, or reaching it keeps over half the events | no, review |
| Range mismatch | Span is zero or negative, or more than 1% of values fall outside Zero and Span | no, review |
| Not moving | The value never changes | no, review |
| Too few events | Fewer than 50 events | no |

## Retention

Retention is the share of the signal's movement, beyond its own noise, that the archive keeps. For the raw input `x`, the reconstructed archive `r`, the measured noise `σ` (robust MAD of first differences), and a noise band `c` (default 2):

```
e'  = max(0, |x - r| - c·σ)                       error beyond the noise band
F_w = 1 - Σe'² / max(Σ(x - x̄)², n·σ²)             per time window (default 1 h)
F   = min(10th percentile of F_w, whole-series F)  headline score
```

- Windows where the signal only moves inside the noise band are skipped, because there is nothing in them to lose.
- The low window percentile catches detail lost during a few busy hours, which a whole-series score hides behind slow drift.
- Use `--max-error` (engineering units) or `--max-error-pct-span` to add a hard limit on the largest error.

**In `snapshot` mode** the reference is the snapshot stream, which has already passed exception. Only CompDev is tuned in this mode.

**In `raw` mode** both stages are replayed, and ExcDev is tuned together with CompDev:

- ExcDev is tried at 0.1, 0.25, 0.5, 0.75 and 1 × CompDev. The ratio that meets the target with the fewest archive events wins. Use `--excdev-ratio` to fix one ratio instead.
- ExcDev is never set below the measured noise σ (or above CompDev), so the interface does not pass noise. A note flags points whose current ExcDev is under half the noise.

## Install

```bash
pip install -e ".[ui,kerberos,afsdk]"   # ui: the web UI; kerberos: Windows sign-in to the PI Web API; afsdk: AF SDK fallback (Windows)
pip install -e ".[dev]" && pytest        # development
```

## The UI

```bash
pict ui          # opens http://localhost:8501
```

1. **Load data** in the sidebar. Choose one of:
   - **Demo data**: synthetic signals, no PI needed.
   - **CSV files**: for example a `pict pi record` capture.
   - **PI server**: reads archived values through the PI Web API, falling back to AF SDK.
2. **Choose tuning settings and run.** Settings are the target retention, scoring window, error limits, and whether the data is the snapshot stream or raw interface values.
3. **Overview tab:**
   - A map plots every point by archive events per day against retention, with arrows showing where each proposal moves it.
   - Below the map is the verdict table. Tick **Export** for the rows you want selected in the PI Builder sheet. Rows ready to export are ticked by default.
4. **Point detail tab:**
   - A plain-language finding, then now-versus-proposed archive volume, retention and largest error.
   - The point's settings with changed attributes highlighted, a signal profile, and the trend (whole period, or zoomed to the largest miss).
   - A trade-off curve shows what each CompDev costs in archive volume and buys in retention, with **Now**, **Proposal** and **Probe** marked.
   - Set the probe to any CompDev (and ExcDev, in raw mode) to see its retention, events per day, compression ratio, and largest and RMS error.
   - The trend's red strip shows where the proposed (or probe) archive strays from the input beyond the noise band.
   - Use the toggle to include or leave out the point from the PI Builder sheet, and **Previous**/**Next** to step through points.
   - Click **Adopt** to export the probe value instead of the proposal.
   - **Key moments**: see below.
5. **Export tab:** preview and download the PI Builder sheet, the results table, the HTML report, or the key moments you marked.

## Key moments

A key moment is an event the archive must never lose, such as a trip, a peak or a step. The retention score is statistical, so it can pass a setting that flattens one short but important excursion. Key moments close that gap.

- **Mark them** in Point detail:
  - Click the trend to mark the nearest input event.
  - Or press **Suggest**, which marks the main peaks, valleys and steps, and each crossing of the point's alarm limits. Isolated spikes are left out.
  - Alarm limits come from the settings CSV, or type them in the box next to **Suggest** (for example `20, 80`). A crossing counts once the smoothed signal gets two noise widths past the limit, so a signal sitting on the limit is not marked over and over. The trend and the report draw the limits as dashed lines.
  - Remove a moment from the list under the trend.
- **Retune** finds the loosest setting that meets the target and also keeps every moment.
  - A moment counts as kept when the archive passes within one noise width beyond the noise band (or within 2% of the observed range, if that is larger) of the event itself or of the smoothed signal there.
  - A tighter error limit always wins.
  - Current settings that miss a moment are marked **Losing detail**.
- **CompDev from moments:** with two or more moments, the tuner also reports the CompDev the moments alone imply, as a sanity check next to its own proposal. For each pair of consecutive moments it takes the largest distance of the smoothed signal from a straight line between them, then averages over the pairs (Method 1 in Silveira et al., 2012). A setting near that figure archives roughly the shape the moments draw. It is a guide only and never replaces the proposal.
- The HTML report marks each key moment on the trend with an open diamond.
- **Save and reuse them:**
  - Download them from the Export tab as `moments.csv` (`Tag,Timestamp`).
  - Load them again next to the CSV files in the UI, or pass them to `pict analyze --moments moments.csv`.

## Spikes

An isolated spike is one event that jumps at least 6 noise widths away and comes straight back. The signal profile counts them, and the notes flag them, because only a person can tell a real event from an instrument fault. Swinging door usually archives spikes, so each one costs archive events. Spikes are never dropped or smoothed. To be sure a real one is always kept, mark it as a key moment.

Recording the snapshot stream takes hours to days, so it stays a command-line job (`pict pi record`). Load its output into the UI as CSV files.

## Try the command line without PI

```bash
pict demo demo
pict analyze demo/data.csv --config demo/config.csv --mode raw --out demo/out
# open demo/out/report.html
```

## Connecting to PI (read-only)

By default, every `pict pi` command (and the UI) tries the **PI Web API** first. If the Web API is unreachable, refuses the credentials, or can't find the server, it falls back to **AF SDK**, and says which source it used and why. To force one source, pass `--source webapi` or `--source afsdk`.

```bash
# 1. Triage: rank points by archive events
pict pi triage --server PISRV --url https://pihost/piwebapi --filter "FIC*" --start "*-1d"

# 2. Record the snapshot stream for the candidates (no point settings are changed)
pict pi record --server PISRV --url https://pihost/piwebapi --filter "FIC*" --duration 2d --out capture

# 3. Analyze (or load capture/ into the UI)
pict analyze capture/data.csv --config capture/config.csv --mode snapshot --server PISRV --out results
```

- **PI Web API:**
  - `--auth` takes `kerberos` (the default), `basic` (which reads `PI_WEBAPI_USER` and `PI_WEBAPI_PASSWORD` from the environment), or `anonymous`.
  - Use `--ca-bundle` for an internal CA.
  - `record` uses stream updates. It registers a marker per point, which creates a server-side subscription but changes no data or settings.
- **AF SDK:**
  - Needs Windows, PI AF Client, and `pythonnet`.
  - The assembly is looked up in the standard `PIPC\AF\PublicAssemblies\4.0` folders, or in `--afsdk-dir` or `PI_AFSDK_DIR`.
  - `record` uses a snapshot `PIDataPipe`.
  - The server must be in the machine's known servers table.

## Outputs

- `report.html`: a summary, a table of every point linked to its own page, and one page per point in the same layout as the UI's Point detail tab: the finding, now-versus-proposed cards, the trend with a detail-lost strip and a zoom on the largest miss, the settings sheet and the signal profile. It works offline, follows the system's dark mode, and prints one point per page.
- `results.csv`: current versus proposed settings, retention, maximum and RMS error, events per day, compression ratio, spike count, key moments missed, the CompDev implied by the key moments, and notes.
- `pibuilder.csv`: one row per changed point, in PI Builder format. Rows that are ready to export are pre-selected with `x`. Review it in Excel, then publish with PI Builder.

## Input formats

- **Data**, long format: `Tag,Timestamp,Value`. Wide format: `Timestamp,<tag1>,<tag2>,...`. Non-numeric values (system states) are dropped.
- **Key moments** (optional): `Tag,Timestamp`, one row per moment.
- **Config**: one row per point, with PI attribute names as headers (case-insensitive): `Name, ExcDev, ExcMin, ExcMax, CompDev, CompMin, CompMax, Compressing, Step, Zero, Span`. `ExcDevPercent` and `CompDevPercent` are used only when the deviation in engineering units is missing. Optional alarm limits go in `LoLo`, `Lo`, `Hi` and `HiHi` columns, or in one `AlarmLimits` column with values separated by semicolons. PI Points do not carry alarm limits themselves, so they are not read from PI.

## Checking the simulator against real PI

`tests/test_parity.py` compares the simulated archive with the archive a real PI Data Archive produced. Add cases under `tests/parity/<case>/`, each with:

- `snapshot.csv`: all snapshot events;
- `archive.csv`: PI's archived events for the same period;
- `config.csv`: the point's settings during that period.

Treat the simulator as validated only after this test passes on real data from your site.

## Not yet verified

The PI Web API and AF SDK adapters are tested against fakes only. Run them against your servers, and add parity cases, before relying on them.

## Not in the MVP

- Raw capture runs that temporarily lower ExcDev.
- Writing settings to PI, with change history and undo.

The design notes are in the research page linked from the project.
