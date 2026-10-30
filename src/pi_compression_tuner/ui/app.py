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
import re
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from pi_compression_tuner import present
from pi_compression_tuner.demo import demo_data, demo_info
from pi_compression_tuner.export import pibuilder_frame, results_frame
from pi_compression_tuner.metrics import reconstruct
from pi_compression_tuner.models import PointConfig, Series
from pi_compression_tuner.moments import moment_index, moments_frame, read_moments, suggest_moments
from pi_compression_tuner.report import envelope, write_report
from pi_compression_tuner.simulate import simulate_archive
from pi_compression_tuner.sources.csvfiles import read_configs, read_point_info, read_series
from pi_compression_tuner.templates import match, point_info, tune_with_template
from pi_compression_tuner.tune import Classification, TuneOptions, evaluate_config, tradeoff_curve
from pi_compression_tuner.ui import admin, style
from pi_compression_tuner.ui.style import INK, VERDICT_COLOR

WINDOWS = {"15 min": 900, "30 min": 1800, "1 h": 3600, "4 h": 14400}
TREND_FULL_RES = 200_000  # input events drawn at full resolution in the trend; longer series are thinned
PLOT_CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"]}

st.set_page_config(page_title="PI Compression Tuner", layout="wide", initial_sidebar_state="expanded")
st.markdown(style.CSS, unsafe_allow_html=True)
ss = st.session_state
for k, v in {"series": None, "configs": {}, "infos": {}, "results": None, "adopted": {}, "source_note": "",
             "export_choice": {}, "table_v": 0, "curves": {}, "view": "tuner", "moments": {}, "km_v": 0}.items():
    ss.setdefault(k, v)


def _reset_results():
    ss.results, ss.adopted, ss.curves, ss.export_choice = None, {}, {}, {}
    ss.table_v += 1


def _new_data():
    """Forget results and key moments when a new data set is loaded."""
    _reset_results()
    ss.moments = {}
    ss.km_v += 1


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


def _pi_settings():
    """The connection settings of the server picked in the sidebar, from the Admin page's list."""
    return admin.app_settings().server(ss.get("pi_server_label"))


def _pi_connection():
    """One connection per set of connection settings, kept so a search and the read that follows share it."""
    from pi_compression_tuner.sources.connect import connect
    s = _pi_settings()
    key = tuple(vars(s).values())
    if ss.get("pi_conn_key") != key:
        ss.pi_conn = connect(s.server, source=s.source, url=s.url or None, auth=s.auth, verify=s.tls_verify(),
                             afsdk_dir=s.afsdk_dir or None)
        ss.pi_conn_key, ss.pi_found = key, {}
        if ss.pi_conn.note:
            st.toast(ss.pi_conn.note)
    return ss.pi_conn


def _search_filter(text: str) -> str:
    """A plain word searches anywhere in the name; text with * or ? is used as PI's own name filter."""
    text = text.strip()
    if not text:
        return "*"
    return text if any(c in text for c in "*?") else f"*{text}*"


SEARCH_FIELDS = ("pi_search", "pi_find_source", "pi_find_units", "pi_find_desc")


def _search_pi():
    conn = _pi_connection()
    limit = admin.app_settings().max_results
    from pi_compression_tuner.sources.base import PointFilter
    where = PointFilter(pointsource=ss.get("pi_find_source", ""), engunits=ss.get("pi_find_units", ""),
                        descriptor=ss.get("pi_find_desc", ""))
    refs = conn.source.find_points(_pi_settings().server, _search_filter(ss.get("pi_search", "")), max_count=limit,
                                   where=where)
    ss.pi_found = {r.name: r for r in refs}
    ss.pi_found_capped = len(refs) >= limit
    ss.pi_scan_capped = bool(getattr(conn.source, "last_search", {}).get("capped"))
    ss.pi_picked = [t for t in ss.get("pi_picked", []) if t in ss.pi_found]
    ss.pi_table_v = ss.get("pi_table_v", 0) + 1


def _load_pi(refs, start, end):
    from pi_compression_tuner.sources.ops import fetch_recorded
    from pi_compression_tuner.sources.piwebapi import config_from_attributes
    conn = _pi_connection()
    configs, infos = {}, {}
    for r in refs:
        if hasattr(conn.source, "attributes"):
            attrs = conn.source.attributes(r)
            configs[r.name] = config_from_attributes(attrs)
        else:
            attrs, configs[r.name] = {}, conn.source.config(r)
        infos[r.name] = point_info(r.name, attrs, r)
    bar = st.progress(0.0, text="Reading archived values")
    series = fetch_recorded(conn.source, refs, start, end, progress=lambda i, n: bar.progress(i / n, text=f"Read {i} of {n} points"))
    bar.empty()
    via = "PI Web API" if conn.kind == "webapi" else "AF SDK"
    return series, configs, infos, f"{_pi_settings().label} · {len(refs)} points · {via}"


def _set_picked(names):
    ss.pi_picked = names
    ss.pi_table_v = ss.get("pi_table_v", 0) + 1


def _point_picker(found):
    """The PI search results as a table to tick points in. Names are long, so this lives in the main area."""
    picked = set(ss.get("pi_picked", []))
    st.markdown(f"#### Points found in PI · {len(picked)} of {len(found)} ticked")
    if ss.get("pi_found_capped"):
        st.warning(f"The search stopped at {len(found):,} points, the most a search returns. Narrow the search, "
                   "or raise the limit on the Admin page.")
    templates = admin.app_settings().templates
    b1, b2, _ = st.columns([1, 1, 5])
    b1.button("Tick all", key="pi_pick_all", use_container_width=True, on_click=_set_picked, args=(list(found),))
    b2.button("Clear", key="pi_pick_none", use_container_width=True, on_click=_set_picked, args=([],))
    table = pd.DataFrame({"Read": [t in picked for t in found], "Tag": list(found),
                          "Description": [r.descriptor for r in found.values()],
                          "Units": [r.engunits for r in found.values()],
                          "Template": [getattr(match(templates, point_info(t, ref=r)), "name", "") for t, r in found.items()]})
    edited = st.data_editor(table, key=f"pi_table_{ss.get('pi_table_v', 0)}", hide_index=True, use_container_width=True,
                            disabled=["Tag", "Description", "Units", "Template"], height=min(40 + 35 * len(table), 460),
                            column_config={"Read": st.column_config.CheckboxColumn(width="small")})
    now = edited.loc[edited["Read"], "Tag"].tolist()
    if set(now) != picked:
        ss.pi_picked = now
        st.rerun()  # so the sidebar's Read button shows the new count


def _go(view):
    ss.view = view


with st.sidebar:
    st.markdown('<div class="pct-brand"><b>PI Compression Tuner</b><span>Read-only · recommendations</span></div>',
                unsafe_allow_html=True)
    if ss.view == "admin":
        st.button("← Back to the tuner", key="admin_back", use_container_width=True, on_click=_go, args=("tuner",))
        st.caption("Admin settings: PI servers, the search limit and point templates.")

if ss.view == "admin":
    admin.render()
    st.stop()

with st.sidebar:
    st.markdown("### Data")
    origin = st.radio("Source", ["Demo data", "CSV files", "PI server"], key="origin", label_visibility="collapsed",
                      help="CSV files can come from `pict pi record` (snapshot stream) or any export.")
    if origin == "Demo data":
        days = st.slider("Days of data", 1, 7, 3, key="demo_days")
        if st.button("Load demo data", type="primary", key="load_demo", use_container_width=True):
            ss.series, ss.configs = demo_data(days)
            ss.infos = demo_info()
            ss.source_note = f"Demo · 4 points · {days} days"
            _new_data()
    elif origin == "CSV files":
        data_up = st.file_uploader("Data", type="csv", key="data_up",
                                   help="Long (Tag, Timestamp, Value) or wide (Timestamp, one column per tag).")
        cfg_up = st.file_uploader("Point settings (optional)", type="csv", key="cfg_up",
                                  help="PI Builder column names: Name, ExcDev, CompDev, CompMax, Span, ... "
                                       "Descriptor, EngUnits and PointType columns let templates match.")
        km_up = st.file_uploader("Key moments (optional)", type="csv", key="km_up",
                                 help="Tag and Timestamp columns: events each proposal must keep. "
                                      "The Export tab saves them in this format.")
        if st.button("Load files", type="primary", disabled=data_up is None, key="load_csv", use_container_width=True):
            try:
                ss.series = _read_upload(data_up, read_series)
                ss.configs = _read_upload(cfg_up, read_configs) if cfg_up else {}
                ss.infos = _read_upload(cfg_up, read_point_info) if cfg_up else {}
                ss.source_note = f"{data_up.name} · {len(ss.series)} points"
                _new_data()
                if km_up:
                    ss.moments = {t: sorted(v.tolist()) for t, v in _read_upload(km_up, read_moments).items()}
            except Exception as e:
                st.error(f"Could not read the files: {e}")
    else:
        app_cfg = admin.app_settings()
        labels = app_cfg.labels()
        if not labels:
            st.info("No PI server is set up yet. Add one on the Admin page.")
            server = ""
        else:
            if ss.get("pi_server_label") not in labels:
                ss.pi_server_label = app_cfg.server().label
            st.selectbox("PI server", labels, key="pi_server_label")
            chosen = _pi_settings()
            server = chosen.server
            route = {"auto": "PI Web API, then AF SDK", "webapi": "PI Web API", "afsdk": "AF SDK"}.get(chosen.source, chosen.source)
            st.caption(f"{chosen.server} · {route}")

        st.markdown("**Points**")
        with st.form("pi_search_form", border=False):
            st.text_input("Tag mask", placeholder="FIC or FIC1*", key="pi_search",
                          help="A word matches anywhere in the name. Use * and ? for PI's own name filter. "
                               "Leave empty for any name.")
            st.text_input("Point source", placeholder="OPC or R*", key="pi_find_source",
                          help="The interface's point source. * and ? are wildcards.")
            c1, c2 = st.columns(2)
            c1.text_input("Units", placeholder="degF", key="pi_find_units",
                          help="Engineering units, exactly (any case), or with * and ? wildcards.")
            c2.text_input("Description", placeholder="flow", key="pi_find_desc",
                          help="Text anywhere in the description, or a pattern with * and ?.")
            search_clicked = st.form_submit_button("Search PI", key="pi_search_go", use_container_width=True,
                                                   disabled=not server)
        if search_clicked:
            if not any(ss.get(k, "").strip() for k in SEARCH_FIELDS):
                st.warning("Fill in at least one search field.")
            else:
                try:
                    _search_pi()
                except Exception as e:
                    st.error(f"Could not search PI: {e}")
        found = ss.get("pi_found", {})
        if found:
            more = " (the search limit; see Admin)" if ss.get("pi_found_capped") else ""
            if ss.get("pi_scan_capped"):
                more += ". The search stopped after reading 100,000 names; add a tag mask to cover the rest"
            st.caption(f"{len(found)} found{more}. Tick the points to read in the table on the right.")
        with st.expander("Or paste tag names"):
            st.text_area("One per line", key="pi_tags", height=90, label_visibility="collapsed")
        c1, c2 = st.columns(2)
        start = c1.text_input("Start", "*-7d", key="pi_start")
        end = c2.text_input("End", "*", key="pi_end")
        pasted = [t.strip() for t in ss.get("pi_tags", "").splitlines() if t.strip()]
        picked = [t for t in ss.get("pi_picked", []) if t in found]
        n = len(dict.fromkeys(picked + pasted))
        st.caption("Reads archived values: right for points whose compression is off or very tight. "
                   "For the rest, record with `pict pi record` and load the CSV files.")
        if st.button(f"Read {n} point{'s' if n != 1 else ''} from PI" if n else "Read from PI", type="primary",
                     disabled=not (server and n), key="load_pi", use_container_width=True):
            try:
                conn = _pi_connection()
                refs = [found[t] for t in picked] + [conn.source.point(server, t) for t in pasted if t not in picked]
                ss.series, ss.configs, ss.infos, ss.source_note = _load_pi(refs, start, end)
                _new_data()
                empty = [t for t, sr in ss.series.items() if len(sr) == 0]
                if empty:
                    st.warning(f"{len(empty)} of {len(ss.series)} points came back with no numeric values between "
                               f"{start} and {end}: {', '.join(empty[:5])}{' …' if len(empty) > 5 else ''}. "
                               "Check them in PI, or compare with `pict pi check --tag NAME`.")
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
        search_ratio = st.checkbox("Search ExcDev as 0.1 to 1 × CompDev", True, key="search_ratio",
                                   disabled=mode != "raw",
                                   help="Raw mode: try several ExcDev ratios and keep the one with the fewest events.")
        excdev_ratio = st.number_input("ExcDev as a fraction of CompDev", 0.0, 1.0, 0.5, 0.05, key="excdev_ratio",
                                       disabled=mode != "raw" or search_ratio)
    use_templates = st.checkbox("Use point templates", True, key="use_templates",
                                help="Match each point to a template from the Admin page, for its baseline settings "
                                     "and any target or error limit the template sets.")
    run = st.button("Run tuning", type="primary", disabled=ss.series is None, key="run", use_container_width=True)
    st.divider()
    st.button("Admin settings", key="admin_open", use_container_width=True, on_click=_go, args=("admin",),
              help="PI servers, the search limit and point templates.")

opts = TuneOptions(target=target_pct / 100, input_mode=mode, window_s=WINDOWS[window], max_error=max_err or None,
                   max_error_pct_span=max_err_pct or None, excdev_ratio=None if search_ratio else excdev_ratio)

if run and ss.series is not None:
    results = []
    bar = st.progress(0.0, text="Tuning")
    items = list(ss.series.items())
    for i, (tag, s) in enumerate(items):
        results.append(tune_with_template(tag, s, ss.configs.get(tag, PointConfig()), opts, ss.infos.get(tag),
                                          admin.app_settings().templates if use_templates else [],
                                          moments=ss.moments.get(tag, ())))
        bar.progress((i + 1) / len(items), text=f"Tuned {i + 1} of {len(items)} points")
    bar.empty()
    _reset_results()
    ss.results, ss.opts = results, opts


# --- header and empty states ----------------------------------------------

st.markdown(style.header(ss.source_note), unsafe_allow_html=True)

if ss.origin == "PI server" and ss.get("pi_found"):
    if ss.series is None:
        _point_picker(ss.pi_found)
    else:
        with st.expander(f"Points found in PI ({len(ss.pi_found)})"):
            _point_picker(ss.pi_found)

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
# Points without a proposal (too few events, not moving) go last in the point picker.
NO_PROPOSAL = (Classification.TOO_FEW_EVENTS.value, Classification.NOT_MOVING.value)
tag_order = sorted(by_tag, key=lambda t: by_tag[t].classification.value in NO_PROPOSAL)

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
    verdicts_found = list(dict.fromkeys(r.classification.value for r in results))
    # Rows with no proposal are blank across the table, so leave them out unless they are all there is.
    default_shown = [c for c in verdicts_found if c not in NO_PROPOSAL] or verdicts_found
    shown = st.multiselect("Show verdicts", [c.value for c in Classification], label_visibility="collapsed",
                           default=default_shown, key="class_filter",
                           placeholder="Filter by verdict")
    hidden = sum(r.classification.value in NO_PROPOSAL and r.classification.value not in shown for r in results)
    if hidden:
        st.markdown(f'<p class="pct-note">{hidden} points with too few events or no movement have no proposal and '
                    "are hidden. Add them in the filter to list them.</p>", unsafe_allow_html=True)
    df = results_frame(results)
    df.insert(0, "Export", [ss.export_choice.get(r.tag, r.changes and (r.ready_to_export or r.tag in ss.adopted))
                            for r in results])
    for c in ("CurrentEventsPerDay", "RecommendedEventsPerDay"):
        df[c] = df[c].round()
    view = df[df["Classification"].isin(shown)]
    cols = ["Export", "Tag", "Classification", "Template", "CurrentCompDev", "RecommendedCompDev", "RecommendedExcDev",
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
            "Template": st.column_config.TextColumn("Template", width="small"),
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
    tags = tag_order

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
            ref = (r.recommended or r.current).config
            ss.curves[tag] = tradeoff_curve(s, r.current.config, run_opts, noise=r.noise,
                                            excdev_ratio=ref.excdev / ref.compdev if ref.compdev > 0 else None)
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
                    if kind == "cr":
                        return f"<div>{present.fmt_ratio(ev.compression_ratio)}:1</div>"
                    if kind == "rms":
                        return f"<div>{ev.score.rms_error:.3g}</div>"
                    return f"<div>{ev.score.max_error:.3g}</div>"

                grid = '<div class="h k"></div>' + "".join(f'<div class="h">{n}</div>' for n, _ in cols_cmp)
                grid += "".join(f'<div class="k">{label}</div>' + "".join(cell(ev, k) for _, ev in cols_cmp)
                                for label, k in (("CompDev", "cd"), ("Retained", "ret"),
                                                 ("Events/day", "ev"), ("Compression", "cr"),
                                                 ("Max error", "err"), ("RMS error", "rms")))
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
            # Send the full-resolution trend (WebGL draws it quickly) so zooming and panning show real
            # events; only very long series are thinned, and then Largest miss keeps a full-resolution
            # stretch of context around the miss instead.
            full = len(s.t) <= TREND_FULL_RES
            if view == "Largest miss":
                k = int(np.argmax(err))
                half = run_opts.window_s / 2
                lo, hi = s.t[k] - half, s.t[k] + half
                w = (s.t >= lo) & (s.t <= hi)
                if full:
                    keep = np.ones(len(s.t), bool)
                else:
                    span = TREND_FULL_RES / 2 * np.median(np.diff(s.t)) if len(s.t) > 1 else half
                    keep = (s.t >= s.t[k] - span) & (s.t <= s.t[k] + span)
                tt, vv, xt, xv = s.t[keep], s.v[keep], s.t[keep], excess[keep]
            else:
                tt, vv = (s.t, s.v) if full else envelope(s.t, s.v, 3000)
                xt, xv = (s.t, excess) if full else envelope(s.t, excess, 1500)
                w = None
            fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.8, 0.2], vertical_spacing=0.05)
            fig.add_trace(go.Scattergl(x=ts(tt), y=vv, name="Input", line=dict(color=INK["input"], width=1)), row=1, col=1)
            for name, cfg, color in lines:
                at, av = archives[name]
                if w is not None:
                    # one event either side so the segments into the shown stretch are drawn
                    i0 = max(int(np.searchsorted(at, tt[0])) - 1, 0)
                    i1 = int(np.searchsorted(at, tt[-1], side="right")) + 1
                    at, av = at[i0:i1], av[i0:i1]
                    size = 4 if name == "Archive now" else 8
                    mode, mk = "lines+markers", dict(size=size, color=color, line=dict(width=1, color="white"))
                else:
                    mode, mk = "lines", None
                    if len(at) > TREND_FULL_RES:
                        at, av = envelope(at, av, 3000)
                fig.add_trace(go.Scattergl(x=ts(at), y=av, name=name, mode=mode, marker=mk,
                                           line=dict(color=color, width=1.8 if name != "Archive now" else 1.3,
                                                     shape="hv" if cfg.step else "linear")), row=1, col=1)
            pending = sorted(ss.moments.get(tag, []))
            mi = moment_index(s, pending)
            if w is not None:
                mi = mi[w[mi]]
            if len(mi):
                fig.add_scatter(x=ts(s.t[mi]), y=s.v[mi], name="Key moment", mode="markers", row=1, col=1,
                                marker=dict(size=12, symbol="diamond-open", color=INK["probe"], line=dict(width=2)))
            for lim in cfg_now.alarm_limits:
                fig.add_hline(y=lim, line=dict(color=INK["probe"], width=1, dash="dash"), row=1, col=1,
                              annotation_text=f"Alarm {lim:g}", annotation_font_color=INK["probe"],
                              annotation_position="top left")
            fig.add_trace(go.Scattergl(x=ts(xt), y=xv, name="Detail lost", fill="tozeroy", fillcolor="rgba(208,87,74,0.25)",
                                       line=dict(color=INK["loss"], width=1)), row=2, col=1)
            # Plotly reports a click for selection only on markers and only with closest-point hover,
            # so the trend lines get invisible markers to make them clickable for key moments.
            for tr in fig.data:
                if tr.name == "Input" or (tr.name.startswith("Archive") and tr.mode == "lines"):
                    tr.update(mode="lines+markers", marker=dict(size=8, opacity=0))
            style.plot_layout(fig, 460, hovermode="closest")
            fig.update_yaxes(title_text="Value", row=1, col=1)
            fig.update_yaxes(title_text="Lost", row=2, col=1, rangemode="tozero")
            if w is None:
                fig.update_xaxes(range=[ts(s.t[0]), ts(s.t[-1])])  # no padding from the invisible markers
            else:
                wv = s.v[w]
                pad = max(np.ptp(wv), r.noise, 1e-9) * 0.15
                fig.update_xaxes(range=[ts(lo), ts(hi)])
                fig.update_yaxes(range=[wv.min() - pad, wv.max() + pad], row=1, col=1)
            picked = st.plotly_chart(fig, use_container_width=True, config=PLOT_CONFIG, key=f"trend_{tag}_{ss.km_v}",
                                     on_select="rerun", selection_mode="points")
            # A click on the trend marks a key moment at the nearest input event. The lines overlap,
            # so any trace's point counts. Selections persist across reruns, so only a new one adds.
            clicked = tuple(pd.Timestamp(p["x"], tz="UTC").timestamp() if pd.Timestamp(p["x"]).tzinfo is None
                            else pd.Timestamp(p["x"]).timestamp()
                            for p in (picked.selection.points if picked else []) if "x" in p)
            if clicked and clicked != ss.get(f"clicked_{tag}"):
                ss[f"clicked_{tag}"] = clicked
                ss.moments[tag] = sorted(set(pending) | {float(s.t[i]) for i in moment_index(s, clicked)})
                ss.km_v += 1
                st.rerun()
            what = {"Archive now": "today's archive", "Archive proposed": "the proposed archive",
                    "Archive at probe": "the probe's archive"}[judged_name]
            st.markdown(f'<p class="pct-note">The lower strip shows where {what} strays from the input by more than the '
                        "noise band; flat means no real detail is lost. <b>Largest miss</b> zooms to one scoring window "
                        "around the biggest error, with each archived event marked. Zoom and pan freely: every event is "
                        "drawn; double-click the chart to see the whole period.</p>", unsafe_allow_html=True)

            st.markdown(style.section("Key moments"), unsafe_allow_html=True)
            k1, k4, k2, k3 = st.columns([7, 3, 2, 3], vertical_alignment="bottom")
            fmt = lambda t: pd.to_datetime(t, unit="s", utc=True).strftime("%b %d %H:%M:%S")  # noqa: E731
            kept = k1.multiselect("Key moments", pending, default=pending, format_func=fmt, key=f"km_{tag}_{ss.km_v}",
                                  label_visibility="collapsed", placeholder="Click the trend, or press Suggest")
            if sorted(kept) != pending:
                ss.moments[tag] = sorted(kept)
                ss.km_v += 1
                st.rerun()
            lim_text = k4.text_input("Alarm limits", ", ".join(f"{x:g}" for x in cfg_now.alarm_limits),
                                     key=f"lim_{tag}_{ss.km_v}", placeholder="Alarm limits, e.g. 20, 80", label_visibility="collapsed",
                                     help="Lo, Hi, LoLo and HiHi, separated by commas. Suggest marks each crossing.")
            try:
                limits = tuple(sorted({float(x) for x in re.split(r"[;,\s]+", lim_text.strip()) if x}))
            except ValueError:
                limits = cfg_now.alarm_limits
                k4.caption("Use numbers separated by commas.")
            if limits != cfg_now.alarm_limits:
                ss.configs[tag] = dataclasses.replace(cfg_now, alarm_limits=limits)
                st.rerun()
            if k2.button("Suggest", key=f"km_suggest_{tag}", use_container_width=True,
                         help="Mark the main peaks, valleys and steps, and each alarm-limit crossing."):
                ss.moments[tag] = sorted(set(pending) | set(suggest_moments(s, r.noise, limits=limits).tolist()))
                ss.km_v += 1
                st.rerun()
            stale = tuple(pending) != tuple(r.moments)
            if k3.button(f"Retune with {len(pending)} moment{'s' if len(pending) != 1 else ''}" if pending
                         else "Retune without moments", key=f"km_retune_{tag}", type="primary",
                         disabled=not stale, use_container_width=True,
                         help="Find the loosest setting that keeps every key moment."):
                i = [x.tag for x in ss.results].index(tag)
                ss.results[i] = tune_with_template(tag, s, cfg_now, run_opts, ss.infos.get(tag),
                                                   admin.app_settings().templates if ss.get("use_templates", True) else [],
                                                   moments=pending)
                ss.curves.pop(tag, None)
                ss.adopted.pop(tag, None)
                ss.export_choice.pop(tag, None)
                ss.table_v += 1
                st.rerun()
            if pending:
                judged_ev = evaluate_config(s, judged_cfg, run_opts, noise=r.noise, moments=pending)
                keeps = judged_ev.moments_total - judged_ev.moments_missed
                st.markdown(f'<p class="pct-note">{what[0].upper() + what[1:]} keeps <b>{keeps} of '
                            f'{judged_ev.moments_total}</b> key moments.'
                            + (" Press <b>Retune</b> to update the proposal." if stale else "") + "</p>",
                            unsafe_allow_html=True)
            else:
                st.markdown('<p class="pct-note">Mark the events this point must never lose (a trip, a peak, a step, '
                            "an alarm) by clicking them on the trend, or press <b>Suggest</b>. <b>Retune</b> then finds the loosest "
                            "setting that keeps every one.</p>", unsafe_allow_html=True)

# --- Export ---------------------------------------------------------------

with tab_export:
    selected = {t for t, on in ss.export_choice.items() if on} | {
        r.tag for r in results if r.tag not in ss.export_choice and r.changes and (r.ready_to_export or r.tag in ss.adopted)}
    left, right = st.columns([5, 12], gap="large")
    with left:
        server_name = st.text_input("Data Archive (PI Builder Parent column)",
                                    _pi_settings().server if ss.origin == "PI server" else "", key="parent")
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
        if any(ss.moments.values()):
            km = moments_frame({t: v for t, v in ss.moments.items() if v})
            st.download_button(f"Download key moments ({len(km)})", km.to_csv(index=False), "moments.csv", "text/csv",
                               use_container_width=True, help="Load it again with the CSV files, or pass it to "
                                                               "`pict analyze --moments`.")
    with right:
        st.markdown(style.section("PI Builder sheet preview"), unsafe_allow_html=True)
        st.dataframe(pib.astype(object).where(pib.notna(), ""), hide_index=True, use_container_width=True)
        st.markdown(f'<p class="pct-note">The PI Builder sheet has <b>{len(pib)}</b> changed points, <b>{n_sel}</b> of them '
                    "selected. Unselected rows stay in the sheet so you can review them one at a time. "
                    "Open it in Excel with PI Builder and publish when you're satisfied.</p>", unsafe_allow_html=True)
