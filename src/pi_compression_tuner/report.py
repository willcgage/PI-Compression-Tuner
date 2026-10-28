"""Self-contained HTML report: a summary by classification, a results table, and a trend overlay per point."""

from __future__ import annotations

import html
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .metrics import reconstruct
from .models import Series
from .simulate import simulate_archive
from .tune import Classification, TuneOptions, TuneResult

_CLASS_STYLE = {
    Classification.ON_TARGET: "good",
    Classification.STORING_NOISE: "under",
    Classification.LOSING_DETAIL: "over",
    Classification.LITTLE_TO_GAIN: "info",
    Classification.RANGE_MISMATCH: "info",
    Classification.NOT_MOVING: "info",
    Classification.TOO_FEW_EVENTS: "info",
}

_CSS = """
:root{--bg:#f6f8f9;--panel:#fff;--ink:#17222a;--muted:#5a6872;--rule:#d6dee3;--raw:#9aa7b0;--cur:#b8551c;--rec:#0f6f86;
--good:#2f7a3c;--under:#8a5a00;--over:#b03a2e;--info:#4b5d9a}
@media (prefers-color-scheme:dark){:root{--bg:#11181d;--panel:#172129;--ink:#e3eaee;--muted:#9aabb6;--rule:#2c3a44;
--raw:#5d6c76;--cur:#f08a4b;--rec:#5cc3da;--good:#79c486;--under:#f0b44a;--over:#ec8a7f;--info:#9fb0ee;color-scheme:dark}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:32px max(16px,4vw)}
main{max-width:1100px;margin:0 auto;display:flex;flex-direction:column;gap:28px}
h1{margin:0;font-size:1.8rem}h2{margin:0;font-size:1.2rem}p{margin:0}
.meta{color:var(--muted);font-size:.9rem}
.tiles{display:flex;flex-wrap:wrap;gap:10px}
.tile{background:var(--panel);border:1px solid var(--rule);padding:10px 14px;min-width:130px}
.tile b{display:block;font-size:1.5rem;font-variant-numeric:tabular-nums}
.wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--rule)}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{padding:6px 10px;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
th:first-child,td:first-child,td.l,th.l{text-align:left}
thead th{color:var(--muted);font-weight:600;background:var(--bg)}
.pill{display:inline-block;padding:0 8px;border-radius:999px;border:1px solid currentColor;font-size:.8rem}
.good{color:var(--good)}.under{color:var(--under)}.over{color:var(--over)}.info{color:var(--info)}
.point{background:var(--panel);border:1px solid var(--rule);padding:14px 16px;display:flex;flex-direction:column;gap:8px}
.point svg{width:100%;height:auto;display:block}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:.85rem;color:var(--muted)}
.sw{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px}
.notes{color:var(--muted);font-size:.88rem}
.charts{display:grid;grid-template-columns:2fr 1fr;gap:12px}
@media (max-width:760px){.charts{grid-template-columns:1fr}}
"""


def _fmt(x, digits=4):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "–"
    return f"{x:.{digits}g}"


def _count(x):
    if x is None:
        return "–"
    return f"{x:,.0f}" if x >= 100 else f"{x:.3g}"


def _pct(x):
    return "–" if x is None else f"{100 * x:.1f}%"


def envelope(t, v, buckets=800):
    """Min/max per bucket so a long series draws quickly without hiding spikes."""
    if len(t) <= 2 * buckets:
        return t, v
    edges = np.linspace(0, len(t), buckets + 1).astype(int)
    tt, vv = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        if b <= a:
            continue
        seg = v[a:b]
        i, j = a + int(np.argmin(seg)), a + int(np.argmax(seg))
        for k in sorted((i, j)):
            tt.append(t[k]); vv.append(v[k])
    return np.asarray(tt), np.asarray(vv)


def _svg(lines, t0, t1, w=720, h=200):
    vals = np.concatenate([v[(t >= t0) & (t <= t1)] for t, v, _ in lines if len(v)])
    if len(vals) == 0:
        vals = np.concatenate([v for _, v, _ in lines if len(v)])
    lo, hi = float(np.min(vals)), float(np.max(vals))
    if hi == lo:
        hi = lo + 1
    pad = (hi - lo) * 0.05
    lo, hi = lo - pad, hi + pad
    sx = lambda t: 40 + (t - t0) / max(t1 - t0, 1e-9) * (w - 50)
    sy = lambda v: 10 + (hi - v) / (hi - lo) * (h - 30)
    parts = [f'<svg viewBox="0 0 {w} {h}" role="img"><defs><clipPath id="c{w}"><rect x="40" y="0" width="{w-50}" height="{h-20}"/></clipPath></defs>']
    for frac in (0, 0.5, 1):
        val = lo + (hi - lo) * frac
        y = sy(val)
        parts.append(f'<line x1="40" x2="{w-10}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--rule)" stroke-width="1"/>')
        parts.append(f'<text x="36" y="{y+4:.1f}" font-size="10" text-anchor="end" fill="var(--muted)">{_fmt(val,3)}</text>')
    for t, v, color in lines:
        if len(t) == 0:
            continue
        pts = " ".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(t, v))
        width = {"--raw": 1.0, "--cur": 1.2, "--rec": 1.8}.get(color, 1.2)
        parts.append(f'<polyline points="{pts}" fill="none" stroke="var({color})" stroke-width="{width}" '
                     f'stroke-linejoin="round" clip-path="url(#c{w})"/>')
    span_h = (t1 - t0) / 3600
    parts.append(f'<text x="{w-10}" y="{h-4}" font-size="10" text-anchor="end" fill="var(--muted)">{span_h:.3g} h shown</text>')
    parts.append("</svg>")
    return "".join(parts)


def _point_section(r: TuneResult, s: Series, opts: TuneOptions) -> str:
    raw_mode = opts.input_mode == "raw"
    head = (f'<h2>{html.escape(r.tag)} <span class="pill {_CLASS_STYLE[r.classification]}">'
            f'{r.classification.value}</span></h2>')
    notes = f'<p class="notes">{html.escape(" ".join(r.notes))}</p>' if r.notes else ""
    if r.current is None or len(s) == 0:
        return f'<section class="point">{head}{notes}</section>'

    def recon_of(cfg):
        sim = simulate_archive(s, cfg, apply_exception=raw_mode)
        vis = sim.visible()
        return s.t[vis], s.v[vis], reconstruct(s.t[vis], s.v[vis], s.t, cfg.step)

    ct, cv, crec = recon_of(r.current.config)
    lines_full = [(*envelope(s.t, s.v), "--raw"), (*envelope(ct, cv), "--cur")]
    rec_line = None
    if r.recommended is not None and r.changes:
        rt, rv, rrec = recon_of(r.recommended.config)
        rec_line = (rt, rv)
        lines_full.append((*envelope(rt, rv), "--rec"))
        err = np.abs(s.v - rrec)
    else:
        err = np.abs(s.v - crec)
    # Zoom on the hour around the largest error, where any lost detail shows.
    k = int(np.argmax(err))
    z0, z1 = s.t[k] - 1800, s.t[k] + 1800
    def sel(t, v):
        # Keep one point either side of the window so segments crossing its edges still draw.
        a = max(int(np.searchsorted(t, z0)) - 1, 0)
        b = min(int(np.searchsorted(t, z1, side="right")) + 1, len(t))
        return t[a:b], v[a:b]
    zoom = [(*sel(s.t, s.v), "--raw"), (*sel(ct, cv), "--cur")]
    if rec_line is not None:
        zoom.append((*sel(*rec_line), "--rec"))
    legend = ('<div class="legend"><span><i class="sw" style="background:var(--raw)"></i>Input</span>'
              '<span><i class="sw" style="background:var(--cur)"></i>Archive now</span>'
              + ('<span><i class="sw" style="background:var(--rec)"></i>Archive with proposal</span>' if rec_line is not None else "")
              + "</div>")
    charts = (f'<div class="charts"><div>{_svg(lines_full, s.t[0], s.t[-1])}</div>'
              f'<div>{_svg(zoom, z0, z1, w=360)}</div></div>')
    return f'<section class="point">{head}{legend}{charts}{notes}</section>'


def write_report(results: list[TuneResult], series: dict[str, Series], opts: TuneOptions, path: str | Path,
                 title: str = "Compression tuning report") -> None:
    counts = Counter(r.classification for r in results)
    cur_total = sum(r.current.events_per_day for r in results if r.current)
    rec_total = sum((r.recommended or r.current).events_per_day for r in results if r.current)
    tiles = [f'<div class="tile"><b>{len(results)}</b>points analysed</div>']
    for c in Classification:
        if counts.get(c):
            tiles.append(f'<div class="tile {_CLASS_STYLE[c]}"><b>{counts[c]}</b>{c.value}</div>')
    if cur_total:
        tiles.append(f'<div class="tile"><b>{100 * (rec_total - cur_total) / cur_total:+.0f}%</b>archive events per day</div>')

    rows = []
    for r in results:
        cur, rec = r.current, r.recommended
        rows.append(
            f'<tr><td>{html.escape(r.tag)}</td><td class="l"><span class="pill {_CLASS_STYLE[r.classification]}">'
            f'{r.classification.value}</span></td>'
            f'<td>{_fmt(cur.config.compdev) if cur else "–"}</td><td>{_pct(cur.score.retention) if cur else "–"}</td>'
            f'<td>{_count(cur.events_per_day) if cur else "–"}</td>'
            f'<td>{_fmt(rec.config.compdev) if rec else "–"}</td>'
            f'<td>{_fmt(rec.config.excdev) if rec and opts.input_mode == "raw" else "–"}</td>'
            f'<td>{_pct(rec.score.retention) if rec else "–"}</td><td>{_count(rec.events_per_day) if rec else "–"}</td>'
            f'<td>{"yes" if r.ready_to_export else ""}</td></tr>')
    table = ('<div class="wrap"><table><thead><tr><th>Tag</th><th class="l">Verdict</th><th>CompDev</th><th>Retention</th>'
             '<th>Events/day</th><th>Proposed CompDev</th><th>Proposed ExcDev</th><th>Proposed retention</th><th>Proposed events/day</th>'
             f'<th>Ready to export</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
    sections = "".join(_point_section(r, series.get(r.tag, Series([], [])), opts) for r in results)
    mode = ("raw interface values (exception and compression simulated)" if opts.input_mode == "raw"
            else "the snapshot stream (compression simulated, ExcDev left as configured)")
    meta = (f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Target retention {opts.target:.0%}, "
            f"scored per {opts.window_s / 3600:g} h window at the {opts.percentile:g}th percentile. Input: {mode}. "
            "Recommendations only: nothing has been written to PI.")
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{html.escape(title)}</title><style>{_CSS}</style></head><body><main>'
           f'<header><h1>{html.escape(title)}</h1><p class="meta">{html.escape(meta)}</p></header>'
           f'<div class="tiles">{"".join(tiles)}</div>{table}{sections}</main></body></html>')
    Path(path).write_text(doc, encoding="utf-8")
