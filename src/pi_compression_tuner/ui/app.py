"""PI Compression Tuner web UI (Streamlit).

Load data, run the tuning, review each point's trend, try custom settings,
choose which rows to export, and download a PI Builder sheet. Recommendations
only: nothing here writes to PI.
"""

from __future__ import annotations

import dataclasses
import io
import tempfile
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from pi_compression_tuner.demo import demo_data
from pi_compression_tuner.export import pibuilder_frame, results_frame
from pi_compression_tuner.metrics import reconstruct
from pi_compression_tuner.models import PointConfig, Series
from pi_compression_tuner.report import envelope, write_report
from pi_compression_tuner.simulate import simulate_archive
from pi_compression_tuner.sources.csvfiles import read_configs, read_series
from pi_compression_tuner.tune import Classification, TuneOptions, evaluate_config, tune_point

COLORS = {"raw": "#9aa7b0", "cur": "#c4621f", "rec": "#0f7f99", "custom": "#7a4fbf"}
CLASS_ICON = {
    Classification.GOOD: "🟢", Classification.UNDERCOMPRESSED: "🟠", Classification.OVERCOMPRESSED: "🔴",
    Classification.HARD_TO_COMPRESS: "🔵", Classification.ZERO_SPAN_ISSUE: "🔵", Classification.FLATLINE: "⚪",
    Classification.INSUFFICIENT_DATA: "⚪",
}
WINDOWS = {"15 min": 900, "30 min": 1800, "1 h": 3600, "4 h": 14400}

st.set_page_config(page_title="PI Compression Tuner", layout="wide")
ss = st.session_state
ss.setdefault("series", None)
ss.setdefault("configs", {})
ss.setdefault("results", None)
ss.setdefault("custom", {})
ss.setdefault("source_note", "")
ss.setdefault("export_choice", {})
ss.setdefault("table_v", 0)


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
    st.header("1. Data")
    origin = st.radio("Source", ["Demo data", "CSV files", "PI server"], key="origin",
                      help="CSV files can come from `pict pi record` (snapshot stream) or any export.")
    if origin == "Demo data":
        days = st.slider("Days of data", 1, 7, 3, key="demo_days")
        if st.button("Load demo data", type="primary", key="load_demo"):
            ss.series, ss.configs = demo_data(days)
            ss.results, ss.custom, ss.source_note = None, {}, "Loaded synthetic demo data."
    elif origin == "CSV files":
        data_up = st.file_uploader("Data CSV", type="csv", key="data_up",
                                   help="Long (Tag, Timestamp, Value) or wide (Timestamp, one column per tag).")
        cfg_up = st.file_uploader("Settings CSV (optional)", type="csv", key="cfg_up",
                                  help="PI Builder column names: Name, ExcDev, CompDev, CompMax, Span, ...")
        if st.button("Load files", type="primary", disabled=data_up is None, key="load_csv"):
            try:
                ss.series = _read_upload(data_up, read_series)
                ss.configs = _read_upload(cfg_up, read_configs) if cfg_up else {}
                ss.results, ss.custom = None, {}
                ss.source_note = f"Loaded {len(ss.series)} points from {data_up.name}."
            except Exception as e:
                st.error(f"Could not read the files: {e}")
    else:
        server = st.text_input("Data Archive", key="pi_server")
        source = st.selectbox("Connect through", ["auto", "webapi", "afsdk"], key="pi_source",
                              format_func={"auto": "PI Web API, falling back to AF SDK", "webapi": "PI Web API only",
                                           "afsdk": "AF SDK only"}.get)
        url = st.text_input("PI Web API URL", placeholder="https://pihost/piwebapi", key="pi_url",
                            disabled=source == "afsdk")
        auth = st.selectbox("Web API sign-in", ["kerberos", "basic", "anonymous"], key="pi_auth",
                            help="basic reads PI_WEBAPI_USER and PI_WEBAPI_PASSWORD from the environment.")
        verify = st.checkbox("Check TLS certificate", value=True, key="pi_verify")
        afsdk_dir = st.text_input("AF SDK folder (optional)", key="pi_afsdk_dir")
        tags_text = st.text_area("Tags, one per line", key="pi_tags")
        name_filter = st.text_input("…or a tag filter", placeholder="FIC*", key="pi_filter")
        limit = st.number_input("Most points", 1, 5000, 100, key="pi_limit")
        c1, c2 = st.columns(2)
        start = c1.text_input("Start", "*-7d", key="pi_start")
        end = c2.text_input("End", "*", key="pi_end")
        st.caption("This reads archived values, which suits points whose compression is off or very tight. "
                   "For other points, record the snapshot stream with `pict pi record` and load it as CSV files.")
        if st.button("Read from PI", type="primary", disabled=not server, key="load_pi"):
            try:
                tags = [t.strip() for t in tags_text.splitlines() if t.strip()]
                ss.series, ss.configs, ss.source_note = _load_pi(server, source, url, auth, verify, afsdk_dir,
                                                                 tags, name_filter, int(limit), start, end)
                ss.results, ss.custom = None, {}
            except Exception as e:
                st.error(f"Could not read from PI: {e}")

    st.header("2. Tuning")
    mode = st.radio("The data is", ["snapshot", "raw"], key="mode",
                    format_func={"snapshot": "the snapshot stream (tune CompDev)",
                                 "raw": "raw interface values (tune ExcDev and CompDev)"}.get)
    target = st.slider("Target fidelity", 0.80, 0.999, 0.97, 0.001, format="%.3f", key="target")
    window = st.selectbox("Scoring window", list(WINDOWS), index=2, key="window")
    max_err = st.number_input("Largest allowed error, engineering units (0 = no limit)", 0.0, value=0.0, key="max_err")
    max_err_pct = st.number_input("Largest allowed error, % of span (0 = no limit)", 0.0, 100.0, 0.0, key="max_err_pct")
    excdev_ratio = st.number_input("ExcDev as a fraction of CompDev", 0.0, 1.0, 0.5, 0.05, key="excdev_ratio",
                                   disabled=mode != "raw")
    run = st.button("Run tuning", type="primary", disabled=ss.series is None, key="run")

opts = TuneOptions(target=target, input_mode=mode, window_s=WINDOWS[window], max_error=max_err or None,
                   max_error_pct_span=max_err_pct or None, excdev_ratio=excdev_ratio)

if run and ss.series is not None:
    results = []
    bar = st.progress(0.0, text="Tuning")
    items = list(ss.series.items())
    for i, (tag, s) in enumerate(items):
        results.append(tune_point(tag, s, ss.configs.get(tag, PointConfig()), opts))
        bar.progress((i + 1) / len(items), text=f"Tuned {i + 1} of {len(items)} points")
    bar.empty()
    ss.results, ss.custom, ss.opts = results, {}, opts
    ss.export_choice, ss.table_v = {}, ss.table_v + 1


# --- main panel -----------------------------------------------------------

st.title("PI Compression Tuner")
st.caption("Recommendations only. Nothing here writes to PI: export a PI Builder sheet, review it, and publish it yourself.")
if ss.source_note:
    st.info(ss.source_note)

if ss.series is None:
    st.markdown("Load data in the sidebar to start. **Demo data** shows the whole workflow without a PI server.")
    st.stop()
if ss.results is None:
    n = sum(len(s) for s in ss.series.values())
    st.markdown(f"**{len(ss.series)} points** loaded ({n:,} events). Choose tuning settings and press **Run tuning**.")
    st.stop()

run_opts: TuneOptions = ss.get("opts", opts)


def effective(r):
    """The result with any custom setting the user chose in place of the recommendation."""
    c = ss.custom.get(r.tag)
    return dataclasses.replace(r, recommended=c, notes=r.notes + ["Custom setting chosen in the UI."]) if c else r


results = [effective(r) for r in ss.results]

# Summary
counts = pd.Series([r.classification.value for r in results]).value_counts()
cur_total = sum(r.current.events_per_day for r in results if r.current)
rec_total = sum((r.recommended or r.current).events_per_day for r in results if r.current)
cols = st.columns(2 + len(counts))
cols[0].metric("Points", len(results))
for col, (name, n) in zip(cols[1:], counts.items()):
    col.metric(name, n)
if cur_total:
    cols[-1].metric("Archive events per day", f"{rec_total:,.0f}", f"{100 * (rec_total - cur_total) / cur_total:+.1f}%",
                    delta_color="off")

# Results table with an editable Export column
st.subheader("Recommendations")
shown = st.multiselect("Show classes", [c.value for c in Classification], default=list(counts.index), key="class_filter")
df = results_frame(results)
df.insert(0, "Export", [r.changes and (r.safe_to_bulk_apply or r.tag in ss.custom) for r in results])
df.insert(2, "", [CLASS_ICON[r.classification] for r in results])
df["Export"] = [ss.export_choice.get(t, e) for t, e in zip(df["Tag"], df["Export"])]
view = df[df["Classification"].isin(shown)]
cols_shown = ["Export", "", "Tag", "Classification", "CurrentCompDev", "CurrentFidelity", "CurrentEventsPerDay",
              "RecommendedCompDev", "RecommendedExcDev", "RecommendedFidelity", "RecommendedEventsPerDay",
              "EventChangePercent", "Notes"]
if run_opts.input_mode != "raw":
    cols_shown.remove("RecommendedExcDev")
edited = st.data_editor(
    view[cols_shown], hide_index=True, use_container_width=True, key=f"table_{ss.table_v}",
    disabled=[c for c in cols_shown if c != "Export"],
    column_config={
        "Export": st.column_config.CheckboxColumn(help="Include in the PI Builder sheet as a selected row."),
        "": st.column_config.TextColumn(width="small"),
        "CurrentFidelity": st.column_config.NumberColumn("Fidelity", format="percent"),
        "RecommendedFidelity": st.column_config.NumberColumn("Rec. fidelity", format="percent"),
        "CurrentCompDev": st.column_config.NumberColumn("CompDev", format="%.4g"),
        "RecommendedCompDev": st.column_config.NumberColumn("Rec. CompDev", format="%.4g"),
        **({"RecommendedExcDev": st.column_config.NumberColumn("Rec. ExcDev", format="%.4g")}
           if "RecommendedExcDev" in cols_shown else {}),
        "CurrentEventsPerDay": st.column_config.NumberColumn("Events/day", format="%.0f"),
        "RecommendedEventsPerDay": st.column_config.NumberColumn("Rec. events/day", format="%.0f"),
        "EventChangePercent": st.column_config.NumberColumn("Change", format="%+.0f%%"),
    },
)
ss.export_choice.update(dict(zip(edited["Tag"], edited["Export"])))

# Point detail
st.subheader("Point detail")
tag = st.selectbox("Point", [r.tag for r in results], key="detail_tag",
                   format_func=lambda t: f"{CLASS_ICON[next(r for r in results if r.tag == t).classification]} {t}")
r = next(x for x in results if x.tag == tag)
s: Series = ss.series[tag]
st.markdown(f"**{r.classification.value}**" + (f": {' '.join(r.notes)}" if r.notes else ""))

if r.current is not None:
    left, right = st.columns([3, 1])
    with right:
        st.markdown("**Try a custom setting**")
        base = (r.recommended or r.current).config
        c_comp = st.number_input("CompDev", min_value=0.0, value=float(base.compdev), format="%.5g", key=f"cd_{tag}")
        c_exc = st.number_input("ExcDev", min_value=0.0, value=float(base.excdev), format="%.5g", key=f"ed_{tag}",
                                disabled=run_opts.input_mode != "raw",
                                help="Only tunable when the data is raw interface values.")
        custom_cfg = dataclasses.replace(r.current.config, compdev=c_comp, compressing=True,
                                         excdev=c_exc if run_opts.input_mode == "raw" else r.current.config.excdev)
        custom_ev = evaluate_config(s, custom_cfg, run_opts, noise=r.noise)
        st.metric("Fidelity", f"{custom_ev.score.fidelity:.1%}",
                  "meets target" if custom_ev.score.fidelity >= run_opts.target else "below target",
                  delta_color="normal" if custom_ev.score.fidelity >= run_opts.target else "inverse")
        st.metric("Archive events per day", f"{custom_ev.events_per_day:,.0f}")
        st.metric("Largest error", f"{custom_ev.score.max_error:.4g}")
        b1, b2 = st.columns(2)
        if b1.button("Use this", key=f"use_{tag}"):
            ss.custom[tag] = custom_ev
            ss.export_choice[tag] = True
            ss.table_v += 1  # rebuild the table so it shows the new values
            st.rerun()
        if tag in ss.custom and b2.button("Revert", key=f"revert_{tag}"):
            del ss.custom[tag]
            ss.export_choice.pop(tag, None)
            ss.table_v += 1
            st.rerun()

    with left:
        def archive_of(cfg):
            sim = simulate_archive(s, cfg, apply_exception=run_opts.input_mode == "raw")
            vis = sim.visible()
            return s.t[vis], s.v[vis]

        def ts(t):
            return pd.to_datetime(t, unit="s", utc=True)

        fig = go.Figure()
        et, ev = envelope(s.t, s.v, 3000)
        fig.add_scatter(x=ts(et), y=ev, name="Input signal", line=dict(color=COLORS["raw"], width=1))
        ct, cv = archive_of(r.current.config)
        ct, cv = envelope(ct, cv, 3000)
        fig.add_scatter(x=ts(ct), y=cv, name=f"Current (CompDev {r.current.config.compdev:.4g})",
                        line=dict(color=COLORS["cur"], width=1.3))
        if r.recommended is not None and r.changes:
            rt, rv = archive_of(r.recommended.config)
            label = "Custom" if tag in ss.custom else "Recommended"
            fig.add_scatter(x=ts(rt), y=rv, name=f"{label} (CompDev {r.recommended.config.compdev:.4g})",
                            mode="lines+markers", marker=dict(size=4),
                            line=dict(color=COLORS["custom" if tag in ss.custom else "rec"], width=2))
        if abs(custom_cfg.compdev - (r.recommended or r.current).config.compdev) > 1e-12:
            kt, kv = archive_of(custom_cfg)
            fig.add_scatter(x=ts(kt), y=kv, name=f"Trying CompDev {custom_cfg.compdev:.4g}",
                            line=dict(color=COLORS["custom"], width=1.5, dash="dot"))
        fig.update_layout(height=460, margin=dict(l=10, r=10, t=30, b=10), legend=dict(orientation="h", y=1.08),
                          xaxis=dict(rangeslider=dict(visible=True)), hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Drag on the chart or the slider below it to zoom. Archived lines are drawn the way PI interpolates them.")

# Downloads
st.subheader("Export")
server_name = st.text_input("Data Archive name for the PI Builder Parent column", ss.get("pi_server", ""), key="parent")
selected = {t for t, on in ss.export_choice.items() if on}
pib = pibuilder_frame(results, server=server_name, include_excdev=run_opts.input_mode == "raw", selected=selected)
st.markdown(f"The PI Builder sheet has **{len(pib)}** changed points, **{(pib['Selected(x)'] == 'x').sum()}** of them selected.")
d1, d2, d3 = st.columns(3)
d1.download_button("PI Builder sheet (CSV)", pib.to_csv(index=False, float_format="%.6g"), "pibuilder.csv", "text/csv",
                   type="primary")
d2.download_button("Results table (CSV)", results_frame(results).to_csv(index=False, float_format="%.6g"),
                   "results.csv", "text/csv")
with tempfile.TemporaryDirectory() as d:
    write_report(results, ss.series, run_opts, Path(d) / "report.html")
    d3.download_button("Report (HTML)", (Path(d) / "report.html").read_bytes(), "report.html", "text/html")
