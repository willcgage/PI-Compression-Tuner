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
