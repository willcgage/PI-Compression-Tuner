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
    assert at.title[0].value == "PI Compression Tuner"
    assert any(m.label == "Points" and m.value == "4" for m in at.metric)
    assert [t.label for t in at.tabs] == ["Overview", "Workbench", "Export"]
    assert any("PI Builder sheet has" in m.value for m in at.markdown)


def test_probe_value_can_be_adopted():
    at = _loaded_and_run()
    at.selectbox(key="detail_tag").set_value("DEMO.TEMP.PV").run()
    at.number_input(key="cd_DEMO.TEMP.PV").set_value(0.1).run()
    at.button(key="adopt_DEMO.TEMP.PV").click().run()
    assert not at.exception, at.exception
    assert any("CompDev adopted in the workbench" in c.value for c in at.caption)


def test_pi_errors_are_shown_not_raised():
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.radio(key="origin").set_value("PI server").run()
    at.text_input(key="pi_server").input("NOSUCHSERVER").run()
    at.selectbox(key="pi_source").set_value("webapi").run()
    at.text_input(key="pi_url").input("https://127.0.0.1:9/piwebapi").run()
    at.text_input(key="pi_filter").input("*").run()
    at.button(key="load_pi").click().run()
    assert not at.exception
    assert at.error and "Could not read from PI" in at.error[0].value
