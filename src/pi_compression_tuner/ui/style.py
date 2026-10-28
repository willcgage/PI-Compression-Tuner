"""Visual style for the UI: CSS, small HTML components and a shared Plotly layout."""

from __future__ import annotations

import html

from pi_compression_tuner.tune import Classification

FONT = '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif'
MONO = '"IBM Plex Mono", ui-monospace, Consolas, monospace'

INK = {
    "text": "#17222a", "muted": "#5b6a75", "rule": "#dde4e8", "panel": "#f4f7f8",
    "input": "#9aa6af", "now": "#c0702b", "proposal": "#13708a", "probe": "#6d4bb3", "loss": "#d0574a",
}
VERDICT_COLOR = {
    Classification.ON_TARGET: "#2f7a3c", Classification.STORING_NOISE: "#b8661f",
    Classification.LOSING_DETAIL: "#c0392b", Classification.LITTLE_TO_GAIN: "#4b5d9a",
    Classification.RANGE_MISMATCH: "#4b5d9a", Classification.NOT_MOVING: "#7b8790",
    Classification.TOO_FEW_EVENTS: "#7b8790",
}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');
.stApp {{ font-family: {FONT}; }}
.stMarkdown p, .stMarkdown li, [data-testid="stWidgetLabel"] p, input, textarea {{ font-family: {FONT}; }}
.block-container {{ padding-top: 2.2rem; padding-bottom: 3rem; max-width: 1280px; }}
h1, h2, h3, h4 {{ font-family: {FONT}; letter-spacing: -0.01em; }}
[data-testid="stSidebar"] {{ background: {INK['panel']}; border-right: 1px solid {INK['rule']}; }}
[data-testid="stSidebar"] h3 {{ font-size: .78rem; text-transform: uppercase; letter-spacing: .08em; color: {INK['muted']};
  margin: 1.1rem 0 .2rem; }}
.stTabs [data-baseweb="tab-list"] {{ gap: 1.6rem; border-bottom: 1px solid {INK['rule']}; }}
.stTabs [data-baseweb="tab"] {{ font-weight: 600; padding: .6rem 0; }}
.stDownloadButton button, .stButton button {{ border-radius: 6px; font-weight: 500; }}

.pct-brand {{ display:flex; flex-direction:column; gap:2px; margin-bottom:.4rem; }}
.pct-brand b {{ font-size:1.05rem; color:{INK['text']}; }}
.pct-brand span {{ font-size:.8rem; color:{INK['muted']}; }}

.pct-header {{ display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between; gap:12px;
  padding-bottom:14px; margin-bottom:6px; border-bottom:2px solid {INK['text']}; }}
.pct-header h1 {{ margin:0; padding:0; font-size:2rem; font-weight:700; color:{INK['text']}; }}
.pct-header p {{ margin:4px 0 0; color:{INK['muted']}; font-size:.95rem; max-width:80ch; }}
.pct-source {{ font-family:{MONO}; font-size:.78rem; color:{INK['muted']}; background:{INK['panel']};
  border:1px solid {INK['rule']}; border-radius:999px; padding:4px 12px; white-space:nowrap; }}
.pct-readonly {{ font-size:.78rem; color:{INK['proposal']}; font-weight:600; white-space:nowrap; }}

.pct-kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:14px 0 6px; }}
.pct-kpi {{ border:1px solid {INK['rule']}; border-radius:8px; padding:14px 16px; background:#fff; }}
.pct-kpi .l {{ font-size:.75rem; text-transform:uppercase; letter-spacing:.06em; color:{INK['muted']}; }}
.pct-kpi .v {{ font-size:1.7rem; font-weight:600; color:{INK['text']}; font-variant-numeric:tabular-nums; line-height:1.3; }}
.pct-kpi .s {{ font-size:.82rem; color:{INK['muted']}; }}
.pct-kpi .s b.down {{ color:{INK['proposal']}; }} .pct-kpi .s b.up {{ color:{INK['now']}; }}

.pct-chips {{ display:flex; flex-wrap:wrap; gap:8px; margin:6px 0 2px; }}
.pct-chip {{ display:inline-flex; align-items:center; gap:6px; font-size:.82rem; font-weight:500; padding:3px 10px;
  border-radius:999px; border:1px solid currentColor; white-space:nowrap; }}
.pct-chip i {{ width:8px; height:8px; border-radius:50%; background:currentColor; display:inline-block; }}
.pct-chip b {{ font-variant-numeric:tabular-nums; }}

.pct-section {{ font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:{INK['muted']};
  margin:18px 0 4px; font-weight:600; }}
.pct-note {{ font-size:.85rem; color:{INK['muted']}; margin-top:-4px; }}

.pct-point {{ display:flex; flex-wrap:wrap; align-items:center; gap:10px 14px; margin:6px 0 2px; }}
.pct-point h3 {{ margin:0; padding:0; font-family:{MONO}; font-size:1.25rem; font-weight:500; }}
.pct-point .meta {{ font-size:.85rem; color:{INK['muted']}; }}

.pct-compare.n2 {{ grid-template-columns:auto repeat(2,minmax(0,1fr)); }}
.pct-compare.n3 {{ grid-template-columns:auto repeat(3,minmax(0,1fr)); }}
.pct-compare {{ display:grid; font-size:.86rem;
  font-variant-numeric:tabular-nums; margin:4px 0 6px; }}
.pct-compare > div {{ padding:6px 3px; border-bottom:1px solid {INK['rule']}; text-align:right; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }}
.pct-compare > div.h {{ font-weight:600; }}
.pct-compare > div.k {{ text-align:left; color:{INK['muted']}; }}
.pct-compare > div.ok {{ color:#2f7a3c; }} .pct-compare > div.bad {{ color:#c0392b; }}

.pct-steps {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:14px; margin-top:18px; }}
.pct-step {{ border:1px solid {INK['rule']}; border-radius:8px; padding:16px 18px; background:#fff; }}
.pct-step .n {{ font-family:{MONO}; font-size:.8rem; color:{INK['proposal']}; }}
.pct-step b {{ display:block; margin:4px 0; font-size:1rem; }}
.pct-step span {{ font-size:.88rem; color:{INK['muted']}; }}
</style>
"""


def esc(x) -> str:
    return html.escape(str(x))


def header(source_note: str) -> str:
    src = f'<span class="pct-source">{esc(source_note)}</span>' if source_note else ""
    return (f'<div class="pct-header"><div><h1>PI Compression Tuner</h1>'
            '<p>Finds the compression each point needs from how its own signal behaves. '
            '<span class="pct-readonly">Read-only:</span> changes leave as a PI Builder sheet for you to review and publish.</p>'
            f'</div>{src}</div>')


def kpis(items: list[tuple[str, str, str]]) -> str:
    cells = "".join(f'<div class="pct-kpi"><div class="l">{esc(l)}</div><div class="v">{esc(v)}</div>'
                    f'<div class="s">{s}</div></div>' for l, v, s in items)
    return f'<div class="pct-kpis">{cells}</div>'


def chip(c: Classification, count: int | None = None) -> str:
    n = f" <b>{count}</b>" if count is not None else ""
    return f'<span class="pct-chip" style="color:{VERDICT_COLOR[c]}"><i></i>{esc(c.value)}{n}</span>'


def chips(counts: dict) -> str:
    return '<div class="pct-chips">' + "".join(chip(c, n) for c, n in counts.items()) + "</div>"


def section(title: str) -> str:
    return f'<div class="pct-section">{esc(title)}</div>'


def steps(items: list[tuple[str, str]]) -> str:
    return '<div class="pct-steps">' + "".join(
        f'<div class="pct-step"><span class="n">{i:02d}</span><b>{esc(t)}</b><span>{esc(d)}</span></div>'
        for i, (t, d) in enumerate(items, 1)) + "</div>"


def plot_layout(fig, height: int, legend_y: float = 1.02, **kw):
    """One look for every chart: plain white, faint grid, legend above the plot."""
    fig.update_layout(
        template="plotly_white", height=height, margin=dict(l=8, r=8, t=36, b=8),
        font=dict(family=FONT, size=12, color=INK["text"]),
        legend=dict(orientation="h", yanchor="bottom", y=legend_y, xanchor="left", x=0, font=dict(size=12),
                    bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="white", bordercolor=INK["rule"], font=dict(family=FONT, size=12)),
        **kw,
    )
    fig.update_xaxes(gridcolor="#eef2f4", zeroline=False, linecolor=INK["rule"], ticks="outside", tickcolor=INK["rule"],
                     title_font=dict(size=12, color=INK["muted"]))
    fig.update_yaxes(gridcolor="#eef2f4", zeroline=False, linecolor=INK["rule"],
                     title_font=dict(size=12, color=INK["muted"]))
    return fig
