"""Plain-language and tabular views of a tuning result, shared by the web UI and the HTML report."""

from __future__ import annotations

import html

import numpy as np

from .models import PointConfig, Series
from .tune import Classification, TuneResult


def fmt_count(x: float) -> str:
    return f"{x:,.0f}" if x >= 100 else f"{x:.3g}"


def fmt_num(x: float) -> str:
    return f"{x:.4g}"


def fmt_change(pct: float, sign: bool = True) -> str:
    """A percent change that never rounds a near-total cut up to 100%."""
    d = 2 if 99.9 <= abs(pct) < 100 else (1 if abs(pct) >= 10 or sign else 0)
    return f"{pct:+.{d}f}%" if sign else f"{abs(pct):.{d}f}%"


def fmt_secs(x: float) -> str:
    for unit, n in (("h", 3600), ("min", 60)):
        if x >= n and x % n == 0:
            return f"{x / n:g} {unit}"
    return f"{x:g} s"


def proposal(r: TuneResult):
    """The proposed evaluation when it changes anything, else None."""
    return r.recommended if (r.recommended is not None and r.changes) else None


def attr_rows(now: PointConfig, new: PointConfig | None) -> list[tuple]:
    """Point attributes, now against proposed: (name, now, new, changed, secondary)."""
    rows = []
    nw = new or now

    def add(name, a, b, fmt=fmt_num, sub=False):
        changed = new is not None and not np.isclose(float(a), float(b))
        rows.append((name, fmt(a), fmt(b) if new is not None else "", changed, sub))

    add("ExcDev", now.excdev, nw.excdev)
    add("ExcMin", now.excmin, nw.excmin, fmt_secs)
    add("ExcMax", now.excmax, nw.excmax, fmt_secs)
    add("CompDev", now.compdev, nw.compdev)
    add("CompDev % span", now.compdevpercent, nw.compdevpercent, lambda x: f"{x:.3g}%", sub=True)
    add("CompMin", now.compmin, nw.compmin, fmt_secs)
    add("CompMax", now.compmax, nw.compmax, fmt_secs)
    add("Compressing", int(now.compressing), int(nw.compressing), lambda x: "On" if x else "Off")
    add("Zero", now.zero, nw.zero, sub=True)
    add("Span", now.span, nw.span, sub=True)
    add("Step", int(now.step), int(nw.step), lambda x: "Yes" if x else "No", sub=True)
    return rows


def fmt_ratio(x: float) -> str:
    """A compression ratio (input events per archived event) for display."""
    if not np.isfinite(x):
        return "∞"
    return f"{x:,.0f}" if x >= 10 else f"{x:.1f}"


def signal_profile(r: TuneResult, s: Series, cfg: PointConfig) -> list[tuple[str, str]]:
    days = max(s.duration, 1) / 86400
    dt = np.diff(s.t)
    items = [("Input events", f"{len(s):,}"), ("Period", f"{days:.1f} days"),
             ("Typical interval", fmt_secs(round(float(np.median(dt)), 1)) if len(dt) else "n/a"),
             ("Observed range", f"{s.v.min():.4g} to {s.v.max():.4g}" if len(s) else "n/a"),
             ("Noise σ", f"{r.noise:.3g}")]
    if cfg.span > 0 and len(s):
        items.append(("Range used", f"{100 * np.ptp(s.v) / cfg.span:.0f}% of span"))
    prop = proposal(r)
    if prop is not None and r.noise > 0:
        items.append(("Proposed CompDev", f"{prop.config.compdev / r.noise:.1f}× noise σ"))
    return items


def finding(r: TuneResult, target: float, adopted: bool = False) -> str:
    """One or two sentences saying what is wrong and what the proposal does. Returns HTML."""
    c, cur, prop = r.classification, r.current, proposal(r)
    if cur is None:
        return f"<b>{html.escape(c.value)}.</b> {html.escape(r.notes[0] if r.notes else '')} Nothing to tune for this point."
    now = f"{fmt_count(cur.events_per_day)} events a day"
    if prop is not None:
        cut = 100 * (1 - prop.events_per_day / max(cur.events_per_day, 1e-9))
        pct = fmt_change(cut, sign=False) if abs(cut) >= 99 else f"{abs(cut):.0f}%"
        how = (f"CompDev <b>{fmt_num(prop.config.compdev)}</b> keeps {prop.score.retention:.1%} of the signal's movement "
               f"with {fmt_count(prop.events_per_day)} events a day, {pct} {'fewer' if cut > 0 else 'more'}.")
    lead = {
        Classification.STORING_NOISE: f"<b>Storing noise.</b> The archive keeps {now}, far more than the signal needs. ",
        Classification.LOSING_DETAIL: f"<b>Losing detail.</b> Today the archive keeps {cur.score.retention:.1%} of the "
                                      f"signal's movement, below the {target:.1%} target. ",
        Classification.ON_TARGET: f"<b>On target.</b> Current settings keep {cur.score.retention:.1%} of the signal's "
                                  f"movement with {now}. No change is proposed.",
        Classification.LITTLE_TO_GAIN: "<b>Little to gain.</b> ",
        Classification.RANGE_MISMATCH: "<b>Range mismatch.</b> Check Zero and Span before changing compression. ",
    }.get(c, f"<b>{html.escape(c.value)}.</b> ")
    if c == Classification.ON_TARGET and not adopted:
        return lead
    if c == Classification.LITTLE_TO_GAIN and r.notes:
        lead += html.escape(r.notes[0]) + " "
    if prop is None:
        return lead + "Not exported by default; review the trend before changing anything."
    return lead + ("The adopted probe value: " if adopted else "") + how


def outcome_cards(r: TuneResult, target: float, span: float, colors: dict) -> list[dict]:
    """Now-versus-proposed cards for volume, retention, largest error and yearly volume.

    ``colors`` maps "now" and "proposal" to CSS colors for the paired bars.
    """
    cur, prop = r.current, proposal(r)
    if cur is None:
        return []
    new = prop or cur
    hi = np.log10(max(cur.events_per_day, new.events_per_day, 10) * 1.2)
    frac = lambda x: float(np.log10(max(x, 1)) / hi)  # noqa: E731
    pair = lambda a, b: [("Now", a, colors["now"])] + ([("Proposed", b, colors["proposal"])] if prop else [])  # noqa: E731
    cut = 100 * (new.events_per_day - cur.events_per_day) / max(cur.events_per_day, 1e-9)
    ok = new.score.retention >= target
    emax = max(cur.score.max_error, new.score.max_error, 1e-12) * 1.15
    err_sub = f"{new.score.max_error / r.noise:.1f}× noise σ" if r.noise else ""
    if span > 0:
        err_sub += f" · {100 * new.score.max_error / span:.2g}% of span"
    err_sub += f" · RMS {new.score.rms_error:.3g}"
    yearly = new.events_per_day * 365
    return [
        dict(label="Archive events per day", value=fmt_count(new.events_per_day),
             delta=(fmt_change(cut), "good" if cut <= 0 else "warn") if prop else None,
             sub=(f"from {fmt_count(cur.events_per_day)} today" if prop else "unchanged")
             + f" · keeps 1 in {fmt_ratio(new.compression_ratio)}",
             bars=pair(frac(cur.events_per_day), frac(new.events_per_day))),
        dict(label="Retention", value=f"{new.score.retention:.1%}",
             delta=("meets target", "good") if ok else ("below target", "warn"),
             sub=(f"from {cur.score.retention:.1%} · " if prop else "") + f"target {target:.1%}",
             bars=pair(cur.score.retention, new.score.retention), tick=target),
        dict(label="Largest error", value=f"{new.score.max_error:.3g}", sub=err_sub.strip(" ·"),
             bars=pair(cur.score.max_error / emax, new.score.max_error / emax)),
        dict(label="Archive events per year", value=f"{yearly:,.0f}" if yearly < 1e6 else f"{yearly / 1e6:.1f} M",
             sub=f"from {cur.events_per_day * 365:,.0f}" if prop else "at current settings"),
    ]


# --- HTML fragments (class names styled by the UI's CSS and the report's CSS) ---

def _esc(x) -> str:
    return html.escape(str(x))


def _bar(label: str, frac: float, color: str, tick: float | None = None) -> str:
    frac = min(max(frac, 0.0), 1.0)
    t = f'<span class="tick" style="left:{100 * min(max(tick, 0), 1):.1f}%"></span>' if tick is not None else ""
    return (f'<span>{_esc(label)}</span><span class="track"><span class="fill" '
            f'style="width:{max(100 * frac, 1.5):.1f}%;background:{color}"></span>{t}</span>')


def outcome(cards: list[dict]) -> str:
    """Cards with a headline value, a delta badge, a sub-line and paired now/proposed bars.

    Each card: label, value, sub (HTML), optional delta=(text, 'good'|'warn'), optional
    bars=[(label, fraction, color)] and tick (fraction).
    """
    out = []
    for c in cards:
        d = f'<span class="d {c["delta"][1]}">{_esc(c["delta"][0])}</span>' if c.get("delta") else ""
        bars = "".join(_bar(l, f, col, c.get("tick")) for l, f, col in c.get("bars", []))
        bars = f'<div class="pct-bars">{bars}</div>' if bars else ""
        out.append(f'<div class="pct-oc"><div class="l">{_esc(c["label"])}</div><div class="row">'
                   f'<span class="v">{_esc(c["value"])}</span>{d}</div><div class="s">{c.get("sub", "")}</div>{bars}</div>')
    return '<div class="pct-outcome">' + "".join(out) + "</div>"


def attrs_table(rows: list[tuple], proposed: bool) -> str:
    """Point attributes, now against proposed. Rows: (name, now, new, changed, is_sub)."""
    head = "<tr><th>Attribute</th><th>Now</th>" + ("<th>Proposed</th>" if proposed else "") + "</tr>"
    body = []
    for name, now, new, changed, sub in rows:
        cls = "chg" if changed else ("sub" if sub else "")
        newcell = (f'<td class="{"new" if changed else "same"}">{_esc(new)}</td>' if proposed else "")
        body.append(f'<tr class="{cls}"><td>{_esc(name)}</td><td>{_esc(now)}</td>{newcell}</tr>')
    return f'<table class="pct-attrs">{head}{"".join(body)}</table>'


def profile(items: list[tuple[str, str]]) -> str:
    return '<dl class="pct-profile">' + "".join(f"<dt>{_esc(k)}</dt><dd>{_esc(v)}</dd>" for k, v in items) + "</dl>"


def notes(items: list[str]) -> str:
    return '<ul class="pct-notes">' + "".join(f"<li>{_esc(n)}</li>" for n in items) + "</ul>" if items else ""
