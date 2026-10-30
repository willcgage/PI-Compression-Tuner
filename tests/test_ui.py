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


def test_pi_errors_are_shown_not_raised():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    at.text_input(key="pi_server").input("NOSUCHSERVER").run()
    at.selectbox(key="pi_source").set_value("webapi").run()
    at.text_input(key="pi_url").input("https://127.0.0.1:9/piwebapi").run()
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

    def find_points(self, server, name_filter="*", max_count=1000):
        from fnmatch import fnmatch

        from pi_compression_tuner.sources.base import PointRef
        self.filters.append(name_filter)
        return [PointRef(t, descriptor=f"{t} description") for t in self.series if fnmatch(t, name_filter)][:max_count]

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
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    at.text_input(key="pi_server").input("PISRV").run()
    at.text_input(key="pi_url").input("https://pi/piwebapi").run()
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


def test_pi_fields_start_from_saved_settings(tmp_path, monkeypatch):
    from pi_compression_tuner.settings import PISettings, save_settings
    save_settings(PISettings(server="PISRV", url="https://pi/piwebapi", auth="anonymous"))
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    assert at.text_input(key="pi_server").value == "PISRV"
    assert at.selectbox(key="pi_auth").value == "anonymous"
    at.text_input(key="pi_server").input("OTHER").run()
    at.button(key="pi_save").click().run()
    from pi_compression_tuner.settings import load_settings
    assert load_settings().server == "OTHER"


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
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    at.text_input(key="pi_server").input("PISRV").run()
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
