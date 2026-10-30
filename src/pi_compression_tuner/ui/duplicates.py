"""The Duplicates page: points among those loaded that store the same signal.

Works on whatever the sidebar loaded (demo, CSV files or PI). Read-only: it suggests which copy to keep and leaves
the decision, and any change in PI, to the person.
"""

from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from pi_compression_tuner.duplicates import DuplicateOptions, Match, describe, find_duplicates, groups_frame, pairs_frame
from pi_compression_tuner.models import Series
from pi_compression_tuner.ui import style
from pi_compression_tuner.ui.style import INK

PLOT_POINTS = 20_000  # most points drawn per line
MATCH_COLOR = {Match.EXACT: "#a33b2f", Match.SAME: "#b8661f", Match.SCALED: "#6d4bb3", Match.LOOSE: "#4b5d9a",
               Match.NAME: "#7b8790"}
LINE_COLORS = [INK["proposal"], INK["now"], INK["probe"], "#2f7a3c", "#4b5d9a"]


def _chip(m: Match, count: int | None = None) -> str:
    n = f" <b>{count}</b>" if count is not None else ""
    return f'<span class="pct-chip" style="color:{MATCH_COLOR[m]}"><i></i>{style.esc(m.value)}{n}</span>'


def _thin(t: np.ndarray, v: np.ndarray):
    if len(t) <= PLOT_POINTS:
        return t, v
    i = np.linspace(0, len(t) - 1, PLOT_POINTS).astype(int)
    return t[i], v[i]


def _chart(members: list[str], series: dict[str, Series]):
    """Each copy laid over the kept point, rescaled onto its units when it is a scaled copy, and the difference below."""
    keep = series[members[0]]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.04)
    ts = lambda t: pd.to_datetime(t, unit="s", utc=True)  # noqa: E731
    t, v = _thin(keep.t, keep.v)
    fig.add_trace(go.Scatter(x=ts(t), y=v, name=f"{members[0]} (keep)", mode="lines",
                             line=dict(color=LINE_COLORS[0], width=2.2)), row=1, col=1)
    for i, tag in enumerate(members[1:], 1):
        s = series[tag]
        color = LINE_COLORS[i % len(LINE_COLORS)]
        on_keep = np.interp(keep.t, s.t, s.v)
        slope, offset = np.polyfit(on_keep, keep.v, 1) if np.ptp(on_keep) > 0 else (1.0, 0.0)
        rescaled = abs(slope - 1) > 1e-3 or abs(offset) > 1e-3 * (np.ptp(keep.v) or 1)
        if not rescaled:
            slope, offset = 1.0, 0.0  # plot the copy as stored, so an exact copy shows a flat zero difference
        mt, mv = _thin(s.t, slope * s.v + offset)
        fig.add_trace(go.Scatter(x=ts(mt), y=mv, name=f"{tag}{' (rescaled)' if rescaled else ''}", mode="lines",
                                 line=dict(color=color, width=1.2, dash="dot")), row=1, col=1)
        dt, dv = _thin(keep.t, keep.v - (slope * on_keep + offset))
        fig.add_trace(go.Scatter(x=ts(dt), y=dv, name=f"Difference, {tag}", mode="lines", showlegend=False,
                                 line=dict(color=color, width=1)), row=2, col=1)
    fig.update_yaxes(title_text="Kept point's units", row=1, col=1)
    fig.update_yaxes(title_text="Difference", row=2, col=1)
    return style.plot_layout(fig, 460)


def render(series: dict[str, Series] | None, infos: dict, source_note: str):
    ss = st.session_state
    st.markdown(style.header(source_note), unsafe_allow_html=True)
    st.markdown(style.section("Duplicate points"), unsafe_allow_html=True)
    st.markdown('<p class="pct-note">Points that store the same signal under two names, such as '
                "<code>tag.value</code> and <code>tag.scaled</code> holding identical values. Each copy costs archive "
                "space. Values and timestamps decide; names, instrument tags and descriptions are shown as hints. "
                "Compare points over the same period.</p>", unsafe_allow_html=True)
    if not series:
        st.info("Load points in the sidebar first: demo data, CSV files or archived values from PI.")
        return

    c1, c2, _ = st.columns([3, 2, 4], vertical_alignment="center")
    loose = c1.checkbox("Also list signals that only move together", key="dup_loose",
                        help="Very closely correlated points that are not copies, such as redundant transmitters.")
    key = (id(series), loose)
    if c2.button("Find duplicates", type="primary", key="dup_run", use_container_width=True) or (
            ss.get("dup_key") != key and ss.get("dup_report") is not None):
        bar = st.progress(0.0, text="Comparing points")
        ss.dup_report = find_duplicates(series, infos, DuplicateOptions(include_loose=loose),
                                        progress=lambda i, n: bar.progress(i / n, text=f"Checked {i} of {n} pairs"))
        ss.dup_key = key
        bar.empty()
    rep = ss.get("dup_report") if ss.get("dup_key") == key else None
    if rep is None:
        st.markdown(f'<p class="pct-note">{len(series)} points loaded. Press <b>Find duplicates</b>.</p>',
                    unsafe_allow_html=True)
        return

    st.markdown(style.kpis([
        ("Points compared", f"{rep.compared}", f"of {len(series)} loaded; the rest are flat or nearly empty"),
        ("Copy groups", f"{len(rep.groups)}", "points storing one signal"),
        ("Points that look like copies", f"{rep.redundant_points}", "beyond the one to keep in each group"),
        ("Copy events per day", f"{rep.redundant_per_day:,.0f}", "archive events the copies add"),
    ]), unsafe_allow_html=True)

    if not rep.groups:
        st.success("No copies found among the loaded points.")
    else:
        counts = Counter(g.match for g in rep.groups)
        st.markdown('<div class="pct-chips">' + "".join(_chip(m, n) for m, n in counts.items()) + "</div>",
                    unsafe_allow_html=True)
        st.dataframe(groups_frame(rep), hide_index=True, use_container_width=True,
                     column_config={"Copy events/day": st.column_config.NumberColumn(format="%.0f")})

        labels = {i: f"{i} · {g.match.value} · {g.keep} and {len(g.members) - 1} more"
                  for i, g in enumerate(rep.groups, 1)}
        gid = st.selectbox("Group", list(labels), format_func=labels.get, key="dup_group")
        g = rep.groups[gid - 1]
        st.markdown(f'<div class="pct-point"><h3>{style.esc(g.keep)}</h3>{_chip(g.match)}'
                    f'<span class="meta">suggested keep · {len(g.members) - 1} cop{"y" if len(g.members) == 2 else "ies"} '
                    f'· {g.redundant_per_day:,.0f} copy events/day</span></div>', unsafe_allow_html=True)
        for p in g.pairs:
            st.markdown(style.finding(style.esc(describe(p)), MATCH_COLOR[p.match]), unsafe_allow_html=True)
        st.plotly_chart(_chart(g.members, series), use_container_width=True, key=f"dup_chart_{gid}",
                        config={"displaylogo": False})
        st.markdown('<p class="pct-note">The suggested keep has the plainest name, then the most events. Before '
                    "retiring a copy, check the displays, calculations and interfaces that use each name. "
                    "Nothing here changes PI.</p>", unsafe_allow_html=True)

    if rep.loose:
        st.markdown(style.section(f"Move together, not copies ({len(rep.loose)})"), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"Point A": p.a, "Point B": p.b, "Correlation": p.r, "Hints": "; ".join(p.hints)}
                                   for p in rep.loose]), hide_index=True, use_container_width=True)
    if rep.name_only:
        st.markdown(style.section(f"Look-alike names to check by hand ({len(rep.name_only)})"), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"Point A": p.a, "Point B": p.b, "Hints": "; ".join(p.hints)}
                                   for p in rep.name_only]), hide_index=True, use_container_width=True)

    st.download_button("Download duplicates table", pairs_frame(rep).to_csv(index=False, float_format="%.6g"),
                       "duplicates.csv", "text/csv", key="dup_download")
