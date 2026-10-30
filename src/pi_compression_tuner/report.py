"""Self-contained HTML report: a run summary, a linked table of points, and one review page per point.

Each point page has the same layout as the UI's Point detail tab: a plain-language finding,
now-versus-proposed cards, the trend (whole period with a detail-lost strip, plus the scoring
window around the largest miss), the settings sheet and the signal profile. Charts are inline
SVG, so the file opens anywhere, offline, and prints one point per page.
"""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import present
from .metrics import reconstruct
from .models import Series
from .simulate import simulate_archive
from .tune import Classification, TuneOptions, TuneResult

_VERDICT_CLASS = {
    Classification.ON_TARGET: "v-ok",
    Classification.STORING_NOISE: "v-noise",
    Classification.LOSING_DETAIL: "v-loss",
    Classification.LITTLE_TO_GAIN: "v-info",
    Classification.RANGE_MISMATCH: "v-info",
    Classification.NOT_MOVING: "v-none",
    Classification.TOO_FEW_EVENTS: "v-none",
}

_CSS = """
:root{--bg:#f4f7f8;--panel:#fff;--ink:#17222a;--muted:#5b6a75;--rule:#dde4e8;--soft:#eef2f4;
--raw:#9aa6af;--cur:#c0702b;--rec:#13708a;--loss:#d0574a;--loss-fill:rgba(208,87,74,.22);--hi:rgba(19,112,138,.07);
--v-ok:#2f7a3c;--v-noise:#b8661f;--v-loss:#c0392b;--v-info:#4b5d9a;--v-none:#7b8790;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0f161b;--panel:#162028;--ink:#e3eaee;--muted:#9aabb6;--rule:#2a3843;
--soft:#1d2932;--raw:#6b7a85;--cur:#f0914f;--rec:#5cc3da;--loss:#f08378;--loss-fill:rgba(240,131,120,.22);
--hi:rgba(92,195,218,.09);--v-ok:#79c486;--v-noise:#f0b44a;--v-loss:#ec8a7f;--v-info:#9fb0ee;--v-none:#9aa6af;
color-scheme:dark}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "IBM Plex Sans","Segoe UI",system-ui,sans-serif;
padding:32px max(16px,4vw) 64px}
main{max-width:1120px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
h1{margin:0;font-size:1.9rem;letter-spacing:-.01em}h2{margin:0}p{margin:0}
a{color:var(--rec)}
.mono{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace}
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:12px;padding-bottom:14px;
border-bottom:2px solid var(--ink)}
.top p{color:var(--muted);max-width:78ch;margin-top:4px}
.ro{color:var(--rec);font-weight:600}
.badge{font:12px/1 "IBM Plex Mono",ui-monospace,Consolas,monospace;color:var(--muted);background:var(--panel);
border:1px solid var(--rule);border-radius:999px;padding:6px 12px;white-space:nowrap}
.sec{font-size:.75rem;text-transform:uppercase;letter-spacing:.08em;color:var(--muted);font-weight:600;margin:6px 0 -10px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}
.kpi{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:14px 16px}
.kpi .l,.pct-oc .l{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.kpi .v{font-size:1.7rem;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.3}
.kpi .s{font-size:.82rem;color:var(--muted)}
.down{color:var(--rec)}.up{color:var(--cur)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{display:inline-flex;align-items:center;gap:6px;font-size:.8rem;font-weight:500;padding:2px 10px;border-radius:999px;
border:1px solid currentColor;white-space:nowrap}
.chip i{width:7px;height:7px;border-radius:50%;background:currentColor}
.v-ok{color:var(--v-ok)}.v-noise{color:var(--v-noise)}.v-loss{color:var(--v-loss)}.v-info{color:var(--v-info)}
.v-none{color:var(--v-none)}
.wrap{overflow-x:auto;background:var(--panel);border:1px solid var(--rule);border-radius:8px}
table.list{border-collapse:collapse;width:100%;font-size:.86rem;font-variant-numeric:tabular-nums}
.list th,.list td{padding:8px 12px;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap}
.list th{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}
.list th:nth-child(-n+2),.list td:nth-child(-n+2){text-align:left}
.list tr:last-child td{border-bottom:0}
.list .arrow{color:var(--muted);padding:0 4px}
.list .new{color:var(--rec);font-weight:600}
.point{background:var(--panel);border:1px solid var(--rule);border-radius:10px;padding:22px 24px;display:flex;
flex-direction:column;gap:14px;scroll-margin-top:16px}
.phead{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}
.phead h2{font:500 1.3rem "IBM Plex Mono",ui-monospace,Consolas,monospace}
.phead .meta{font-size:.85rem;color:var(--muted)}
.phead .sheet{margin-left:auto;font-size:.82rem;color:var(--muted)}
.phead .sheet b{color:var(--rec)}
.pct-finding{font-size:1.02rem;line-height:1.55;border-left:3px solid var(--rec);padding:4px 0 4px 14px;max-width:95ch}
.pct-outcome{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.pct-oc{border:1px solid var(--rule);border-radius:8px;padding:12px 14px 10px}
.pct-oc .row{display:flex;align-items:baseline;gap:10px;margin-top:2px}
.pct-oc .v{font-size:1.5rem;font-weight:600;font-variant-numeric:tabular-nums}
.pct-oc .d{font-size:.76rem;font-weight:600;padding:1px 7px;border-radius:999px;background:var(--soft)}
.pct-oc .d.good{color:var(--rec)}.pct-oc .d.warn{color:var(--cur)}
.pct-oc .s{font-size:.8rem;color:var(--muted)}
.pct-bars{margin-top:8px;display:grid;grid-template-columns:62px 1fr;gap:4px 8px;align-items:center;font-size:.7rem;
color:var(--muted)}
.pct-bars .track{position:relative;height:7px;background:var(--soft);border-radius:4px}
.pct-bars .fill{position:absolute;left:0;top:0;bottom:0;border-radius:4px}
.pct-bars .tick{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--ink);opacity:.55}
.trend{display:grid;grid-template-columns:5fr 3fr;gap:16px}
.trend figure{margin:0}
.trend figcaption{font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;
margin-bottom:4px}
.trend svg{width:100%;height:auto;display:block}
.legend{display:flex;gap:16px;flex-wrap:wrap;font-size:.8rem;color:var(--muted)}
.sw{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px;border-radius:2px}
.two{display:grid;grid-template-columns:3fr 2fr;gap:24px}
.pct-attrs{width:100%;border-collapse:collapse;font-size:.86rem;font-variant-numeric:tabular-nums}
.pct-attrs th{text-align:right;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
font-weight:600;padding:6px 10px;border-bottom:1px solid var(--ink)}
.pct-attrs th:first-child,.pct-attrs td:first-child{text-align:left}
.pct-attrs td{text-align:right;padding:6px 10px;border-bottom:1px solid var(--rule)}
.pct-attrs td:first-child{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-size:.8rem}
.pct-attrs td.same{color:var(--muted)}
.pct-attrs tr.chg td{background:var(--hi)}
.pct-attrs tr.chg td.new{color:var(--rec);font-weight:600}
.pct-attrs tr.chg td:first-child{box-shadow:inset 3px 0 0 var(--rec)}
.pct-attrs tr.sub td{font-size:.78rem;color:var(--muted)}
.pct-profile{display:grid;grid-template-columns:auto 1fr;margin:0;font-size:.86rem}
.pct-profile dt,.pct-profile dd{padding:6px 0;border-bottom:1px solid var(--rule);margin:0}
.pct-profile dt{color:var(--muted);padding-right:16px}
.pct-profile dd{text-align:right;font-variant-numeric:tabular-nums}
.pct-notes{margin:12px 0 0;padding:0;list-style:none;font-size:.84rem;color:var(--muted)}
.pct-notes li{padding:5px 0 5px 18px;position:relative}
.pct-notes li::before{content:"";position:absolute;left:4px;top:12px;width:6px;height:6px;border-radius:50%;
background:var(--cur)}
.subh{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin-bottom:6px}
.back{font-size:.8rem;align-self:flex-end}
.empty{color:var(--muted)}
@media (max-width:820px){.trend,.two{grid-template-columns:1fr}.phead .sheet{margin-left:0}}
@media print{body{background:#fff;padding:0;font-size:12px}.point{break-before:page;border:0;padding:0}
.back{display:none}.wrap,.kpi,.pct-oc{break-inside:avoid}a{color:inherit;text-decoration:none}}
"""

_W, _H = 680, 230   # whole-period chart
_ZW, _ZH = 400, 230  # zoom chart


def _fmt(x, digits=4):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "–"
    return f"{x:.{digits}g}"


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


def _anchor(tag: str) -> str:
    return "pt-" + re.sub(r"[^A-Za-z0-9_-]+", "-", tag)


def _time_ticks(t0: float, t1: float, n: int = 5):
    """Round tick times and labels (UTC) for a span."""
    span = max(t1 - t0, 1.0)
    for step in (60, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400, 172800, 604800):
        if span / step <= n:
            break
    first = np.ceil(t0 / step) * step
    ticks = np.arange(first, t1 + 1e-9, step)
    fmt = "%H:%M" if step < 86400 else "%b %d"
    labels = []
    for k, x in enumerate(ticks):
        d = datetime.fromtimestamp(float(x), timezone.utc)
        lab = d.strftime(fmt)
        if step < 86400 and (k == 0 or d.hour == 0 and d.minute == 0):
            lab = d.strftime("%b %d %H:%M")
        labels.append(lab)
    return ticks, labels


def _chart(lines, t0, t1, w, h, strip=None, markers=False, clip_id="c"):
    """An SVG line chart. ``lines`` are (t, v, css-var, width); ``strip`` is (t, v) drawn as a filled band below."""
    sh = 52 if strip is not None else 0
    top, bottom, left, right = 8, 22, 46, 8
    ph = h - top - bottom - sh
    inside = [v[(t >= t0) & (t <= t1)] for t, v, _, _ in lines if len(v)]
    vals = np.concatenate(inside) if inside else np.array([0.0])
    if len(vals) == 0:
        vals = np.concatenate([v for _, v, _, _ in lines if len(v)])
    lo, hi = float(np.min(vals)), float(np.max(vals))
    if hi == lo:
        hi = lo + 1
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    pw = w - left - right
    sx = lambda t: left + (t - t0) / max(t1 - t0, 1e-9) * pw  # noqa: E731
    sy = lambda v: top + (hi - v) / (hi - lo) * ph  # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" font-family="inherit"><defs><clipPath id="{clip_id}">'
           f'<rect x="{left}" y="0" width="{pw}" height="{h}"/></clipPath></defs>']
    for frac in (0, 1 / 3, 2 / 3, 1):
        val = lo + (hi - lo) * frac
        y = sy(val)
        out.append(f'<line x1="{left}" x2="{w - right}" y1="{y:.1f}" y2="{y:.1f}" stroke="var(--soft)"/>')
        out.append(f'<text x="{left - 6}" y="{y + 3.5:.1f}" font-size="10" text-anchor="end" fill="var(--muted)">'
                   f'{_fmt(val, 4)}</text>')
    for t, v, color, width in lines:
        if len(t) == 0:
            continue
        pts = " ".join(f"{sx(a):.1f},{sy(b):.1f}" for a, b in zip(t, v))
        out.append(f'<polyline points="{pts}" fill="none" stroke="var({color})" stroke-width="{width}" '
                   f'stroke-linejoin="round" clip-path="url(#{clip_id})"/>')
        inside_t = (t >= t0) & (t <= t1)
        if markers and color != "--raw" and inside_t.sum() <= 150:
            r = 2.2 if color == "--cur" else 3.6
            out.extend(f'<circle cx="{sx(a):.1f}" cy="{sy(b):.1f}" r="{r}" fill="var({color})" stroke="var(--panel)" '
                       f'stroke-width="1"/>' for a, b in zip(t[inside_t], v[inside_t]))
    base = top + ph
    out.append(f'<line x1="{left}" x2="{w - right}" y1="{base:.1f}" y2="{base:.1f}" stroke="var(--rule)"/>')
    if strip is not None:
        st_, sv = strip
        s_top, s_bot = base + 14, base + sh - 4
        smax = max(float(np.max(sv)) if len(sv) else 0.0, 1e-12)
        ys = lambda v: s_bot - v / smax * (s_bot - s_top)  # noqa: E731
        if len(st_):
            pts = " ".join(f"{sx(a):.1f},{ys(b):.1f}" for a, b in zip(st_, sv))
            out.append(f'<polygon points="{sx(st_[0]):.1f},{s_bot:.1f} {pts} {sx(st_[-1]):.1f},{s_bot:.1f}" '
                       f'fill="var(--loss-fill)" stroke="var(--loss)" stroke-width="0.8" clip-path="url(#{clip_id})"/>')
        out.append(f'<line x1="{left}" x2="{w - right}" y1="{s_bot:.1f}" y2="{s_bot:.1f}" stroke="var(--rule)"/>')
        out.append(f'<text x="{left - 6}" y="{s_bot:.1f}" font-size="10" text-anchor="end" fill="var(--muted)">0</text>')
        out.append(f'<text x="{left - 6}" y="{s_top + 6:.1f}" font-size="10" text-anchor="end" fill="var(--muted)">'
                   f'{_fmt(smax, 2)}</text>')
    ticks, labels = _time_ticks(t0, t1, 5 if w > 500 else 3)
    for x, lab in zip(ticks, labels):
        out.append(f'<text x="{sx(x):.1f}" y="{h - 6}" font-size="10" text-anchor="middle" fill="var(--muted)">'
                   f'{html.escape(lab)}</text>')
    out.append("</svg>")
    return "".join(out)


def _legend(items):
    return '<div class="legend">' + "".join(
        f'<span><i class="sw" style="background:var({c})"></i>{html.escape(n)}</span>' for n, c in items) + "</div>"


def _point_section(r: TuneResult, s: Series, opts: TuneOptions, selected: bool) -> str:
    vc = _VERDICT_CLASS[r.classification]
    days = max(s.duration, 1) / 86400 if len(s) else 0
    prop = present.proposal(r)
    sheet = ""
    if r.changes:
        sheet = ('<span class="sheet">PI Builder sheet: <b>selected</b></span>' if selected
                 else '<span class="sheet">PI Builder sheet: listed, not selected</span>')
    head = (f'<div class="phead"><h2>{html.escape(r.tag)}</h2><span class="chip {vc}"><i></i>{r.classification.value}</span>'
            f'<span class="meta">{len(s):,} input events over {days:.1f} days</span>{sheet}</div>')
    finding = present.finding(r, opts.target)
    parts = [head, f'<div class="pct-finding" style="border-left-color:var(--{vc})">{finding}</div>']
    back = '<a class="back" href="#top">Back to all points</a>'
    if r.current is None or len(s) == 0:
        parts.append(present.notes([n for n in r.notes if html.escape(n) not in finding]))
        return f'<section class="point" id="{_anchor(r.tag)}">{"".join(parts)}{back}</section>'

    cfg = r.current.config
    parts.append(present.outcome(present.outcome_cards(r, opts.target, cfg.span,
                                                       {"now": "var(--cur)", "proposal": "var(--rec)"})))

    def archive_of(c):
        sim = simulate_archive(s, c, apply_exception=opts.input_mode == "raw")
        vis = sim.visible()
        return s.t[vis], s.v[vis]

    ct, cv = archive_of(cfg)
    judged = (*archive_of(prop.config), prop.config) if prop is not None else (ct, cv, cfg)
    err = np.abs(s.v - reconstruct(judged[0], judged[1], s.t, judged[2].step))
    excess = np.maximum(0, err - opts.noise_band * r.noise)

    full = [(*envelope(s.t, s.v, 1500), "--raw", 1.0), (*envelope(ct, cv, 1500), "--cur", 1.2)]
    if prop is not None:
        full.append((*envelope(judged[0], judged[1], 1500), "--rec", 1.8))
    whole = _chart(full, s.t[0], s.t[-1], _W, _H, strip=envelope(s.t, excess, 600), clip_id=f"w{_anchor(r.tag)}")

    # The scoring window around the largest miss, with every archived event marked.
    k = int(np.argmax(err))
    z0, z1 = s.t[k] - opts.window_s / 2, s.t[k] + opts.window_s / 2

    def sel(t, v):
        a = max(int(np.searchsorted(t, z0)) - 1, 0)
        b = min(int(np.searchsorted(t, z1, side="right")) + 1, len(t))
        return t[a:b], v[a:b]

    zoom = [(*sel(s.t, s.v), "--raw", 1.0), (*sel(ct, cv), "--cur", 1.0)]
    if prop is not None:
        zoom.append((*sel(judged[0], judged[1]), "--rec", 1.8))
    zchart = _chart(zoom, z0, z1, _ZW, _ZH, markers=True, clip_id=f"z{_anchor(r.tag)}")
    legend = [("Input", "--raw"), ("Archive now", "--cur")] + ([("Archive proposed", "--rec")] if prop else []) \
        + [("Detail lost", "--loss")]
    parts.append(_legend(legend))
    parts.append(f'<div class="trend"><figure><figcaption>Whole period</figcaption>{whole}</figure>'
                 f'<figure><figcaption>Largest miss · {opts.window_s / 60:g} min around the biggest error</figcaption>'
                 f'{zchart}</figure></div>')

    side = (f'<div><div class="subh">Signal</div>{present.profile(present.signal_profile(r, s, cfg))}'
            f'{present.notes([n for n in r.notes if html.escape(n) not in finding])}</div>')
    parts.append(f'<div class="two"><div><div class="subh">Settings</div>'
                 f'{present.attrs_table(present.attr_rows(cfg, prop.config if prop else None), proposed=prop is not None)}'
                 f'</div>{side}</div>')
    return f'<section class="point" id="{_anchor(r.tag)}">{"".join(parts)}{back}</section>'


def _arrow(now: str, new: str | None) -> str:
    if new is None or new == now:
        return now
    return f'{now}<span class="arrow">→</span><span class="new">{new}</span>'


def write_report(results: list[TuneResult], series: dict[str, Series], opts: TuneOptions, path: str | Path,
                 title: str = "Compression tuning report", selected: set[str] | None = None) -> None:
    """Write the report. ``selected`` names the points marked x in the PI Builder sheet
    (default: those ready to export)."""
    if selected is None:
        selected = {r.tag for r in results if r.changes and r.ready_to_export}
    counts = Counter(r.classification for r in results)
    cur_total = sum(r.current.events_per_day for r in results if r.current)
    new_total = sum((r.recommended or r.current).events_per_day for r in results if r.current)
    changes = sum(1 for r in results if r.changes)
    review = sum(1 for r in results if not r.ready_to_export and r.classification != Classification.ON_TARGET)
    delta = 100 * (new_total - cur_total) / cur_total if cur_total else 0.0
    kpis = [("Points", f"{len(results)}", f"target retention {opts.target:.1%}"),
            ("Proposed changes", f"{changes}", f"{len(selected & {r.tag for r in results})} selected in the sheet"),
            ("Need a closer look", f"{review}", "not selected by default" if review else "none"),
            ("Archive events / day", present.fmt_count(new_total),
             f'<b class="{"down" if delta <= 0 else "up"}">{delta:+.1f}%</b> from {present.fmt_count(cur_total)} today')]
    kpi_html = '<div class="kpis">' + "".join(
        f'<div class="kpi"><div class="l">{html.escape(l)}</div><div class="v">{html.escape(v)}</div>'
        f'<div class="s">{s}</div></div>' for l, v, s in kpis) + "</div>"
    chips = '<div class="chips">' + "".join(
        f'<span class="chip {_VERDICT_CLASS[c]}"><i></i>{c.value} <b>{counts[c]}</b></span>'
        for c in Classification if counts.get(c)) + "</div>"

    rows = []
    for r in results:
        cur, prop = r.current, present.proposal(r)
        vc = _VERDICT_CLASS[r.classification]
        ch = (present.fmt_change(100 * (prop.events_per_day - cur.events_per_day) / max(cur.events_per_day, 1e-9))
              if cur and prop else "")
        rows.append(
            f'<tr><td><a class="mono" href="#{_anchor(r.tag)}">{html.escape(r.tag)}</a></td>'
            f'<td><span class="chip {vc}"><i></i>{r.classification.value}</span></td>'
            f'<td>{_arrow(_fmt(cur.config.compdev), _fmt(prop.config.compdev) if prop else None) if cur else "–"}</td>'
            + (f'<td>{_arrow(_fmt(cur.config.excdev), _fmt(prop.config.excdev) if prop else None) if cur else "–"}</td>'
               if opts.input_mode == "raw" else "")
            + f'<td>{_arrow(f"{cur.score.retention:.1%}", f"{prop.score.retention:.1%}" if prop else None) if cur else "–"}</td>'
            f'<td>{_arrow(present.fmt_count(cur.events_per_day), present.fmt_count(prop.events_per_day) if prop else None) if cur else "–"}</td>'
            f'<td>{ch}</td><td>{"x" if r.tag in selected and r.changes else ""}</td></tr>')
    exc_head = "<th>ExcDev</th>" if opts.input_mode == "raw" else ""
    table = ('<div class="wrap"><table class="list"><thead><tr><th>Point</th><th>Verdict</th><th>CompDev</th>'
             f'{exc_head}<th>Retention</th><th>Events / day</th><th>Volume change</th><th>Selected</th></tr></thead>'
             f'<tbody>{"".join(rows)}</tbody></table></div>')
    sections = "".join(_point_section(r, series.get(r.tag, Series([], [])), opts, r.tag in selected) for r in results)
    mode = ("raw interface values (exception and compression replayed)" if opts.input_mode == "raw"
            else "the snapshot stream (compression replayed, ExcDev left as configured)")
    meta = (f"Target retention {opts.target:.1%}, scored per {opts.window_s / 3600:g} h window at the "
            f"{opts.percentile:g}th percentile. Input: {mode}. ")
    stamp = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>'
           '<link rel="preconnect" href="https://fonts.googleapis.com">'
           '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">'
           f'<style>{_CSS}</style></head><body><main>'
           f'<header class="top" id="top"><div><h1>{html.escape(title)}</h1><p>{html.escape(meta)}'
           '<span class="ro">Recommendations only:</span> nothing has been written to PI.</p></div>'
           f'<span class="badge">Generated {stamp}</span></header>'
           f'{kpi_html}{chips}<div class="sec">All points</div>{table}'
           f'<div class="sec">Point by point</div>{sections}</main></body></html>')
    Path(path).write_text(doc, encoding="utf-8")
