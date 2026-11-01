import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from pi_compression_tuner import service as svc


def test_service_xml_runs_the_bundled_python_with_serve():
    opts = svc.ServiceOptions(port=8600, cert=r"C:\certs\pict cert.pem", key=r"C:\certs\pict.key")
    xml = svc.service_xml(opts, Path(r"C:\Program Files\PI Compression Tuner\python\python.exe"),
                          Path(r"C:\ProgramData\PI Compression Tuner"))
    root = ET.fromstring(xml)
    assert root.findtext("id") == svc.SERVICE_ID
    assert root.findtext("executable").endswith(r"python\python.exe")
    args = root.findtext("arguments")
    assert args.startswith("-s -m pi_compression_tuner serve --host 0.0.0.0 --port 8600")
    assert r'--cert "C:\certs\pict cert.pem"' in args and "--no-sign-in" not in args
    env = {e.get("name"): e.get("value") for e in root.findall("env")}
    assert env["PICT_CONFIG"].endswith(r"PI Compression Tuner\pict.toml") or env["PICT_CONFIG"].endswith("pict.toml")
    assert root.findtext("startmode") == "Automatic" and len(root.findall("onfailure")) == 2
    assert "<serviceaccount>" not in xml   # passwords never go in the file


@pytest.mark.parametrize("account,needs", [
    ("NT AUTHORITY\\NetworkService", False), ("localsystem", False), ("NT AUTHORITY\\LocalService", False),
    ("CORP\\svc-pict$", False), ("CORP\\svc-pict", True), (".\\pictsvc", True)])
def test_which_accounts_need_a_password(account, needs):
    assert svc.needs_password(account) is needs


def test_data_folder_is_locked_to_admins_and_the_service_account():
    (cmd,) = svc.folder_acl_commands(Path(r"C:\ProgramData\PI Compression Tuner"), "NT AUTHORITY\\NetworkService",
                                     writable=True)
    assert cmd[:3] == ["icacls", r"C:\ProgramData\PI Compression Tuner", "/inheritance:r"]
    assert "*S-1-5-20:(OI)(CI)M" in cmd and "*S-1-5-32-544:(OI)(CI)F" in cmd
    assert not any(g.startswith("*S-1-5-32-545") for g in cmd)   # ordinary users can't edit who may sign in
    (cmd,) = svc.folder_acl_commands(Path(r"C:\PICT"), "CORP\\svc-pict", writable=False)
    assert "CORP\\svc-pict:(OI)(CI)RX" in cmd and "*S-1-5-32-545:(OI)(CI)RX" in cmd


def test_firewall_rule_is_replaced_not_duplicated():
    delete, add = svc.firewall_commands(8501)
    assert delete[3] == "delete" and "localport=8501" in add


def test_seed_settings_copies_the_admins_own_file_and_sets_admins(tmp_path, monkeypatch):
    from pi_compression_tuner.settings import AppSettings, PISettings, load_app_settings, save_app_settings
    mine = tmp_path / "mine.toml"
    save_app_settings(AppSettings(servers=[PISettings(server="PISRV")]), mine)
    monkeypatch.setenv("PICT_CONFIG", str(mine))
    data = tmp_path / "data"
    data.mkdir()
    said = []
    target = svc.seed_settings(data, ["CORP\\PI Admins"], said.append)
    app = load_app_settings(target)
    assert [s.server for s in app.servers] == ["PISRV"] and app.access.admins == ["CORP\\PI Admins"]
    assert any("Copied your settings" in s for s in said)


def test_service_commands_explain_they_need_windows(monkeypatch):
    from click.testing import CliRunner

    from pi_compression_tuner.cli import main
    monkeypatch.setattr(svc.sys, "platform", "linux")
    r = CliRunner().invoke(main, ["service", "install"])
    assert r.exit_code == 1 and "only be set up on Windows" in r.output


def test_serve_without_windows_sign_in_says_so(monkeypatch):
    from click.testing import CliRunner

    from pi_compression_tuner import serve
    from pi_compression_tuner.cli import main
    pytest.importorskip("streamlit")
    monkeypatch.setattr(serve, "windows_sign_in_available", lambda: False)
    r = CliRunner().invoke(main, ["serve"])
    assert r.exit_code == 1 and "Windows sign-in needs Windows" in r.output
