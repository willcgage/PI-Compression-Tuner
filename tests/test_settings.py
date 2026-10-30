from pi_compression_tuner.settings import PISettings, load_settings, save_settings, settings_path


def test_round_trip_with_windows_paths(tmp_path):
    p = tmp_path / "pict.toml"
    s = PISettings(server="PISRV", url="https://pi/piwebapi", auth="anonymous", verify=True,
                   ca_bundle=r"C:\certs\site ca.pem", afsdk_dir=r"C:\Program Files\PIPC\AF\PublicAssemblies\4.0")
    save_settings(s, p)
    assert load_settings(p) == s
    assert s.tls_verify() == r"C:\certs\site ca.pem"
    assert PISettings(verify=False, ca_bundle="x").tls_verify() is False


def test_missing_file_gives_defaults_and_unknown_keys_are_ignored(tmp_path):
    assert load_settings(tmp_path / "none.toml") == PISettings()
    p = tmp_path / "pict.toml"
    p.write_text('[pi]\nserver = "A"\nfuture_key = 1\n')
    assert load_settings(p).server == "A"


def test_path_prefers_env_then_local_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PICT_CONFIG", str(tmp_path / "x.toml"))
    assert settings_path() == tmp_path / "x.toml"
    monkeypatch.delenv("PICT_CONFIG")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pict.toml").write_text("")
    assert settings_path() == tmp_path / "pict.toml"


def test_legacy_single_connection_file_is_read_as_one_server(tmp_path):
    from pi_compression_tuner.settings import load_app_settings
    p = tmp_path / "pict.toml"
    p.write_text('[pi]\nserver = "PISRV"\nurl = "https://pi/piwebapi"\nauth = "anonymous"\n')
    app = load_app_settings(p)
    assert [s.server for s in app.servers] == ["PISRV"] and app.server().auth == "anonymous"
    assert app.max_results == 1000 and app.templates  # starter templates until some are saved


def test_servers_and_templates_round_trip(tmp_path):
    from pi_compression_tuner.settings import AppSettings, load_app_settings, save_app_settings
    from pi_compression_tuner.templates import Template, starter_templates
    p = tmp_path / "pict.toml"
    t = Template("Odd \"quoted\" name", priority=5, match_units=["°F", "degF"], match_descriptor=["a, b"],
                 compdev=0.5, step=True, target_pct=99.0, note="line\nbreak")
    app = AppSettings(servers=[PISettings(server="A", name="Plant"), PISettings(server="B", source="afsdk")],
                      default_server="B", max_results=25000, templates=[t, *starter_templates()[:2]])
    save_app_settings(app, p)
    back = load_app_settings(p)
    assert back == app
    assert back.server().server == "B" and back.server("Plant").server == "A"
    save_app_settings(AppSettings(templates=[]), p)
    assert load_app_settings(p).templates == []  # an emptied list stays empty


def test_save_settings_keeps_other_servers_and_templates(tmp_path):
    from pi_compression_tuner.settings import AppSettings, load_app_settings, save_app_settings
    p = tmp_path / "pict.toml"
    save_app_settings(AppSettings(servers=[PISettings(server="A", name="Plant"), PISettings(server="B")],
                                  default_server="Plant", templates=[]), p)
    save_settings(PISettings(server="A2", url="https://x/piwebapi"), p)  # e.g. `pict pi config --server A2`
    app = load_app_settings(p)
    assert [s.label for s in app.servers] == ["Plant", "B"] and app.server().server == "A2"
    assert app.templates == []
