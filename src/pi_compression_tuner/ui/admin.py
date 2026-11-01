"""The Admin page: PI servers, the search limit and point templates, saved to the settings file."""

from __future__ import annotations

import dataclasses
import math
from datetime import datetime

import pandas as pd
import streamlit as st

from pi_compression_tuner.access import AccessSettings, log_activity, read_activity, split_names
from pi_compression_tuner.settings import (MAX_RESULTS_LIMIT, AppSettings, PISettings, load_app_settings,
                                           save_app_settings, settings_path, settings_stamp)
from pi_compression_tuner.templates import Template, match, point_info, split_list, starter_templates
from pi_compression_tuner.ui import identity, style

SOURCES = {"auto": "PI Web API, then AF SDK", "webapi": "PI Web API only", "afsdk": "AF SDK only"}
AUTHS = ["kerberos", "basic", "anonymous"]
TRI = {"keep": None, "on": True, "off": False}

# Template table columns: (field, label, kind)
TEMPLATE_COLUMNS = [
    ("enabled", "On", "bool"), ("priority", "Priority", "int"), ("name", "Name", "text"),
    ("match_any", "Match any", "flag"),
    ("match_descriptor", "Description has", "list"), ("match_units", "Units", "list"),
    ("match_pointtype", "Point type", "list"), ("match_pointclass", "Point class", "list"),
    ("match_tag", "Tag name like", "list"), ("match_pointsource", "Point source", "list"),
    ("compdev", "CompDev", "num"), ("compdev_pct", "CompDev % span", "num"),
    ("excdev", "ExcDev", "num"), ("excdev_pct", "ExcDev % span", "num"),
    ("compmax", "CompMax s", "num"), ("excmax", "ExcMax s", "num"),
    ("compmin", "CompMin s", "num"), ("excmin", "ExcMin s", "num"),
    ("step", "Step", "tri"), ("compressing", "Compression", "tri"),
    ("target_pct", "Target %", "num"), ("max_error", "Max error", "num"), ("max_error_pct", "Max error % span", "num"),
    ("note", "Note", "text"),
]


def app_settings() -> AppSettings:
    """The saved admin settings, read once per session and again whenever the file changes.

    On a shared server another admin may save while this page is open. The tuner pages pick their changes up
    on the next rerun; the Admin page keeps what it showed, so edits in progress are not lost, and says so.
    """
    ss = st.session_state
    stamp = settings_stamp()
    if "admin" in ss and ss.get("admin_stamp") != stamp and ss.get("view") != "admin":
        del ss["admin"]
    if "admin" not in ss:
        try:
            ss.admin = load_app_settings()
            ss.admin_error = ""
        except ValueError as e:
            ss.admin, ss.admin_error = AppSettings(), str(e)
        ss.admin_stamp = stamp
        ss.pop("pi_conn_key", None)   # reconnect in case the servers changed
    return ss.admin


def _changed_elsewhere() -> bool:
    return st.session_state.get("admin_stamp") != settings_stamp()


def _who() -> str:
    me = identity.viewer()
    return me.user if me else ""


def _save(app: AppSettings, what: str = "settings") -> bool:
    if _changed_elsewhere():
        st.error("Someone else saved the settings after you opened this page, so yours were not saved. "
                 "Press **Load the latest settings** at the top of the page, then make your change again.")
        return False
    try:
        path = save_app_settings(app)
    except (OSError, ValueError) as e:
        st.error(f"Could not save the settings: {e}")
        return False
    st.session_state.admin = app
    st.session_state.admin_stamp = settings_stamp()
    st.session_state.pop("pi_conn_key", None)  # reconnect with the new settings
    log_activity(_who(), "settings saved", what)
    st.toast(f"Saved to {path}")
    return True


def _bump(key: str):
    st.session_state[key] = st.session_state.get(key, 0) + 1


# --- servers ------------------------------------------------------------------

def servers_frame(servers: list[PISettings]) -> pd.DataFrame:
    cols = ["name", "server", "source", "url", "auth", "verify", "ca_bundle", "afsdk_dir"]
    return pd.DataFrame([{c: getattr(s, c) for c in cols} for s in servers], columns=cols).astype(
        {"verify": bool, **{c: object for c in cols if c != "verify"}})


def servers_from_frame(df: pd.DataFrame) -> list[PISettings]:
    out = []
    for _, row in df.iterrows():
        def text(c):
            v = row.get(c)
            return "" if v is None or (isinstance(v, float) and math.isnan(v)) else str(v).strip()
        if not text("server"):
            continue
        verify = row.get("verify")
        out.append(PISettings(server=text("server"), url=text("url"), source=text("source") or "auto",
                              auth=text("auth") or "kerberos", verify=True if verify is None or pd.isna(verify) else bool(verify),
                              ca_bundle=text("ca_bundle"), afsdk_dir=text("afsdk_dir"), name=text("name")))
    labels = [s.label for s in out]
    dupes = sorted({x for x in labels if labels.count(x) > 1})
    if dupes:
        raise ValueError(f"each server needs its own name; repeated: {', '.join(dupes)}")
    return out


def _servers_section(app: AppSettings):
    ss = st.session_state
    st.markdown(style.section("PI servers"), unsafe_allow_html=True)
    st.caption("Each row is a PI Data Archive the tuner can read from. Add a row at the bottom of the table; "
               "select rows and press the bin icon to remove them. The Name is what the server list on the main "
               "page shows. Basic sign-in reads PI_WEBAPI_USER and PI_WEBAPI_PASSWORD from the environment; "
               "no passwords are stored.")
    edited = st.data_editor(
        servers_frame(app.servers), key=f"admin_servers_{ss.get('admin_servers_v', 0)}", hide_index=True,
        num_rows="dynamic", use_container_width=True,
        column_config={
            "name": st.column_config.TextColumn("Name", help="Label in the server list. Defaults to the Data Archive name."),
            "server": st.column_config.TextColumn("Data Archive", required=True),
            "source": st.column_config.SelectboxColumn("Connect through", options=list(SOURCES), default="auto",
                                                       help="auto: PI Web API, then AF SDK if that fails."),
            "url": st.column_config.TextColumn("PI Web API URL", help="https://pihost/piwebapi"),
            "auth": st.column_config.SelectboxColumn("Web API sign-in", options=AUTHS, default="kerberos"),
            "verify": st.column_config.CheckboxColumn("Check TLS", default=True),
            "ca_bundle": st.column_config.TextColumn("CA certificate (PEM)", help="Only for a CA not in the Windows store."),
            "afsdk_dir": st.column_config.TextColumn("AF SDK folder", help="Only if not the standard PIPC folder."),
        },
    )
    try:
        servers = servers_from_frame(edited)
        error = ""
    except ValueError as e:
        servers, error = [], str(e)
    if error:
        st.error(error)
    labels = [s.label for s in servers]
    c1, c2, c3 = st.columns([2, 1, 1])
    default = c1.selectbox("Default server", labels or ["(none)"], key="admin_default",
                           index=labels.index(app.server().label) if app.server().label in labels else 0,
                           disabled=not labels)
    c2.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if c2.button("Save servers", type="primary", key="admin_save_servers", use_container_width=True, disabled=bool(error)):
        new = dataclasses.replace(app, servers=servers, default_server=default if labels else "")
        if _save(new, f"PI servers ({len(servers)})"):
            _bump("admin_servers_v")
            st.rerun()
    c3.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if c3.button("Test default", key="admin_test", use_container_width=True, disabled=not labels or bool(error)):
        s = next(x for x in servers if x.label == default)
        from pi_compression_tuner.sources.connect import connect
        try:
            with st.spinner(f"Connecting to {s.server}"):
                conn = connect(s.server, source=s.source, url=s.url or None, auth=s.auth, verify=s.tls_verify(),
                               afsdk_dir=s.afsdk_dir or None)
            via = "the PI Web API" if conn.kind == "webapi" else "AF SDK"
            st.success(f"Connected to {s.server} through {via}. {conn.note}")
        except Exception as e:
            st.error(f"Could not connect to {s.server}: {e}")


def _search_section(app: AppSettings):
    st.markdown(style.section("Point search"), unsafe_allow_html=True)
    c1, c2 = st.columns([2, 3])
    n = c1.number_input("Most points a search returns", 1, MAX_RESULTS_LIMIT, int(app.max_results), step=100,
                        key="admin_max_results",
                        help="PI Web API pages through results 1,000 at a time, so large searches take longer.")
    c2.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
    if c2.button("Save search limit", key="admin_save_search", disabled=int(n) == app.max_results):
        new = dataclasses.replace(app, max_results=int(n))
        if _save(new, f"search limit {int(n)}"):
            st.rerun()


# --- templates ----------------------------------------------------------------

def templates_frame(templates: list[Template]) -> pd.DataFrame:
    rows = []
    for t in templates:
        row = {}
        for f, _, kind in TEMPLATE_COLUMNS:
            v = getattr(t, f)
            if kind == "list":
                v = ", ".join(v)
            elif kind == "tri":
                v = {None: "keep", True: "on", False: "off"}[v]
            elif kind == "num":
                v = float("nan") if v is None else float(v)
            row[f] = v
        rows.append(row)
    df = pd.DataFrame(rows, columns=[c for c, _, _ in TEMPLATE_COLUMNS])
    return df.astype({c: float for c, _, k in TEMPLATE_COLUMNS if k == "num"} | {"enabled": bool, "match_any": bool})


def templates_from_frame(df: pd.DataFrame) -> list[Template]:
    out = []
    for i, row in df.iterrows():
        d = {}
        for f, label, kind in TEMPLATE_COLUMNS:
            v = row.get(f)
            missing = v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA
            if kind == "text":
                d[f] = "" if missing else str(v).strip()
            elif kind == "list":
                d[f] = [] if missing else split_list(str(v))
            elif kind in ("bool", "flag"):
                d[f] = (kind == "bool") if missing else bool(v)
            elif kind == "int":
                d[f] = 100 if missing else int(v)
            elif kind == "tri":
                d[f] = TRI.get("keep" if missing else str(v), None)
            else:
                if not missing and float(v) < 0:
                    raise ValueError(f"row {len(out) + 1} ({d.get('name') or 'no name'}): {label} can't be negative")
                d[f] = None if missing else float(v)
        if not d["name"] and not any(d[f] for f in ("match_descriptor", "match_units", "match_tag")):
            continue  # an empty row the editor added
        if not d["name"]:
            raise ValueError(f"row {len(out) + 1}: every template needs a name")
        if d["target_pct"] is not None and not 50 <= d["target_pct"] <= 100:
            raise ValueError(f"{d['name']}: Target % must be between 50 and 100")
        out.append(Template.from_dict(d))
    names = [t.name for t in out]
    dupes = sorted({x for x in names if names.count(x) > 1})
    if dupes:
        raise ValueError(f"each template needs its own name; repeated: {', '.join(dupes)}")
    return out


def _templates_section(app: AppSettings):
    ss = st.session_state
    st.markdown(style.section("Point templates"), unsafe_allow_html=True)
    st.caption(
        "A template gives a starting point for one kind of point, matched on its description, units, point type, "
        "point class, point source or tag name. Every column you fill in must match (or just one, with Match any "
        "ticked); within a cell, any of the comma-separated entries can match. A description word matches whole words; use * and ? for patterns. "
        "The lowest Priority wins when several match. Blank settings leave the point's own value. ExcDev left "
        "blank is half of CompDev. Target % and the error limits override the run's settings for these points.")
    enabled = sum(t.enabled for t in app.templates)
    st.markdown(f"**{len(app.templates)} templates · {enabled} on**")
    edited = st.data_editor(
        templates_frame(app.templates), key=f"admin_templates_{ss.get('admin_templates_v', 0)}", hide_index=True,
        num_rows="dynamic", use_container_width=True, height=min(80 + 35 * len(app.templates), 560),
        column_config={
            f: (st.column_config.CheckboxColumn(label, default=True, width="small") if kind == "bool"
                else st.column_config.CheckboxColumn(label, default=False, width="small",
                                                     help="Ticked: any one filled-in rule column is enough. "
                                                          "Clear: every filled-in column must match.") if kind == "flag"
                else st.column_config.NumberColumn(label, default=100, step=1, width="small") if kind == "int"
                else st.column_config.SelectboxColumn(label, options=list(TRI), default="keep", width="small") if kind == "tri"
                else st.column_config.NumberColumn(label, min_value=0.0, format="%g", width="small") if kind == "num"
                else st.column_config.TextColumn(label, width="medium" if f in ("name", "match_descriptor", "note") else "small"))
            for f, label, kind in TEMPLATE_COLUMNS
        },
        column_order=[c for c, _, _ in TEMPLATE_COLUMNS],
    )
    try:
        templates, error = templates_from_frame(edited), ""
    except (ValueError, TypeError) as e:
        templates, error = [], str(e)
    if error:
        st.error(error)
    c1, c2, c3, _ = st.columns([1, 1, 1, 2])
    if c1.button("Save templates", type="primary", key="admin_save_templates", use_container_width=True, disabled=bool(error)):
        if _save(dataclasses.replace(app, templates=templates), f"point templates ({len(templates)})"):
            _bump("admin_templates_v")
            st.rerun()
    if c2.button("Add missing starters", key="admin_add_starters", use_container_width=True,
                 help="Adds any starter template whose name isn't in the list. Existing templates are kept."):
        have = {t.name for t in app.templates}
        extra = [t for t in starter_templates() if t.name not in have]
        if not extra:
            st.info("Every starter template is already in the list.")
        elif _save(dataclasses.replace(app, templates=app.templates + extra), f"added {len(extra)} starter templates"):
            _bump("admin_templates_v")
            st.rerun()
    if c3.button("Discard edits", key="admin_discard_templates", use_container_width=True):
        _bump("admin_templates_v")
        st.rerun()

    with st.expander("Try the templates on a point"):
        a, b, c, d = st.columns(4)
        tag = a.text_input("Tag", key="try_tag")
        desc = b.text_input("Description", key="try_desc")
        units = c.text_input("Units", key="try_units")
        ptype = d.selectbox("Point type", ["Float32", "Float64", "Float16", "Int32", "Int16", "Digital", "String"],
                            key="try_type")
        if tag or desc or units:
            t = match(templates if not error else app.templates,
                      point_info(tag, {"descriptor": desc, "engunits": units, "pointtype": ptype}))
            if t:
                st.success(f"**{t.name}** · {t.summary() or 'no settings'}")
            else:
                st.info("No enabled template matches. The point is tuned on the run's settings alone.")
        st.caption("Uses the table as edited, before saving.")


# --- access -------------------------------------------------------------------

def would_lock_out(me: identity.Viewer, access: AccessSettings) -> bool:
    """True if saving ``access`` would take the signed-in admin's own admin rights away.

    Their rights came through one entry (``me.via``); keeping that entry, or naming them, keeps them an admin.
    """
    if not me.shared:
        return False
    keep = {x.lower() for x in access.admins}
    return me.user.lower() not in keep and me.via.lower() not in keep


def _access_section(app: AppSettings, me: identity.Viewer):
    ss = st.session_state
    st.markdown(style.section("Who may sign in"), unsafe_allow_html=True)
    st.caption(
        "Applies when the tuner runs as a shared server (`pict serve`, or the Windows service). People sign in with "
        "their Windows account. Write each account or group as DOMAIN\\name, one per line; a group covers everyone in "
        "it. Admins can use the tuner and change these settings; users can use the tuner. Changes apply the next time "
        "someone signs in (at the latest 8 hours later, or when the server restarts). PI is read as the account the "
        "server runs as, so give that account a read-only PI mapping.")
    v = ss.get("admin_access_v", 0)
    c1, c2 = st.columns(2)
    admins_text = c1.text_area("Admins", "\n".join(app.access.admins), height=130, key=f"access_admins_{v}",
                               help="BUILTIN\\Administrators is the server's local Administrators group.")
    users_text = c2.text_area("Users", "\n".join(app.access.users), height=130, key=f"access_users_{v}",
                              help="For example CORP\\PI Engineers. Leave empty if only admins use the tuner.")
    new = AccessSettings(admins=split_names(admins_text), users=split_names(users_text))
    error = ""
    if not new.admins:
        error = "Keep at least one admin entry, or nobody can change these settings."
    elif would_lock_out(me, new):
        error = (f"You are an admin through **{me.via}**. Keep that entry, or add {me.user}, so you don't lock "
                 "yourself out.")
    bad = [x for x in new.admins + new.users if "\\" not in x and "@" not in x]
    if bad:
        st.warning(f"Write accounts and groups with their domain, as DOMAIN\\name: {', '.join(bad)}")
    if error:
        st.error(error)
    b1, b2, _ = st.columns([1, 1, 3])
    if b1.button("Save access", type="primary", key="admin_save_access", use_container_width=True,
                 disabled=bool(error) or new == app.access):
        if _save(dataclasses.replace(app, access=new),
                 f"access: admins {', '.join(new.admins)}; users {', '.join(new.users) or 'none'}"):
            _bump("admin_access_v")
            st.rerun()
    if b2.button("Discard edits", key="admin_discard_access", use_container_width=True, disabled=new == app.access):
        _bump("admin_access_v")
        st.rerun()


def _activity_section():
    rows = read_activity(200)
    st.markdown(style.section("Recent activity"), unsafe_allow_html=True)
    if not rows:
        st.caption("Sign-ins, turned-away visits and saved settings are listed here once the server has some.")
        return
    df = pd.DataFrame(rows, columns=["Time", "Who", "What", "Detail"])
    st.dataframe(df, hide_index=True, use_container_width=True, height=min(80 + 35 * len(df), 360))
    st.caption("The newest 200 entries of the activity log, kept beside the settings file.")


def render(me: identity.Viewer | None = None):
    me = me or identity.Viewer("", True)
    app = app_settings()
    st.markdown(style.header("Admin settings"), unsafe_allow_html=True)
    if st.session_state.get("admin_error"):
        st.error(f"The settings file could not be read, so defaults are shown: {st.session_state.admin_error}")
    st.caption(f"Saved in {settings_path()}. Recommendations only: nothing here writes to PI.")
    if _changed_elsewhere():
        last = next((r for r in read_activity(50) if r[2] == "settings saved"), None)
        who = f" by {last[1]} at {_when(last[0])}" if last and last[1] != "-" else ""
        c1, c2 = st.columns([4, 1])
        c1.warning(f"The settings were saved{who} since you opened this page. Load them before making changes.")
        if c2.button("Load the latest settings", key="admin_reload", use_container_width=True):
            for k in ("admin_servers_v", "admin_templates_v", "admin_access_v"):
                _bump(k)
            for k in ("admin", "admin_max_results", "admin_default"):   # widgets showing the old values
                st.session_state.pop(k, None)
            st.rerun()
    _servers_section(app)
    _search_section(app)
    _templates_section(app)
    _access_section(app, me)
    if me.shared:
        _activity_section()


def _when(stamp: str) -> str:
    try:
        return datetime.fromisoformat(stamp).strftime("%H:%M on %b %d")
    except ValueError:
        return stamp
