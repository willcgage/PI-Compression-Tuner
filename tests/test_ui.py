from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("plotly")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).parents[1] / "src" / "pi_compression_tuner" / "ui" / "app.py")


def _loaded_and_run():
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.slider(key="demo_days").set_value(1)
    at.button(key="load_demo").click().run()
    at.button(key="run").click().run()
    assert not at.exception, at.exception
    return at


def test_demo_workflow_produces_recommendations():
    at = _loaded_and_run()
    text = " ".join(m.value for m in at.markdown)
    assert "PI Compression Tuner" in text and "Proposed changes" in text
    assert [t.label for t in at.tabs] == ["Overview", "Point detail", "Export"]
    assert any("PI Builder sheet has" in m.value for m in at.markdown)


def test_probe_value_can_be_adopted():
    at = _loaded_and_run()
    at.selectbox(key="detail_tag").set_value("DEMO.TEMP.PV").run()
    at.number_input(key="cd_DEMO.TEMP.PV").set_value(0.1).run()
    at.button(key="adopt_DEMO.TEMP.PV").click().run()
    assert not at.exception, at.exception
    assert any("CompDev adopted from the probe" in m.value for m in at.markdown)


def _save_servers(*servers, max_results=1000):
    from pi_compression_tuner.settings import AppSettings, save_app_settings
    save_app_settings(AppSettings(servers=list(servers), default_server=servers[0].label if servers else "",
                                  max_results=max_results))


def test_pi_errors_are_shown_not_raised():
    from pi_compression_tuner.settings import PISettings
    _save_servers(PISettings(server="NOSUCHSERVER", source="webapi", url="https://127.0.0.1:9/piwebapi"))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    at.text_input(key="pi_search").input("FIC")
    at.button(key="pi_search_go").click().run()
    assert not at.exception
    assert at.error and "Could not search PI" in at.error[0].value


class FakePI:
    """A PI source holding a few demo signals."""

    def __init__(self):
        from pi_compression_tuner.demo import demo_data
        self.series, self.configs = demo_data(1)
        self.filters = []
        self.wheres = []

    def find_points(self, server, name_filter="*", max_count=1000, where=None):
        from fnmatch import fnmatch

        from pi_compression_tuner.demo import DEMO_INFO
        from pi_compression_tuner.sources.base import PointFilter, PointRef
        self.filters.append(name_filter)
        self.wheres.append(where)
        refs = [PointRef(t, descriptor=DEMO_INFO[t]["descriptor"], engunits=DEMO_INFO[t]["engunits"])
                for t in self.series if fnmatch(t, name_filter)]
        return [r for r in refs if (where or PointFilter()).keeps(r)][:max_count]

    def point(self, server, tag):
        from pi_compression_tuner.sources.base import PointRef
        return PointRef(tag)

    def config(self, ref):
        return self.configs[ref.name]

    def recorded(self, ref, start, end):
        return self.series[ref.name]


def test_search_pick_and_read_from_pi(monkeypatch):
    from pi_compression_tuner.sources import connect as connect_mod
    fake = FakePI()
    monkeypatch.setattr(connect_mod, "connect", lambda *a, **k: connect_mod.Connection(fake, "webapi"))
    from pi_compression_tuner.settings import PISettings
    _save_servers(PISettings(server="PISRV", url="https://pi/piwebapi"))
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    at.text_input(key="pi_search").input("DEMO")
    at.button(key="pi_search_go").click().run()
    assert fake.filters == ["*DEMO*"]
    assert any("4 found" in c.value for c in at.caption)
    at.button(key="pi_pick_all").click().run()
    assert any("4 of 4 ticked" in m.value for m in at.markdown)
    at.button(key="pi_pick_none").click().run()
    at.text_area(key="pi_tags").input("DEMO.LEVEL.PV").run()
    assert at.button(key="load_pi").label == "Read 1 point from PI"
    at.button(key="pi_pick_all").click().run()
    assert at.button(key="load_pi").label == "Read 4 points from PI"
    at.button(key="load_pi").click().run()
    assert not at.exception and not at.error, (at.exception, at.error)
    assert set(at.session_state["series"]) == {"DEMO.TEMP.PV", "DEMO.FLOW.PV", "DEMO.PRESS.PV", "DEMO.LEVEL.PV"}
    at.button(key="run").click().run()  # tuning and every tab must render with the PI panel showing
    assert not at.exception, at.exception
    assert any("Proposed changes" in m.value for m in at.markdown)


def test_server_dropdown_lists_admin_servers_and_main_page_has_no_connection_fields(monkeypatch):
    from pi_compression_tuner.settings import PISettings
    from pi_compression_tuner.sources import connect as connect_mod
    fake = FakePI()
    calls = []
    monkeypatch.setattr(connect_mod, "connect", lambda server, **k: calls.append(server) or connect_mod.Connection(fake, "webapi"))
    _save_servers(PISettings(server="PISRV", url="https://pi/piwebapi", name="Plant"),
                  PISettings(server="LABSRV", source="afsdk"), max_results=2)
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    box = at.selectbox(key="pi_server_label")
    assert box.options == ["Plant", "LABSRV"] and box.value == "Plant"
    keys = {w.key for w in at.sidebar.text_input}
    assert not keys & {"pi_server", "pi_url", "pi_ca_bundle", "pi_afsdk_dir"}
    box.set_value("LABSRV").run()
    at.text_input(key="pi_search").input("DEMO")
    at.button(key="pi_search_go").click().run()
    assert calls == ["LABSRV"]
    assert any("search limit" in c.value for c in at.caption)  # 2 of 4 demo points: the limit was hit
    assert at.warning and "stopped at 2 points" in at.warning[0].value


def test_admin_page_renders_and_saves_search_limit():
    from pi_compression_tuner.settings import load_app_settings
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.button(key="admin_open").click().run()
    assert not at.exception, at.exception
    assert any("Point templates" in m.value for m in at.markdown)
    at.number_input(key="admin_max_results").set_value(5000).run()
    at.button(key="admin_save_search").click().run()
    assert not at.exception and not at.error, (at.exception, at.error)
    saved = load_app_settings()
    assert saved.max_results == 5000 and len(saved.templates) > 10
    at.text_input(key="try_desc").input("Reactor outlet temperature")
    at.text_input(key="try_units").input("deg F").run()
    assert at.success and "Temperature, °F" in at.success[0].value
    at.button(key="admin_back").click().run()
    assert at.radio(key="origin")


def test_demo_points_match_templates():
    at = _loaded_and_run()
    results = {r.tag: r for r in at.session_state["results"]}
    assert results["DEMO.TEMP.PV"].template == "Temperature, °F"
    assert results["DEMO.LEVEL.PV"].template == "Level"
    assert any("Matched the" in n for n in results["DEMO.FLOW.PV"].notes)


def test_point_detail_navigation_and_zoom():
    at = _loaded_and_run()
    first = at.selectbox(key="detail_tag").value
    at.button(key="next_pt").click().run()
    assert at.selectbox(key="detail_tag").value != first
    tag = at.selectbox(key="detail_tag").value
    assert any("Archive events per day" in m.value for m in at.markdown)
    at.button_group(key=f"view_{tag}").set_value("Largest miss").run()
    assert not at.exception, at.exception


def test_points_with_too_few_events_render(monkeypatch):
    from pi_compression_tuner.models import Series
    from pi_compression_tuner.sources import connect as connect_mod

    class FewPI(FakePI):
        def recorded(self, ref, start, end):
            s = self.series[ref.name]
            return Series(s.t[:10], s.v[:10]) if ref.name != "DEMO.FLOW.PV" else Series([], [])

    fake = FewPI()
    monkeypatch.setattr(connect_mod, "connect", lambda *a, **k: connect_mod.Connection(fake, "afsdk"))
    from pi_compression_tuner.settings import PISettings
    _save_servers(PISettings(server="PISRV", source="afsdk"))
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    at.text_input(key="pi_search").input("DEMO")
    at.button(key="pi_search_go").click().run()
    at.button(key="pi_pick_all").click().run()
    at.button(key="load_pi").click().run()
    assert at.warning and "1 of 4 points came back with no numeric values" in at.warning[0].value
    at.button(key="run").click().run()
    assert not at.exception, at.exception
    for tag in ("DEMO.TEMP.PV", "DEMO.FLOW.PV"):
        at.selectbox(key="detail_tag").set_value(tag).run()
        assert not at.exception, at.exception


def test_search_by_units_and_description(monkeypatch):
    from pi_compression_tuner.settings import PISettings
    from pi_compression_tuner.sources import connect as connect_mod
    fake = FakePI()
    monkeypatch.setattr(connect_mod, "connect", lambda *a, **k: connect_mod.Connection(fake, "webapi"))
    _save_servers(PISettings(server="PISRV", url="https://pi/piwebapi"))
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    at.text_input(key="pi_find_units").input("PSIG")
    at.text_input(key="pi_find_source").input("OPC")
    at.button(key="pi_search_go").click().run()
    assert fake.filters == ["*"]  # no tag mask: every name, filtered on the rest
    assert fake.wheres[-1].pointsource == "OPC"
    assert list(at.session_state["pi_found"]) == ["DEMO.PRESS.PV"]
    at.text_input(key="pi_find_units").input("")
    at.text_input(key="pi_find_source").input("")
    at.text_input(key="pi_find_desc").input("level")
    at.button(key="pi_search_go").click().run()
    assert list(at.session_state["pi_found"]) == ["DEMO.LEVEL.PV"]
