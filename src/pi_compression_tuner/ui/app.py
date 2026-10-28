"""PI Compression Tuner web UI (Streamlit).

Three tabs:
  * Overview: where every point sits on retention versus archive volume, with a table of verdicts.
  * Workbench: one point's trade-off curve, a probe to try any CompDev, and the trend with a
    residual strip showing where detail would be lost.
  * Export: the PI Builder sheet, results table and HTML report.
Recommendations only: nothing here writes to PI.
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from pi_compression_tuner.demo import demo_data
from pi_compression_tuner.export import pibuilder_frame, results_frame
from pi_compression_tuner.metrics import reconstruct
from pi_compression_tuner.models import PointConfig, Series
from pi_compression_tuner.report import envelope, write_report
from pi_compression_tuner.simulate import simulate_archive
from pi_compression_tuner.sources.csvfiles import read_configs, read_series
from pi_compression_tuner.tune import Classification, TuneOptions, evaluate_config, tradeoff_curve, tune_point

INK = {"input": "#8d99a3", "now": "#b5651d", "proposal": "#13708a", "probe": "#6d4bb3", "loss": "#c0392b"}
VERDICT_COLOR = {
    Classification.ON_TARGET: "#2f7a3c", Classification.STORING_NOISE: "#b5651d",
    Classification.LOSING_DETAIL: "#c0392b", Classification.LITTLE_TO_GAIN: "#4b5d9a",
    Classification.RANGE_MISMATCH: "#4b5d9a", Classification.NOT_MOVING: "#8d99a3",
    Classification.TOO_FEW_EVENTS: "#8d99a3",
}
WINDOWS = {"15 min": 900, "30 min": 1800, "1 h": 3600, "4 h": 14400}

st.set_page_config(page_title="PI Compression Tuner", layout="wide")
ss = st.session_state
for k, v in {"series": None, "configs": {}, "results": None, "adopted": {}, "source_note": "",
             "export_choice": {}, "table_v": 0, "curves": {}}.items():
    ss.setdefault(k, v)


def _reset_results():
    ss.results, ss.adopted, ss.curves, ss.export_choice = None, {}, {}, {}
    ss.table_v += 1


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
    via = "the PI Web API" if conn.kind == "webapi" else "AF SDK"
    return series, configs, conn.note or f"Read {len(refs)} points from {server} through {via}."


with st.sidebar:
    st.subheader("Data")
    origin = st.radio("Source", ["Demo data", "CSV files", "PI server"], key="origin", horizontal=True,
                      help="CSV files can come from `pict pi record` (snapshot stream) or any export.")
    if origin == "Demo data":
        days = st.slider("Days of data", 1, 7, 3, key="demo_days")
        if st.button("Load demo data", type="primary", key="load_demo", use_container_width=True):
            ss.series, ss.configs = demo_data(days)
            ss.source_note = "Synthetic demo data: four points with deliberately poor settings."
            _reset_results()
    elif origin == "CSV files":
        data_up = st.file_uploader("Data CSV", type="csv", key="data_up",
                                   help="Long (Tag, Timestamp, Value) or wide (Timestamp, one column per tag).")
        cfg_up = st.file_uploader("Point settings CSV (optional)", type="csv", key="cfg_up",
                                  help="PI Builder column names: Name, ExcDev, CompDev, CompMax, Span, ...")
        if st.button("Load files", type="primary", disabled=data_up is None, key="load_csv", use_container_width=True):
            try:
                ss.series = _read_upload(data_up, read_series)
                ss.configs = _read_upload(cfg_up, read_configs) if cfg_up else {}
                ss.source_note = f"Loaded {len(ss.series)} points from {data_up.name}."
                _reset_results()
            except Exception as e:
                st.error(f"Could not read the files: {e}")
    else:
        server = st.text_input("Data Archive", key="pi_server")
        source = st.selectbox("Connect through", ["auto", "webapi", "afsdk"], key="pi_source",
                              format_func={"auto": "PI Web API, falling back to AF SDK", "webapi": "PI Web API only",
                                           "afsdk": "AF SDK only"}.get)
        url = st.text_input("PI Web API URL", placeholder="https://pihost/piwebapi", key="pi_url",
                            disabled=source == "afsdk")
        with st.expander("Connection options"):
            auth = st.selectbox("Web API sign-in", ["kerberos", "basic", "anonymous"], key="pi_auth",
                                help="basic reads PI_WEBAPI_USER and PI_WEBAPI_PASSWORD from the environment.")
            verify = st.checkbox("Check TLS certificate", value=True, key="pi_verify")
            afsdk_dir = st.text_input("AF SDK folder", key="pi_afsdk_dir")
        tags_text = st.text_area("Tags, one per line", key="pi_tags")
        name_filter = st.text_input("…or a tag filter", placeholder="FIC*", key="pi_filter")
        limit = st.number_input("Most points", 1, 5000, 100, key="pi_limit")
        c1, c2 = st.columns(2)
        start = c1.text_input("Start", "*-7d", key="pi_start")
        end = c2.text_input("End", "*", key="pi_end")
        st.caption("Reads archived values, which suits points whose compression is off or very tight. "
                   "For other points, record the snapshot stream with `pict pi record` and load it as CSV files.")
        if st.button("Read from PI", type="primary", disabled=not server, key="load_pi", use_container_width=True):
            try:
                tags = [t.strip() for t in tags_text.splitlines() if t.strip()]
                ss.series, ss.configs, ss.source_note = _load_pi(server, source, url, auth, verify, afsdk_dir,
                                                                 tags, name_filter, int(limit), start, end)
                _reset_results()
            except Exception as e:
                st.error(f"Could not read from PI: {e}")

    st.subheader("Tuning")
    mode = st.radio("The data is", ["snapshot", "raw"], key="mode",
                    format_func={"snapshot": "the snapshot stream (tune CompDev)",
                                 "raw": "raw interface values (tune ExcDev and CompDev)"}.get)
    target_pct = st.slider("Target retention, %", 80.0, 99.9, 95.0, 0.1, key="target",
                           help="Share of the signal's movement, beyond its noise, that the archive must keep.")
    window = st.select_slider("Scoring window", list(WINDOWS), value="1 h", key="window")
    with st.expander("Error limits and ExcDev"):
        max_err = st.number_input("Largest error, engineering units (0 = none)", 0.0, value=0.0, key="max_err")
        max_err_pct = st.number_input("Largest error, % of span (0 = none)", 0.0, 100.0, 0.0, key="max_err_pct")
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


# --- header ---------------------------------------------------------------

st.title("PI Compression Tuner")
st.caption("Finds the compression each point needs from how its signal behaves. "
           "Read-only: nothing is written to PI. Changes leave as a PI Builder sheet for you to review and publish.")
if ss.source_note:
    st.caption(f"**Data:** {ss.source_note}")

if ss.series is None:
    st.markdown("#### Start by loading data in the sidebar")
    st.markdown("**Demo data** shows the whole workflow without a PI server.")
    st.stop()
if ss.results is None:
    n = sum(len(s) for s in ss.series.values())
    st.markdown(f"#### {len(ss.series)} points loaded ({n:,} events)")
    st.markdown("Pick a target retention in the sidebar, then **Run tuning**.")
    st.stop()

run_opts: TuneOptions = ss.get("opts", opts)


def effective(r):
    """The result with an adopted probe setting in place of the proposal."""
    a = ss.adopted.get(r.tag)
    return dataclasses.replace(r, recommended=a, notes=r.notes + ["CompDev adopted in the workbench."]) if a else r


results = [effective(r) for r in ss.results]
by_tag = {r.tag: r for r in results}

tab_overview, tab_bench, tab_export = st.tabs(["Overview", "Workbench", "Export"])

# --- Overview -------------------------------------------------------------

with tab_overview:
    cur_total = sum(r.current.events_per_day for r in results if r.current)
    new_total = sum((r.recommended or r.current).events_per_day for r in results if r.current)
    to_change = sum(1 for r in results if r.changes)
    review = sum(1 for r in results if not r.ready_to_export and r.classification != Classification.ON_TARGET)
    m = st.columns(4)
    m[0].metric("Points", len(results))
    m[1].metric("With a proposed change", to_change)
    m[2].metric("Need a closer look", review)
    if cur_total:
        m[3].metric("Archive events per day", f"{new_total:,.0f}",
                    f"{100 * (new_total - cur_total) / cur_total:+.1f}% vs {cur_total:,.0f} now", delta_color="off")

    # Map: each point's archive now, with an arrow to where the proposal puts it.
    ys = [100 * e.score.retention for r in results for e in (r.current, r.recommended) if e is not None]
    low_y = min(ys + [100 * run_opts.target])
    fig = go.Figure()
    fig.add_hline(y=run_opts.target * 100, line=dict(color=INK["input"], dash="dash", width=1),
                  annotation_text=f"target {run_opts.target:.1%}", annotation_position="bottom right")
    for c in Classification:
        pts = [r for r in results if r.classification == c and r.current]
        if not pts:
            continue
        fig.add_scatter(x=[r.current.events_per_day for r in pts], y=[100 * r.current.score.retention for r in pts],
                        mode="markers", name=c.value, text=[r.tag for r in pts],
                        marker=dict(size=11, color=VERDICT_COLOR[c], line=dict(width=1, color="white")),
                        hovertemplate="%{text}<br>%{x:,.0f} events/day<br>%{y:.1f}% retained<extra></extra>")
    for r in results:
        if r.current and r.recommended and r.changes:
            fig.add_annotation(x=np.log10(max(r.recommended.events_per_day, 1e-3)), y=100 * r.recommended.score.retention,
                               ax=np.log10(max(r.current.events_per_day, 1e-3)), ay=100 * r.current.score.retention,
                               xref="x", yref="y", axref="x", ayref="y", showarrow=True, arrowhead=2, arrowwidth=1,
                               arrowcolor=INK["proposal"], opacity=0.7)
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.2),
                      xaxis=dict(type="log", title="Archive events per day (log)"),
                      yaxis=dict(title="Retention, %", range=[max(-2, low_y - 5), 101]))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Each dot is a point's archive today. Arrows show where the proposed setting moves it. "
               "Good settings sit just above the target line and as far left as possible.")

    shown = st.multiselect("Verdicts", [c.value for c in Classification],
                           default=list(dict.fromkeys(r.classification.value for r in results)), key="class_filter")
    df = results_frame(results)
    df.insert(0, "Export", [ss.export_choice.get(r.tag, r.changes and (r.ready_to_export or r.tag in ss.adopted))
                            for r in results])
    view = df[df["Classification"].isin(shown)]
    cols = ["Export", "Tag", "Classification", "CurrentCompDev", "RecommendedCompDev", "RecommendedExcDev",
            "CurrentRetention", "RecommendedRetention", "CurrentEventsPerDay", "RecommendedEventsPerDay",
            "EventChangePercent", "Notes"]
    if run_opts.input_mode != "raw":
        cols.remove("RecommendedExcDev")
    edited = st.data_editor(
        view[cols], hide_index=True, use_container_width=True, key=f"table_{ss.table_v}",
        disabled=[c for c in cols if c != "Export"],
        column_config={
            "Export": st.column_config.CheckboxColumn(help="Selected (x) in the PI Builder sheet."),
            "Classification": st.column_config.TextColumn("Verdict"),
            "CurrentCompDev": st.column_config.NumberColumn("CompDev now", format="%.4g"),
            "RecommendedCompDev": st.column_config.NumberColumn("Proposed CompDev", format="%.4g"),
            "RecommendedExcDev": st.column_config.NumberColumn("Proposed ExcDev", format="%.4g"),
            "CurrentRetention": st.column_config.NumberColumn("Retained now", format="percent"),
            "RecommendedRetention": st.column_config.NumberColumn("Retained", format="percent"),
            "CurrentEventsPerDay": st.column_config.NumberColumn("Events/day now", format="%.0f"),
            "RecommendedEventsPerDay": st.column_config.NumberColumn("Events/day", format="%.0f"),
            "EventChangePercent": st.column_config.NumberColumn("Volume change", format="%+.0f%%"),
        },
    )
    ss.export_choice.update(dict(zip(edited["Tag"], edited["Export"])))

# --- Workbench ------------------------------------------------------------

with tab_bench:
    tag = st.selectbox("Point", list(by_tag), key="detail_tag",
                       format_func=lambda t: f"{t}  ·  {by_tag[t].classification.value}")
    r = by_tag[tag]
    base = ss.results[[x.tag for x in ss.results].index(tag)]  # the tuner's own proposal
    s: Series = ss.series[tag]
    if r.notes:
        st.caption(" ".join(r.notes))

    if r.current is None:
        st.info(f"{r.classification.value}: there is nothing to tune for this point.")
    else:
        if tag not in ss.curves:
            ss.curves[tag] = tradeoff_curve(s, r.current.config, run_opts, noise=r.noise)
        curve = ss.curves[tag]
        start_cd = float((r.recommended or r.current).config.compdev)
        left, right = st.columns([5, 2])
        with right:
            st.markdown("**Probe**")
            probe_cd = st.number_input("CompDev", min_value=0.0, value=start_cd, format="%.5g", key=f"cd_{tag}",
                                       help="Try any value. The curve and the trend update as you change it.")
            probe_exc = st.number_input("ExcDev", min_value=0.0,
                                        value=float((r.recommended or r.current).config.excdev),
                                        format="%.5g", key=f"ed_{tag}", disabled=run_opts.input_mode != "raw",
                                        help="Only tunable when the data is raw interface values.")
            probe_cfg = dataclasses.replace(r.current.config, compdev=probe_cd, compressing=True,
                                            excdev=probe_exc if run_opts.input_mode == "raw" else r.current.config.excdev)
            probe = evaluate_config(s, probe_cfg, run_opts, noise=r.noise)
            ok = probe.score.retention >= run_opts.target
            st.markdown(f"Retained **{probe.score.retention:.1%}** "
                        f"({'meets' if ok else 'below'} the {run_opts.target:.1%} target)")
            st.markdown(f"**{probe.events_per_day:,.0f}** archive events per day")
            st.markdown(f"Largest error **{probe.score.max_error:.4g}** (noise σ {r.noise:.3g})")
            b1, b2 = st.columns(2)
            if b1.button("Adopt", key=f"adopt_{tag}", use_container_width=True, help="Use the probe value in the export."):
                ss.adopted[tag] = probe
                ss.export_choice[tag] = True
                ss.table_v += 1
                st.rerun()
            if tag in ss.adopted and b2.button("Undo", key=f"undo_{tag}", use_container_width=True):
                del ss.adopted[tag]
                ss.export_choice.pop(tag, None)
                ss.table_v += 1
                st.rerun()

        with left:
            # Trade-off curve: what each CompDev costs in volume and buys in retention.
            tc = go.Figure()
            tc.add_scatter(x=[e.events_per_day for e in curve], y=[100 * e.score.retention for e in curve],
                           mode="lines+markers", name="CompDev sweep", line=dict(color=INK["input"]),
                           marker=dict(size=5), text=[f"CompDev {e.config.compdev:.4g}" for e in curve],
                           hovertemplate="%{text}<br>%{x:,.0f} events/day<br>%{y:.1f}%<extra></extra>")
            marks = [("Now", r.current, INK["now"], "circle")]
            if base.recommended is not None and base.changes:
                marks.append(("Proposal", base.recommended, INK["proposal"], "diamond"))
            marks.append(("Probe", probe, INK["probe"], "star"))
            for name, ev, color, sym in marks:
                tc.add_scatter(x=[ev.events_per_day], y=[100 * ev.score.retention], mode="markers", name=name,
                               marker=dict(size=14, color=color, symbol=sym, line=dict(width=1, color="white")),
                               hovertemplate=f"{name}: CompDev {ev.config.compdev:.4g}<br>%{{x:,.0f}} events/day"
                                             "<br>%{y:.1f}%<extra></extra>")
            tc.add_hline(y=run_opts.target * 100, line=dict(color=INK["input"], dash="dash", width=1))
            tc.update_layout(height=300, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=-0.3),
                             xaxis=dict(type="log", title="Archive events per day (log)"),
                             yaxis=dict(title="Retention, %", range=[-2, 102]))
            st.plotly_chart(tc, use_container_width=True)

        # Trend with a residual strip: where the probe's archive departs from the input beyond the noise band.
        def archive_of(cfg):
            sim = simulate_archive(s, cfg, apply_exception=run_opts.input_mode == "raw")
            vis = sim.visible()
            return s.t[vis], s.v[vis]

        def ts(t):
            return pd.to_datetime(t, unit="s", utc=True)

        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.75, 0.25], vertical_spacing=0.04)
        et, ev = envelope(s.t, s.v, 3000)
        fig.add_scatter(x=ts(et), y=ev, name="Input", line=dict(color=INK["input"], width=1), row=1, col=1)
        nt, nv = envelope(*archive_of(r.current.config), 3000)
        fig.add_scatter(x=ts(nt), y=nv, name="Archive now", line=dict(color=INK["now"], width=1.2), row=1, col=1)
        pt, pv = archive_of(probe_cfg)
        fig.add_scatter(x=ts(pt), y=pv, name=f"Archive at probe (CompDev {probe_cd:.4g})", mode="lines+markers",
                        marker=dict(size=4), line=dict(color=INK["probe"], width=1.8), row=1, col=1)
        excess = np.maximum(0, np.abs(s.v - reconstruct(pt, pv, s.t, probe_cfg.step)) - run_opts.noise_band * r.noise)
        xt, xv = envelope(s.t, excess, 3000)
        fig.add_scatter(x=ts(xt), y=xv, name="Error beyond noise", fill="tozeroy",
                        line=dict(color=INK["loss"], width=1), row=2, col=1)
        fig.update_yaxes(title_text="Value", row=1, col=1)
        fig.update_yaxes(title_text="Lost", row=2, col=1, rangemode="tozero")
        fig.update_layout(height=520, margin=dict(l=10, r=10, t=10, b=10), legend=dict(orientation="h", y=1.06),
                          hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("The red strip shows how far the probe's archive strays from the input beyond the noise band. "
                   "A flat strip means no real detail is lost. Drag on the chart to zoom.")

# --- Export ---------------------------------------------------------------

with tab_export:
    server_name = st.text_input("Data Archive name for the PI Builder Parent column", ss.get("pi_server", ""), key="parent")
    selected = {t for t, on in ss.export_choice.items() if on} | {
        r.tag for r in results if r.tag not in ss.export_choice and r.changes and (r.ready_to_export or r.tag in ss.adopted)}
    pib = pibuilder_frame(results, server=server_name, include_excdev=run_opts.input_mode == "raw", selected=selected)
    st.markdown(f"The PI Builder sheet has **{len(pib)}** changed points, **{(pib['Selected(x)'] == 'x').sum()}** "
                "of them selected. Unselected rows stay in the sheet for one-at-a-time review.")
    st.dataframe(pib, hide_index=True, use_container_width=True)
    d1, d2, d3 = st.columns(3)
    d1.download_button("PI Builder sheet (CSV)", pib.to_csv(index=False, float_format="%.6g"), "pibuilder.csv",
                       "text/csv", type="primary", use_container_width=True)
    d2.download_button("Results table (CSV)", results_frame(results).to_csv(index=False, float_format="%.6g"),
                       "results.csv", "text/csv", use_container_width=True)
    with tempfile.TemporaryDirectory() as d:
        write_report(results, ss.series, run_opts, Path(d) / "report.html")
        d3.download_button("Report (HTML)", (Path(d) / "report.html").read_bytes(), "report.html", "text/html",
                           use_container_width=True)
