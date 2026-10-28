from pi_compression_tuner.demo import demo_data
from pi_compression_tuner.report import write_report
from pi_compression_tuner.tune import TuneOptions, tune_point


def test_report_has_a_linked_page_per_point(tmp_path):
    series, configs = demo_data(1)
    opts = TuneOptions(input_mode="raw")
    results = [tune_point(t, s, configs[t], opts) for t, s in series.items()]
    path = tmp_path / "report.html"
    write_report(results, series, opts, path, selected=set())
    doc = path.read_text()
    for r in results:
        anchor = "pt-" + r.tag.replace(".", "-")
        assert f'href="#{anchor}"' in doc and f'id="{anchor}"' in doc
    assert "Largest miss" in doc and "pct-attrs" in doc and "Storing noise." in doc
    assert "PI Builder sheet: <b>selected</b>" not in doc  # nothing selected
    assert "100.0% fewer" not in doc
