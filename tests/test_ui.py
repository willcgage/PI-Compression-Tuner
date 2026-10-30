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
    at.text_input(key="pi_filter").input("*").run()
    at.button(key="load_pi").click().run()
    assert not at.exception
    assert at.error and "Could not read from PI" in at.error[0].value


def test_point_detail_navigation_and_zoom():
    at = _loaded_and_run()
    first = at.selectbox(key="detail_tag").value
    at.button(key="next_pt").click().run()
    assert at.selectbox(key="detail_tag").value != first
    tag = at.selectbox(key="detail_tag").value
    assert any("Archive events per day" in m.value for m in at.markdown)
    at.button_group(key=f"view_{tag}").set_value("Largest miss").run()
    assert not at.exception, at.exception


def test_key_moments_suggest_and_retune():
    at = _loaded_and_run()
    at.selectbox(key="detail_tag").set_value("DEMO.LEVEL.PV").run()
    at.button(key="km_suggest_DEMO.LEVEL.PV").click().run()
    assert not at.exception, at.exception
    assert any("key moments." in m.value and "Retune" in m.value for m in at.markdown)
    at.button(key="km_retune_DEMO.LEVEL.PV").click().run()
    assert not at.exception, at.exception
    assert any("The proposal keeps all" in m.value for m in at.markdown)


def test_alarm_limits_feed_suggest():
    at = _loaded_and_run()
    at.selectbox(key="detail_tag").set_value("DEMO.TEMP.PV").run()
    lim = [w for w in at.text_input if w.key.startswith("lim_DEMO.TEMP.PV")][0]
    assert lim.value == "54, 66"
    at.button(key="km_suggest_DEMO.TEMP.PV").click().run()
    assert not at.exception, at.exception
    picked = [m for m in at.multiselect if m.key.startswith("km_DEMO.TEMP.PV")][0]
    from pi_compression_tuner.moments import alarm_crossings
    from pi_compression_tuner.metrics import estimate_noise
    series = at.session_state.series["DEMO.TEMP.PV"]
    crossings = alarm_crossings(series, (54.0, 66.0), estimate_noise(series.v))
    assert len(crossings) and set(crossings.tolist()) <= set(picked.value)
