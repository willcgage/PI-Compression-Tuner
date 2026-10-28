"""PI Compression Tuner web UI (Streamlit).

Three tabs:
  * Overview: where every point sits on retention versus archive volume, with a table of verdicts.
  * Point detail: one point's finding in plain words, now-versus-proposed outcome and settings,
    the trend with a residual strip, and a trade-off curve with a probe to try any CompDev.
  * Export: the PI Builder sheet, results table and HTML report.
Recommendations only: nothing here writes to PI.
"""

from __future__ import annotations

import dataclasses
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from pi_compression_tuner import present
from pi_compression_tuner.demo import demo_data
from pi_compression_tuner.export import pibuilder_frame, results_frame
from pi_compression_tuner.metrics import reconstruct
from pi_compression_tuner.models import PointConfig, Series
from pi_compression_tuner.report import envelope, write_report
from pi_compression_tuner.simulate import simulate_archive
from pi_compression_tuner.sources.csvfiles import read_configs, read_series
from pi_compression_tuner.tune import Classification, TuneOptions, evaluate_config, tradeoff_curve, tune_point
from pi_compression_tuner.ui import style
from pi_compression_tuner.ui.style import INK, VERDICT_COLOR

WINDOWS = {"15 min": 900, "30 min": 1800, "1 h": 3600, "4 h": 14400}
PLOT_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]}

st.set_page_config(page_title="PI Compression Tuner", layout="wide", initial_sidebar_state="expanded")
st.markdown(style.CSS, unsafe_allow_html=True)
ss = st.session_state
for k, v in {"series": None, "configs": {}, "results": None, "adopted": {}, "source_note": "",
             "export_choice": {}, "table_v": 0, "curves": {}}.items():
    ss.setdefault(k, v)


def _reset_results():
    ss.results, ss.adopted, ss.curves, ss.export_choice = None, {}, {}, {}
    ss.table_v += 1


def _fmt_n(x: float) -> str:
    return f"{x:,.0f}" if x >= 100 else f"{x:.3g}"


# --- data loading ---------------------------------------------------------

def _read_upload(upload, reader):
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
        f.write(upload.getvalue())
        path = f.name
    try:
        return reader(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _load_pi(server, source, url, auth, verify, afsdk_dir, tags, name_filter, limit, start, end):
    from pi_compression_tuner.sources.connect import connect
    from pi_compression_tuner.sources.ops import fetch_recorded, resolve_points
    conn = connect(server, source=source, url=url or None, auth=auth, verify=verify, afsdk_dir=afsdk_dir or None)
    refs = resolve_points(conn.source, server, tags, name_filter or None, limit)
    configs = {r.name: conn.source.config(r) for r in refs}
    bar = st.progress(0.0, text="Reading archived values")
    series = fetch_recorded(conn.source, refs, start, end, progress=lambda i, n: bar.progress(i / n, text=f"Read {i} of {n} points"))
    bar.empty()
    via = "PI Web API" if conn.kind == "webapi" else "AF SDK"
    if conn.note:
        st.toast(conn.note)
    return series, configs, f"{server} · {len(refs)} points · {via}"


with st.sidebar:
    st.markdown('<div class="pct-brand"><b>PI Compression Tuner</b><span>Read-only · recommendations</span></div>',
                unsafe_allow_html=True)
    st.markdown("### Data")
    origin = st.radio("Source", ["Demo data", "CSV files", "PI server"], key="origin", label_visibility="collapsed",
                      help="CSV files can come from `pict pi record` (snapshot stream) or any export.")
    if origin == "Demo data":
        days = st.slider("Days of data", 1, 7, 3, key="demo_days")
        if st.button("Load demo data", type="primary", key="load_demo", use_container_width=True):
            ss.series, ss.configs = demo_data(days)
            ss.source_note = f"Demo · 4 points · {days} days"
            _reset_results()
    elif origin == "CSV files":
        data_up = st.file_uploader("Data", type="csv", key="data_up",
                                   help="Long (Tag, Timestamp, Value) or wide (Timestamp, one column per tag).")
        cfg_up = st.file_uploader("Point settings (optional)", type="csv", key="cfg_up",
                                  help="PI Builder column names: Name, ExcDev, CompDev, CompMax, Span, ...")
        if st.button("Load files", type="primary", disabled=data_up is None, key="load_csv", use_container_width=True):
            try:
                ss.series = _read_upload(data_up, read_series)
                ss.configs = _read_upload(cfg_up, read_configs) if cfg_up else {}
                ss.source_note = f"{data_up.name} · {len(ss.series)} points"
                _reset_results()
            except Exception as e:
                st.error(f"Could not read the files: {e}")
    else:
        server = st.text_input("Data Archive", key="pi_server")
        source = st.selectbox("Connect through", ["auto", "webapi", "afsdk"], key="pi_source",
                              format_func={"auto": "PI Web API, then AF SDK", "webapi": "PI Web API only",
                                           "afsdk": "AF SDK only"}.get)
        url = st.text_input("PI Web API URL", placeholder="https://pihost/piwebapi", key="pi_url",
                            disabled=source == "afsdk")
        tags_text = st.text_area("Tags, one per line", key="pi_tags", height=90)
        name_filter = st.text_input("or a tag filter", placeholder="FIC*", key="pi_filter")
        c1, c2 = st.columns(2)
        start = c1.text_input("Start", "*-7d", key="pi_start")
        end = c2.text_input("End", "*", key="pi_end")
        with st.expander("Connection options"):
            auth = st.selectbox("Web API sign-in", ["kerberos", "basic", "anonymous"], key="pi_auth",
                                help="basic reads PI_WEBAPI_USER and PI_WEBAPI_PASSWORD from the environment.")
            verify = st.checkbox("Check TLS certificate", value=True, key="pi_verify")
            afsdk_dir = st.text_input("AF SDK folder", key="pi_afsdk_dir")
            limit = st.number_input("Most points", 1, 5000, 100, key="pi_limit")
        st.caption("Reads archived values: right for points whose compression is off or very tight. "
                   "For the rest, record with `pict pi record` and load the CSV files.")
        if st.button("Read from PI", type="primary", disabled=not server, key="load_pi", use_container_width=True):
            try:
                tags = [t.strip() for t in tags_text.splitlines() if t.strip()]
                ss.series, ss.configs, ss.source_note = _load_pi(server, source, url, auth, verify, afsdk_dir,
                                                                 tags, name_filter, int(limit), start, end)
                _reset_results()
            except Exception as e:
                st.error(f"Could not read from PI: {e}")

    st.markdown("### Tuning")
    mode = st.radio("The data is", ["snapshot", "raw"], key="mode",
                    format_func={"snapshot": "Snapshot stream · tune CompDev",
                                 "raw": "Raw interface values · tune ExcDev and CompDev"}.get)
    target_pct = st.slider("Target retention", 80.0, 99.9, 95.0, 0.1, format="%.1f%%", key="target",
                           help="Share of the signal's movement, beyond its noise, that the archive must keep.")
    window = st.select_slider("Scoring window", list(WINDOWS), value="1 h", key="window")
    with st.expander("Error limits and ExcDev"):
        max_err = st.number_input("Largest error, engineering units", 0.0, value=0.0, key="max_err", help="0 = no limit")
        max_err_pct = st.number_input("Largest error, % of span", 0.0, 100.0, 0.0, key="max_err_pct", help="0 = no limit")
        excdev_ratio = st.number_input("ExcDev as a fraction of CompDev", 0.0, 1.0, 0.5, 0.05, key="excdev_ratio",
                                       disabled=mode != "raw")
    run = st.button("Run tuning", type="primary", disabled=ss.series is None, key="run", use_container_width=True)

opts = TuneOptions(target=target_pct / 100, input_mode=mode, window_s=WINDOWS[window], max_error=max_err or None,
                   max_error_pct_span=max_err_pct or None, excdev_ratio=excdev_ratio)

if run and ss.series is not None:
    results = []
    bar = st.progress(0.0, text="Tuning")
    items = list(ss.series.items())
    for i, (tag, s) in enumerate(items):
        results.append(tune_point(tag, s, ss.configs.get(tag, PointConfig()), opts))
        bar.progress((i + 1) / len(items), text=f"Tuned {i + 1} of {len(items)} points")
    bar.empty()
    _reset_results()
    ss.results, ss.opts = results, opts


# --- header and empty states ----------------------------------------------

st.markdown(style.header(ss.source_note), unsafe_allow_html=True)

if ss.series is None:
    st.markdown(style.steps([
        ("Load data", "Use the sidebar: demo data, CSV files from a recording, or archived values from PI."),
        ("Run tuning", "Pick a target retention. Every point gets a proposed CompDev and a verdict."),
        ("Review and export", "Check proposals on the trade-off curve, then download a PI Builder sheet."),
    ]), unsafe_allow_html=True)
    st.stop()
if ss.results is None:
    n = sum(len(s) for s in ss.series.values())
    st.markdown(style.kpis([("Points loaded", f"{len(ss.series)}", "ready to tune"),
                            ("Events", f"{n:,}", "across all points")]), unsafe_allow_html=True)
    st.markdown('<p class="pct-note">Choose a target retention in the sidebar, then press <b>Run tuning</b>.</p>',
                unsafe_allow_html=True)
    st.stop()

run_opts: TuneOptions = ss.get("opts", opts)


def effective(r):
    """The result with an adopted probe setting in place of the proposal."""
    a = ss.adopted.get(r.tag)
    return dataclasses.replace(r, recommended=a, notes=r.notes + ["CompDev adopted from the probe."]) if a else r


results = [effective(r) for r in ss.results]
by_tag = {r.tag: r for r in results}

tab_overview, tab_point, tab_export = st.tabs(["Overview", "Point detail", "Export"], key="main_tab", on_change="rerun")

# --- Overview -------------------------------------------------------------

with tab_overview:
    cur_total = sum(r.current.events_per_day for r in results if r.current)
    new_total = sum((r.recommended or r.current).events_per_day for r in results if r.current)
    to_change = sum(1 for r in results if r.changes)
    review = sum(1 for r in results if not r.ready_to_export and r.classification != Classification.ON_TARGET)
    change = 100 * (new_total - cur_total) / cur_total if cur_total else 0
    st.markdown(style.kpis([
        ("Points", f"{len(results)}", f"target retention {run_opts.target:.1%}"),
        ("Proposed changes", f"{to_change}", f"{len(results) - to_change} unchanged"),
        ("Need a closer look", f"{review}", "not exported by default" if review else "none"),
        ("Archive events / day", _fmt_n(new_total),
         f'<b class="{"down" if change <= 0 else "up"}">{change:+.1f}%</b> from {_fmt_n(cur_total)} today'),
    ]), unsafe_allow_html=True)
    counts = Counter(r.classification for r in results)
    st.markdown(style.chips({c: counts[c] for c in Classification if counts[c]}), unsafe_allow_html=True)

    st.markdown(style.section("Where each point sits"), unsafe_allow_html=True)
    ys = [100 * e.score.retention for r in results for e in (r.current, r.recommended) if e is not None]
    low_y = min(ys + [100 * run_opts.target])
    fig = go.Figure()
    fig.add_hrect(y0=run_opts.target * 100, y1=101, fillcolor=INK["proposal"], opacity=0.05, line_width=0)
    fig.add_hline(y=run_opts.target * 100, line=dict(color=INK["proposal"], dash="dot", width=1),
                  annotation_text=f"target {run_opts.target:.1%}", annotation_position="bottom right",
                  annotation_font=dict(size=11, color=INK["proposal"]))
    for r in results:
        if r.current and r.recommended and r.changes:
            fig.add_annotation(x=np.log10(max(r.recommended.events_per_day, 1e-3)), y=100 * r.recommended.score.retention,
                               ax=np.log10(max(r.current.events_per_day, 1e-3)), ay=100 * r.current.score.retention,
                               xref="x", yref="y", axref="x", ayref="y", showarrow=True, arrowhead=3, arrowsize=1.1,
                               arrowwidth=1.2, arrowcolor="rgba(19,112,138,0.45)")
    for c in Classification:
        pts = [r for r in results if r.classification == c and r.current]
        if not pts:
            continue
        fig.add_scatter(x=[r.current.events_per_day for r in pts], y=[100 * r.current.score.retention for r in pts],
                        mode="markers", name=c.value, customdata=[r.tag for r in pts],
                        marker=dict(size=12, color=VERDICT_COLOR[c], line=dict(width=1.5, color="white")),
                        hovertemplate="<b>%{customdata}</b><br>%{x:,.0f} events/day<br>%{y:.1f}% retained<extra></extra>")
    prop = [r for r in results if r.current and r.recommended and r.changes]
    if prop:
        fig.add_scatter(x=[r.recommended.events_per_day for r in prop], y=[100 * r.recommended.score.retention for r in prop],
                        mode="markers", name="With proposal", customdata=[r.tag for r in prop],
                        marker=dict(size=9, color="white", line=dict(width=2, color=INK["proposal"])),
                        hovertemplate="<b>%{customdata}</b> with proposal<br>%{x:,.0f} events/day<br>%{y:.1f}% retained<extra></extra>")
    style.plot_layout(fig, 360, xaxis=dict(type="log", title="Archive events per day (log scale)"),
                      yaxis=dict(title="Retention (%)", range=[max(-2, low_y - 4), 101]))
    st.plotly_chart(fig, use_container_width=True, config=PLOT_CONFIG)
    st.markdown('<p class="pct-note">Filled dots are each point today; open dots are where the proposal puts it. '
                "The best settings sit just inside the shaded band and as far left as possible.</p>",
                unsafe_allow_html=True)

    st.markdown(style.section("Verdicts"), unsafe_allow_html=True)
    shown = st.multiselect("Show verdicts", [c.value for c in Classification], label_visibility="collapsed",
                           default=list(dict.fromkeys(r.classification.value for r in results)), key="class_filter",
                           placeholder="Filter by verdict")
    df = results_frame(results)
    df.insert(0, "Export", [ss.export_choice.get(r.tag, r.changes and (r.ready_to_export or r.tag in ss.adopted))
                            for r in results])
    for c in ("CurrentEventsPerDay", "RecommendedEventsPerDay"):
        df[c] = df[c].round()
    view = df[df["Classification"].isin(shown)]
    cols = ["Export", "Tag", "Classification", "CurrentCompDev", "RecommendedCompDev", "RecommendedExcDev",
            "CurrentRetention", "RecommendedRetention", "CurrentEventsPerDay", "RecommendedEventsPerDay",
            "EventChangePercent"]
    if run_opts.input_mode != "raw":
        cols.remove("RecommendedExcDev")
    edited = st.data_editor(
        view[cols], hide_index=True, use_container_width=True, key=f"table_{ss.table_v}",
        disabled=[c for c in cols if c != "Export"],
        column_config={
            "Export": st.column_config.CheckboxColumn("Export", width="small",
                                                      help="Selected (x) in the PI Builder sheet."),
            "Tag": st.column_config.TextColumn("Tag", width="medium"),
            "Classification": st.column_config.TextColumn("Verdict", width="small"),
            "CurrentCompDev": st.column_config.NumberColumn("CompDev", format="%.4g", width="small"),
            "RecommendedCompDev": st.column_config.NumberColumn("→ proposed", format="%.4g", width="small"),
            "RecommendedExcDev": st.column_config.NumberColumn("ExcDev", format="%.4g", width="small"),
            "CurrentRetention": st.column_config.NumberColumn("Retained", format="percent", width="small"),
            "RecommendedRetention": st.column_config.NumberColumn("→ proposed", format="percent", width="small"),
            "CurrentEventsPerDay": st.column_config.NumberColumn("Events/day", format="localized", width="small"),
            "RecommendedEventsPerDay": st.column_config.NumberColumn("→ proposed", format="localized", width="small"),
            "EventChangePercent": st.column_config.NumberColumn("Volume change", format="%+.1f%%", width="small"),
        },
    )
    ss.export_choice.update(dict(zip(edited["Tag"], edited["Export"])))

# --- Point detail ---------------------------------------------------------

with tab_point:
    tags = list(by_tag)

    def _step(d):
        ss.detail_tag = tags[(tags.index(ss.get("detail_tag", tags[0])) + d) % len(tags)]

    n1, n2, n3 = st.columns([14, 1, 1], vertical_alignment="center")
    tag = n1.selectbox("Point", tags, key="detail_tag", label_visibility="collapsed",
                       format_func=lambda t: f"{t}   ·   {by_tag[t].classification.value}")
    n2.button(":material/chevron_left:", key="prev_pt", on_click=_step, args=(-1,), help="Previous point",
              use_container_width=True)
    n3.button(":material/chevron_right:", key="next_pt", on_click=_step, args=(1,), help="Next point",
              use_container_width=True)

    r = by_tag[tag]
    base = ss.results[[x.tag for x in ss.results].index(tag)]  # the tuner's own proposal
    s: Series = ss.series[tag]
    cfg_now = ss.configs.get(tag, PointConfig())
    days = max(s.duration, 1) / 86400
    prop = r.recommended if (r.recommended is not None and r.changes) else None
    adopted = tag in ss.adopted

    h1, h2 = st.columns([5, 2], vertical_alignment="center")
    h1.markdown(f'<div class="pct-point"><h3>{style.esc(tag)}</h3>{style.chip(r.classification)}'
                f'<span class="meta">{len(s):,} input events over {days:.1f} days</span></div>', unsafe_allow_html=True)
    if r.changes:
        default_on = ss.export_choice.get(tag, r.ready_to_export or adopted)

        def _toggle(t=tag):
            ss.export_choice[t] = ss[f"exp_{t}"]
            ss.table_v += 1

        h2.toggle("Include in PI Builder sheet", value=default_on, key=f"exp_{tag}", on_change=_toggle)
    st.markdown(style.finding(present.finding(r, run_opts.target, adopted), VERDICT_COLOR[r.classification]),
                unsafe_allow_html=True)

    if r.current is not None:
        new = prop or r.current
        cards = present.outcome_cards(r, run_opts.target, cfg_now.span, {"now": INK["now"], "proposal": INK["proposal"]})
        st.markdown(style.outcome(cards), unsafe_allow_html=True)

    trend_box = st.container()

    a1, a2 = st.columns([3, 2], gap="large")
    with a1:
        st.markdown(style.section("Settings"), unsafe_allow_html=True)
        st.markdown(style.attrs_table(present.attr_rows(cfg_now, prop.config if prop else None),
                                      proposed=prop is not None), unsafe_allow_html=True)
    with a2:
        st.markdown(style.section("Signal"), unsafe_allow_html=True)
        items = present.signal_profile(r, s, cfg_now)
        st.markdown(style.profile(items), unsafe_allow_html=True)
        st.markdown(style.notes([n for n in r.notes if n not in present.finding(r, run_opts.target, adopted)]),
                    unsafe_allow_html=True)

    if r.current is not None:
        st.markdown(style.section("Explore other settings"), unsafe_allow_html=True)
        if tag not in ss.curves:
            ss.curves[tag] = tradeoff_curve(s, r.current.config, run_opts, noise=r.noise)
        curve = ss.curves[tag]
        start_cd = float((r.recommended or r.current).config.compdev)
        left, right = st.columns([12, 7], gap="large")
        with right:
            with st.container(border=True):
                st.markdown("**Probe** · try any setting")
                c1, c2 = st.columns(2)
                probe_cd = c1.number_input("CompDev", min_value=0.0, value=start_cd, format="%.4g", key=f"cd_{tag}")
                probe_exc = c2.number_input("ExcDev", min_value=0.0, format="%.4g", key=f"ed_{tag}",
                                            value=float((r.recommended or r.current).config.excdev),
                                            disabled=run_opts.input_mode != "raw",
                                            help="Only tunable when the data is raw interface values.")
                probe_cfg = dataclasses.replace(
                    r.current.config, compdev=probe_cd, compressing=True,
                    excdev=probe_exc if run_opts.input_mode == "raw" else r.current.config.excdev)
                probe = evaluate_config(s, probe_cfg, run_opts, noise=r.noise)

                cols_cmp = [("Now", r.current)]
                if base.recommended is not None and base.changes:
                    cols_cmp.append(("Proposal", base.recommended))
                cols_cmp.append(("Probe", probe))

                def cell(ev, kind):
                    if kind == "ret":
                        cls = "ok" if ev.score.retention >= run_opts.target else "bad"
                        return f'<div class="{cls}">{ev.score.retention:.1%}</div>'
                    if kind == "cd":
                        return f"<div>{ev.config.compdev:.4g}</div>"
                    if kind == "ev":
                        return f"<div>{_fmt_n(ev.events_per_day)}</div>"
                    return f"<div>{ev.score.max_error:.3g}</div>"

                grid = '<div class="h k"></div>' + "".join(f'<div class="h">{n}</div>' for n, _ in cols_cmp)
                grid += "".join(f'<div class="k">{label}</div>' + "".join(cell(ev, k) for _, ev in cols_cmp)
                                for label, k in (("CompDev", "cd"), ("Retained", "ret"),
                                                 ("Events/day", "ev"), ("Max error", "err")))
                st.markdown(f'<div class="pct-compare n{len(cols_cmp)}">{grid}</div>', unsafe_allow_html=True)
                st.write("")
                b1, b2 = st.columns(2)
                if b1.button("Adopt probe", key=f"adopt_{tag}", type="primary", use_container_width=True,
                             help="Export the probe value instead of the proposal."):
                    ss.adopted[tag] = probe
                    ss.export_choice[tag] = True
                    ss.table_v += 1
                    st.rerun()
                if adopted and b2.button("Undo", key=f"undo_{tag}", use_container_width=True):
                    del ss.adopted[tag]
                    ss.export_choice.pop(tag, None)
                    ss.table_v += 1
                    st.rerun()

        with left:
            tc = go.Figure()
            tc.add_hrect(y0=run_opts.target * 100, y1=101, fillcolor=INK["proposal"], opacity=0.05, line_width=0)
            tc.add_hline(y=run_opts.target * 100, line=dict(color=INK["proposal"], dash="dot", width=1))
            tc.add_scatter(x=[e.events_per_day for e in curve], y=[100 * e.score.retention for e in curve],
                           mode="lines+markers", name="CompDev sweep", line=dict(color=INK["input"], width=2, shape="spline"),
                           marker=dict(size=5, color=INK["input"]), customdata=[e.config.compdev for e in curve],
                           hovertemplate="CompDev %{customdata:.4g}<br>%{x:,.0f} events/day<br>%{y:.1f}%<extra></extra>")
            for name, ev, color, sym, size in [("Now", r.current, INK["now"], "circle", 13),
                                               *([("Proposal", base.recommended, INK["proposal"], "diamond", 14)]
                                                 if base.recommended is not None and base.changes else []),
                                               ("Probe", probe, INK["probe"], "star", 16)]:
                tc.add_scatter(x=[ev.events_per_day], y=[100 * ev.score.retention], mode="markers", name=name,
                               marker=dict(size=size, color=color, symbol=sym, line=dict(width=1.5, color="white")),
                               hovertemplate=f"<b>{name}</b> · CompDev {ev.config.compdev:.4g}<br>"
                                             "%{x:,.0f} events/day<br>%{y:.1f}%<extra></extra>")
            style.plot_layout(tc, 330, xaxis=dict(type="log", title="Archive events per day (log scale)"),
                              yaxis=dict(title="Retention (%)", range=[-3, 103]))
            st.plotly_chart(tc, use_container_width=True, config=PLOT_CONFIG)

        def archive_of(cfg):
            sim = simulate_archive(s, cfg, apply_exception=run_opts.input_mode == "raw")
            vis = sim.visible()
            return s.t[vis], s.v[vis]

        def ts(t):
            return pd.to_datetime(t, unit="s", utc=True)

        show_probe = not np.isclose(probe_cfg.compdev, new.config.compdev) or not np.isclose(probe_cfg.excdev, new.config.excdev)
        lines = [("Archive now", r.current.config, INK["now"])]
        if prop is not None:
            lines.append(("Archive proposed", prop.config, INK["proposal"]))
        if show_probe:
            lines.append(("Archive at probe", probe_cfg, INK["probe"]))
        archives = {name: archive_of(cfg) for name, cfg, _ in lines}
        judged_name, judged_cfg, _ = lines[-1]
        jt, jv = archives[judged_name]
        err = np.abs(s.v - reconstruct(jt, jv, s.t, judged_cfg.step))
        excess = np.maximum(0, err - run_opts.noise_band * r.noise)

        with trend_box:
            t1, t2 = st.columns([3, 2], vertical_alignment="bottom")
            t1.markdown(style.section("Trend"), unsafe_allow_html=True)
            view = t2.segmented_control("View", ["Whole period", "Largest miss"], default="Whole period",
                                        key=f"view_{tag}", label_visibility="collapsed") or "Whole period"
            if view == "Largest miss":
                k = int(np.argmax(err))
                half = run_opts.window_s / 2
                w = (s.t >= s.t[k] - half) & (s.t <= s.t[k] + half)
                tt, vv = s.t[w], s.v[w]
                xt, xv = s.t[w], excess[w]
            else:
                tt, vv = envelope(s.t, s.v, 3000)
                xt, xv = envelope(s.t, excess, 1500)
                w = None
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.8, 0.2], vertical_spacing=0.05)
            fig.add_scatter(x=ts(tt), y=vv, name="Input", line=dict(color=INK["input"], width=1), row=1, col=1)
            for name, cfg, color in lines:
                at, av = archives[name]
                if w is not None:
                    # one event either side so the segments into the window are drawn
                    i0 = max(int(np.searchsorted(at, tt[0])) - 1, 0)
                    i1 = int(np.searchsorted(at, tt[-1], side="right")) + 1
                    at, av = at[i0:i1], av[i0:i1]
                    size = 4 if name == "Archive now" else 8
                    mode, mk = "lines+markers", dict(size=size, color=color, line=dict(width=1, color="white"))
                else:
                    mode, mk = "lines", None
                    at, av = envelope(at, av, 3000)
                fig.add_scatter(x=ts(at), y=av, name=name, mode=mode, marker=mk, row=1, col=1,
                                line=dict(color=color, width=1.8 if name != "Archive now" else 1.3,
                                          shape="hv" if cfg.step else "linear"))
            fig.add_scatter(x=ts(xt), y=xv, name="Detail lost", fill="tozeroy", fillcolor="rgba(208,87,74,0.25)",
                            line=dict(color=INK["loss"], width=1), row=2, col=1)
            style.plot_layout(fig, 460, hovermode="x unified")
            fig.update_yaxes(title_text="Value", row=1, col=1)
            fig.update_yaxes(title_text="Lost", row=2, col=1, rangemode="tozero")
            if w is not None:
                pad = max(np.ptp(vv), r.noise, 1e-9) * 0.15
                fig.update_xaxes(range=[ts(tt[0]), ts(tt[-1])])
                fig.update_yaxes(range=[vv.min() - pad, vv.max() + pad], row=1, col=1)
            st.plotly_chart(fig, use_container_width=True, config=PLOT_CONFIG)
            what = {"Archive now": "today's archive", "Archive proposed": "the proposed archive",
                    "Archive at probe": "the probe's archive"}[judged_name]
            st.markdown(f'<p class="pct-note">The lower strip shows where {what} strays from the input by more than the '
                        "noise band; flat means no real detail is lost. <b>Largest miss</b> zooms to one scoring window "
                        "around the biggest error, with each archived event marked.</p>", unsafe_allow_html=True)

# --- Export ---------------------------------------------------------------

with tab_export:
    selected = {t for t, on in ss.export_choice.items() if on} | {
        r.tag for r in results if r.tag not in ss.export_choice and r.changes and (r.ready_to_export or r.tag in ss.adopted)}
    left, right = st.columns([5, 12], gap="large")
    with left:
        server_name = st.text_input("Data Archive (PI Builder Parent column)", ss.get("pi_server", ""), key="parent")
        pib = pibuilder_frame(results, server=server_name, include_excdev=run_opts.input_mode == "raw", selected=selected)
        n_sel = int((pib["Selected(x)"] == "x").sum())
        st.markdown(style.kpis([("Changed points", f"{len(pib)}", "rows in the sheet"),
                                ("Selected", f"{n_sel}", "marked x for publishing")]), unsafe_allow_html=True)
        st.download_button("Download PI Builder sheet", pib.to_csv(index=False, float_format="%.6g"), "pibuilder.csv",
                           "text/csv", type="primary", use_container_width=True)
        st.download_button("Download results table", results_frame(results).to_csv(index=False, float_format="%.6g"),
                           "results.csv", "text/csv", use_container_width=True)
        with tempfile.TemporaryDirectory() as d:
            write_report(results, ss.series, run_opts, Path(d) / "report.html", selected=selected)
            st.download_button("Download HTML report", (Path(d) / "report.html").read_bytes(), "report.html",
                               "text/html", use_container_width=True)
    with right:
        st.markdown(style.section("PI Builder sheet preview"), unsafe_allow_html=True)
        st.dataframe(pib.astype(object).where(pib.notna(), ""), hide_index=True, use_container_width=True)
        st.markdown(f'<p class="pct-note">The PI Builder sheet has <b>{len(pib)}</b> changed points, <b>{n_sel}</b> of them '
                    "selected. Unselected rows stay in the sheet so you can review them one at a time. "
                    "Open it in Excel with PI Builder and publish when you're satisfied.</p>", unsafe_allow_html=True)
