"""Command-line interface: ``pict``."""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import click
import pandas as pd

from . import __version__
from .export import write_pibuilder_csv, write_results_csv
from .models import PointConfig
from .report import write_report
from .sources.csvfiles import read_configs, read_series
from .tune import TuneOptions, tune_point


def _duration(text: str) -> float:
    """Parse '30s', '15m', '1h', '7d' or plain seconds."""
    text = text.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in units:
        return float(text[:-1]) * units[text[-1]]
    return float(text)


@click.group()
@click.version_option(__version__)
def main():
    """Recommend PI Point exception and compression settings. Read-only: nothing is written to PI."""


@main.command()
@click.argument("data", type=click.Path(exists=True, dir_okay=False))
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              help="CSV of current point attributes (PI Builder column names). Missing points use PI defaults.")
@click.option("--mode", type=click.Choice(["snapshot", "raw"]), default="snapshot", show_default=True,
              help="snapshot: data already passed exception. raw: every value the interface read.")
@click.option("--target", type=float, default=0.97, show_default=True, help="Target fidelity, 0 to 1.")
@click.option("--window", default="1h", show_default=True, help="Scoring window, e.g. 15m, 1h.")
@click.option("--percentile", type=float, default=10.0, show_default=True, help="Window percentile used as the headline score.")
@click.option("--max-error", type=float, help="Largest allowed error, engineering units.")
@click.option("--max-error-pct-span", type=float, help="Largest allowed error, percent of span.")
@click.option("--excdev-ratio", type=float, default=0.5, show_default=True, help="Raw mode: ExcDev = ratio x CompDev.")
@click.option("--server", default="", help="Data Archive name to put in the PI Builder Parent column.")
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default="out", show_default=True)
def analyze(data, config_path, mode, target, window, percentile, max_error, max_error_pct_span, excdev_ratio, server, out_dir):
    """Analyse DATA (CSV) and write results.csv, pibuilder.csv and report.html."""
    series = read_series(data)
    configs = read_configs(config_path) if config_path else {}
    opts = TuneOptions(target=target, input_mode=mode, window_s=_duration(window), percentile=percentile,
                       max_error=max_error, max_error_pct_span=max_error_pct_span, excdev_ratio=excdev_ratio)
    results = []
    with click.progressbar(list(series.items()), label="Tuning", file=sys.stderr) as items:
        for tag, s in items:
            cfg = configs.get(tag)
            if cfg is None:
                click.echo(f"\nNo settings for {tag}; assuming PI defaults.", err=True)
                cfg = PointConfig()
            results.append(tune_point(tag, s, cfg, opts))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_results_csv(results, out / "results.csv")
    n = write_pibuilder_csv(results, out / "pibuilder.csv", server=server, include_excdev=mode == "raw")
    write_report(results, series, opts, out / "report.html")
    counts = pd.Series([r.classification.value for r in results]).value_counts()
    for k, v in counts.items():
        click.echo(f"  {k:<20} {v}")
    click.echo(f"Wrote {out / 'report.html'}, {out / 'results.csv'} and {out / 'pibuilder.csv'} ({n} changed points).")


@main.command()
@click.argument("out_dir", type=click.Path(file_okay=False), default="demo")
@click.option("--days", type=float, default=3, show_default=True)
@click.option("--seed", type=int, default=1, show_default=True)
def demo(out_dir, days, seed):
    """Write a synthetic data.csv and config.csv to try `pict analyze` without a PI server."""
    from .demo import demo_data
    from .sources.ops import series_frame
    series, configs = demo_data(days, seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    series_frame(series).to_csv(out / "data.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    pd.DataFrame([{"Name": k, **{a.capitalize(): (int(b) if isinstance(b, bool) else b) for a, b in asdict(c).items()}}
                  for k, c in configs.items()]).to_csv(out / "config.csv", index=False)
    click.echo(f"Wrote {out / 'data.csv'} and {out / 'config.csv'}. Try: pict analyze {out / 'data.csv'} --config {out / 'config.csv'} --mode raw")


# --- PI (read-only) ----------------------------------------------------

def _connect(server, source, url, auth, ca_bundle, insecure, afsdk_dir):
    from .sources.connect import connect
    verify = False if insecure else (ca_bundle or True)
    conn = connect(server, source=source, url=url, auth=auth, verify=verify, afsdk_dir=afsdk_dir)
    click.echo(conn.note or f"Connected through {'the PI Web API' if conn.kind == 'webapi' else 'AF SDK'}.", err=True)
    return conn.source


def _pi_options(f):
    f = click.option("--afsdk-dir", type=click.Path(file_okay=False), help="Folder holding OSIsoft.AFSDK.dll, if not the default.")(f)
    f = click.option("--insecure", is_flag=True, help="Skip TLS certificate checks (PI Web API).")(f)
    f = click.option("--ca-bundle", type=click.Path(exists=True, dir_okay=False), help="CA bundle for the PI Web API certificate.")(f)
    f = click.option("--auth", type=click.Choice(["kerberos", "basic", "anonymous"]), default="kerberos", show_default=True,
                     help="PI Web API auth. basic reads PI_WEBAPI_USER and PI_WEBAPI_PASSWORD from the environment.")(f)
    f = click.option("--url", help="PI Web API base URL, e.g. https://pivision/piwebapi")(f)
    f = click.option("--source", type=click.Choice(["auto", "webapi", "afsdk"]), default="auto", show_default=True,
                     help="auto tries the PI Web API, then falls back to AF SDK.")(f)
    f = click.option("--server", required=True, help="PI Data Archive name.")(f)
    return f


def _point_options(f):
    f = click.option("--limit", type=int, default=100, show_default=True)(f)
    f = click.option("--filter", "name_filter", default=None, help="Tag name filter, used when no --tag is given.")(f)
    f = click.option("--tag", "tags", multiple=True, help="Tag name; repeat for several.")(f)
    return f


@main.group()
def pi():
    """Read points, settings and data from PI (PI Web API, falling back to AF SDK). Read-only."""


@pi.command()
@_pi_options
@click.option("--filter", "name_filter", default="*", show_default=True, help="Tag name filter.")
@click.option("--start", default="*-1d", show_default=True)
@click.option("--end", default="*", show_default=True)
@click.option("--limit", type=int, default=1000, show_default=True)
@click.option("--out", "out_path", default="triage.csv", show_default=True)
def triage(server, source, url, auth, ca_bundle, insecure, afsdk_dir, name_filter, start, end, limit, out_path):
    """Rank points by archive events, the cheap first pass for finding noisy or empty points."""
    src = _connect(server, source, url, auth, ca_bundle, insecure, afsdk_dir)
    refs = src.find_points(server, name_filter, max_count=limit)
    rows = []
    with click.progressbar(refs, label="Counting", file=sys.stderr) as it:
        for ref in it:
            a = src.attributes(ref)
            rows.append({"Name": ref.name, "ArchiveEvents": src.event_count(ref, start, end),
                         **{k: a.get(k.lower()) for k in ("CompDev", "ExcDev", "Compressing", "Span", "PointType")}})
    df = pd.DataFrame(rows)
    if len(df):
        df = df.sort_values("ArchiveEvents", ascending=False)
    df.to_csv(out_path, index=False)
    click.echo(f"Wrote {out_path} ({len(df)} points). Points with the most events, and with none, are the first to review.")


@pi.command()
@_pi_options
@_point_options
@click.option("--duration", default="1d", show_default=True, help="How long to record, e.g. 6h, 2d.")
@click.option("--poll", default="10s", show_default=True)
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default="capture", show_default=True)
def record(server, source, url, auth, ca_bundle, insecure, afsdk_dir, tags, name_filter, limit, duration, poll, out_dir):
    """Record the snapshot stream (after exception, before compression) for later `pict analyze --mode snapshot`."""
    from .sources.ops import configs_frame, series_frame
    src = _connect(server, source, url, auth, ca_bundle, insecure, afsdk_dir)
    refs = _resolve(src, server, tags, name_filter, limit)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    configs_frame(src, refs).to_csv(out / "config.csv", index=False)
    click.echo(f"Recording {len(refs)} points for {duration}. Ctrl+C stops early and keeps what was recorded.", err=True)
    series = src.record_snapshots(refs, _duration(duration), _duration(poll),
                                  on_poll=lambda c: click.echo(f"\r  {sum(c.values())} events", nl=False, err=True))
    series_frame(series).to_csv(out / "data.csv", index=False, date_format="%Y-%m-%dT%H:%M:%S.%fZ")
    click.echo(f"\nWrote {out / 'data.csv'} and {out / 'config.csv'}.")


@pi.command()
@_pi_options
@_point_options
@click.option("--start", default="*-7d", show_default=True)
@click.option("--end", default="*", show_default=True)
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default="fetch", show_default=True)
def fetch(server, source, url, auth, ca_bundle, insecure, afsdk_dir, tags, name_filter, limit, start, end, out_dir):
    """Download archived values and settings. Only useful for tuning where the archive is near-raw
    (compression off or very tight); otherwise use `record`."""
    from .sources.ops import configs_frame, fetch_recorded, series_frame
    src = _connect(server, source, url, auth, ca_bundle, insecure, afsdk_dir)
    refs = _resolve(src, server, tags, name_filter, limit)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    configs_frame(src, refs).to_csv(out / "config.csv", index=False)
    series = fetch_recorded(src, refs, start, end, progress=lambda i, n: click.echo(f"\r  {i}/{n} points", nl=False, err=True))
    series_frame(series).to_csv(out / "data.csv", index=False, date_format="%Y-%m-%dT%H:%M:%S.%fZ")
    click.echo(f"\nWrote {out / 'data.csv'} and {out / 'config.csv'}.")


def _resolve(src, server, tags, name_filter, limit):
    from .sources.ops import resolve_points
    try:
        return resolve_points(src, server, tags, name_filter, limit)
    except ValueError as e:
        raise click.UsageError(str(e))


@main.command()
@click.option("--port", type=int, default=8501, show_default=True)
@click.option("--host", default="localhost", show_default=True, help="Use 0.0.0.0 to serve other machines.")
def ui(port, host):
    """Open the web UI in a browser."""
    try:
        import streamlit  # noqa: F401
    except ImportError:
        raise click.ClickException("The UI needs: pip install 'pi-compression-tuner[ui]'")
    import subprocess
    app = Path(__file__).parent / "ui" / "app.py"
    raise SystemExit(subprocess.call([sys.executable, "-m", "streamlit", "run", str(app),
                                      "--server.port", str(port), "--server.address", host,
                                      "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal"]))


if __name__ == "__main__":
    main()
