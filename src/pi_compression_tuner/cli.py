"""Command-line interface: ``pict``."""

from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import click
import numpy as np
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
    rng = np.random.default_rng(seed)
    t = np.arange(0, days * 86400, 5.0)
    ts = pd.Timestamp("2026-01-01", tz="UTC") + pd.to_timedelta(t, unit="s")
    sigs = {
        "DEMO.TEMP.PV": (60 + 8 * np.sin(2 * np.pi * t / 86400) + rng.normal(0, 0.05, len(t)), PointConfig(compdev=0.05, span=200)),
        "DEMO.FLOW.PV": (120 + np.cumsum(rng.normal(0, 0.4, len(t))) * 0.05 + rng.normal(0, 1.0, len(t)), PointConfig(compdev=0.2, span=300)),
        "DEMO.PRESS.PV": (np.where((t // 7200) % 2 == 0, 10.0, 14.0) + rng.normal(0, 0.02, len(t)), PointConfig(compdev=2.0, span=25)),
        "DEMO.LEVEL.PV": (50 + 20 * np.sin(2 * np.pi * t / 5400) + rng.normal(0, 0.3, len(t)), PointConfig(compdev=0.5, span=100, compressing=False)),
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    pd.concat([pd.DataFrame({"Tag": k, "Timestamp": ts, "Value": v}) for k, (v, _) in sigs.items()]) \
        .to_csv(out / "data.csv", index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    pd.DataFrame([{"Name": k, **{a.capitalize(): (int(b) if isinstance(b, bool) else b) for a, b in asdict(c).items()}}
                  for k, (_, c) in sigs.items()]).to_csv(out / "config.csv", index=False)
    click.echo(f"Wrote {out / 'data.csv'} and {out / 'config.csv'}. Try: pict analyze {out / 'data.csv'} --config {out / 'config.csv'} --mode raw")


# --- PI Web API (read-only) ---------------------------------------------

def _client(url, auth, ca_bundle, insecure):
    from .sources.piwebapi import PIWebAPI
    verify = False if insecure else (ca_bundle or True)
    return PIWebAPI(url, auth=auth, verify=verify)


def _pi_options(f):
    f = click.option("--insecure", is_flag=True, help="Skip TLS certificate checks.")(f)
    f = click.option("--ca-bundle", type=click.Path(exists=True, dir_okay=False), help="CA bundle for the PI Web API certificate.")(f)
    f = click.option("--auth", type=click.Choice(["kerberos", "basic", "anonymous"]), default="kerberos", show_default=True,
                     help="basic reads PI_WEBAPI_USER and PI_WEBAPI_PASSWORD from the environment.")(f)
    f = click.option("--server", required=True, help="PI Data Archive name.")(f)
    f = click.option("--url", required=True, help="PI Web API base URL, e.g. https://pivision/piwebapi")(f)
    return f


def _refs(api, server, tags, name_filter, limit):
    if tags:
        return [api.point(server, t) for t in tags]
    return api.find_points(server, name_filter, max_count=limit)


def _write_configs(api, refs, path):
    rows = []
    for ref in refs:
        a = api.attributes(ref)
        rows.append({"Name": ref.name, **{k: a.get(k.lower()) for k in
                     ("ExcDev", "ExcMin", "ExcMax", "CompDev", "CompMin", "CompMax", "Compressing", "Step", "Zero", "Span", "PointType")}})
    pd.DataFrame(rows).to_csv(path, index=False)


def _write_series(series, path):
    frames = [pd.DataFrame({"Tag": name, "Timestamp": pd.to_datetime(s.t, unit="s", utc=True), "Value": s.v})
              for name, s in series.items() if len(s)]
    df = pd.concat(frames) if frames else pd.DataFrame(columns=["Tag", "Timestamp", "Value"])
    df.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%S.%fZ")


@main.group()
def piwebapi():
    """Read points, settings and data from the PI Web API. Read-only."""


@piwebapi.command()
@_pi_options
@click.option("--filter", "name_filter", default="*", show_default=True, help="Tag name filter.")
@click.option("--start", default="*-1d", show_default=True)
@click.option("--end", default="*", show_default=True)
@click.option("--limit", type=int, default=1000, show_default=True)
@click.option("--out", "out_path", default="triage.csv", show_default=True)
def triage(url, server, auth, ca_bundle, insecure, name_filter, start, end, limit, out_path):
    """Rank points by archive events per day, the cheap first pass for finding noisy or empty points."""
    api = _client(url, auth, ca_bundle, insecure)
    refs = api.find_points(server, name_filter, max_count=limit)
    rows = []
    with click.progressbar(refs, label="Counting", file=sys.stderr) as it:
        for ref in it:
            a = api.attributes(ref)
            rows.append({"Name": ref.name, "ArchiveEvents": api.event_count(ref, start, end),
                         **{k: a.get(k.lower()) for k in ("CompDev", "CompDevPercent", "ExcDev", "Compressing", "Span", "PointType")}})
    df = pd.DataFrame(rows).sort_values("ArchiveEvents", ascending=False)
    df.to_csv(out_path, index=False)
    click.echo(f"Wrote {out_path} ({len(df)} points). Points with the most events, and with none, are the first to review.")


@piwebapi.command()
@_pi_options
@click.option("--tag", "tags", multiple=True, help="Tag name; repeat for several.")
@click.option("--filter", "name_filter", default=None, help="Tag name filter, used when no --tag is given.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--duration", default="1d", show_default=True, help="How long to record, e.g. 6h, 2d.")
@click.option("--poll", default="10s", show_default=True)
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default="capture", show_default=True)
def record(url, server, auth, ca_bundle, insecure, tags, name_filter, limit, duration, poll, out_dir):
    """Record the snapshot stream (after exception, before compression) for later `pict analyze --mode snapshot`."""
    if not tags and not name_filter:
        raise click.UsageError("give --tag or --filter")
    api = _client(url, auth, ca_bundle, insecure)
    refs = _refs(api, server, tags, name_filter, limit)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_configs(api, refs, out / "config.csv")
    click.echo(f"Recording {len(refs)} points for {duration}. Leave this running; Ctrl+C stops early and keeps what was recorded.", err=True)
    series = api.record_snapshots(refs, _duration(duration), _duration(poll),
                                  on_poll=lambda c: click.echo(f"\r  {sum(c.values())} events", nl=False, err=True))
    _write_series(series, out / "data.csv")
    click.echo(f"\nWrote {out / 'data.csv'} and {out / 'config.csv'}.")


@piwebapi.command()
@_pi_options
@click.option("--tag", "tags", multiple=True, help="Tag name; repeat for several.")
@click.option("--filter", "name_filter", default=None, help="Tag name filter, used when no --tag is given.")
@click.option("--limit", type=int, default=100, show_default=True)
@click.option("--start", default="*-7d", show_default=True)
@click.option("--end", default="*", show_default=True)
@click.option("--out", "out_dir", type=click.Path(file_okay=False), default="fetch", show_default=True)
def fetch(url, server, auth, ca_bundle, insecure, tags, name_filter, limit, start, end, out_dir):
    """Download archived values and settings. Only useful for tuning where the archive is near-raw
    (compression off or very tight); otherwise use `record`."""
    if not tags and not name_filter:
        raise click.UsageError("give --tag or --filter")
    api = _client(url, auth, ca_bundle, insecure)
    refs = _refs(api, server, tags, name_filter, limit)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_configs(api, refs, out / "config.csv")
    series = {}
    with click.progressbar(refs, label="Fetching", file=sys.stderr) as it:
        for ref in it:
            series[ref.name] = api.recorded(ref, start, end)
    _write_series(series, out / "data.csv")
    click.echo(f"Wrote {out / 'data.csv'} and {out / 'config.csv'}.")


if __name__ == "__main__":
    main()
