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

### Offline Windows bundle

For a machine on the PI network with no internet access or admin rights, build a self-contained zip that carries its own Python and every dependency (including pythonnet and Windows sign-in):

```bash
pip install uv build
python packaging/build_windows_bundle.py   # writes dist/PI-Compression-Tuner-<version>-win64.zip
```

It builds on Linux or Windows. The **Windows bundle** workflow builds it on each push, tests it on a clean Windows machine, and publishes the tested zip as a pre-release (`bundle-<version>-win64`) on the Releases page. Unzip it on the target and follow `START-HERE.txt`: run the self-test, then `pict pi check`, then try real points.

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
5. **Export tab:** preview and download the PI Builder sheet, the results table, or the HTML report.

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
# 0. Check each source on its own and report what works
pict pi check --server PISRV --url https://pihost/piwebapi --tag SINUSOID

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
- `results.csv`: current versus proposed settings, retention, maximum and RMS error, events per day, compression ratio, and notes.
- `pibuilder.csv`: one row per changed point, in PI Builder format. Rows that are ready to export are pre-selected with `x`. Review it in Excel, then publish with PI Builder.

## Input formats

- **Data**, long format: `Tag,Timestamp,Value`. Wide format: `Timestamp,<tag1>,<tag2>,...`. Non-numeric values (system states) are dropped.
- **Config**: one row per point, with PI attribute names as headers (case-insensitive): `Name, ExcDev, ExcMin, ExcMax, CompDev, CompMin, CompMax, Compressing, Step, Zero, Span`. `ExcDevPercent` and `CompDevPercent` are used only when the deviation in engineering units is missing.

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
