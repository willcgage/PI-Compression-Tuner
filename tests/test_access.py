from pi_compression_tuner.access import (ADMIN, USER, AccessSettings, CookieSigner, Grant, decide, log_activity,
                                         read_activity, split_names)
from pi_compression_tuner.settings import AppSettings, load_app_settings, save_app_settings


def groups(*names):
    return lambda name: name in names


def test_admins_come_first_and_users_second():
    access = AccessSettings(admins=["BUILTIN\\Administrators", "CORP\\PI Admins"], users=["CORP\\PI Engineers"])
    assert decide("CORP\\ann", access, groups("CORP\\PI Admins")) == Grant("CORP\\ann", ADMIN, "CORP\\PI Admins")
    assert decide("CORP\\bob", access, groups("CORP\\PI Engineers")).role == USER
    assert decide("CORP\\eve", access, groups("CORP\\Finance")) is None


def test_an_account_named_in_a_list_matches_in_any_case_without_asking_windows():
    asked = []
    access = AccessSettings(admins=["corp\\ANN"], users=[])
    grant = decide("CORP\\ann", access, lambda name: asked.append(name) or False)
    assert grant.role == ADMIN and asked == []


def test_a_group_windows_cannot_check_counts_as_no():
    def broken(name):
        raise OSError("no such group")
    assert decide("CORP\\ann", AccessSettings(admins=["CORP\\Gone"], users=["CORP\\Gone too"]), broken) is None


def test_default_admins_are_local_administrators_and_nobody_else():
    access = AccessSettings()
    assert access.admins == ["BUILTIN\\Administrators"] and access.users == []
    assert decide("CORP\\ann", access, groups()) is None


def test_access_lists_round_trip_through_the_settings_file(tmp_path):
    path = tmp_path / "pict.toml"
    app = AppSettings()
    app.access = AccessSettings(admins=["CORP\\PI Admins"], users=["CORP\\PI Engineers", "CORP\\jdoe"])
    save_app_settings(app, path)
    assert load_app_settings(path).access == app.access
    assert 'admins = ["CORP\\\\PI Admins"]' in path.read_text()


def test_a_file_without_access_gets_the_default(tmp_path):
    path = tmp_path / "pict.toml"
    path.write_text('default_server = ""\n')
    assert load_app_settings(path).access == AccessSettings()


def test_split_names_accepts_lines_commas_and_semicolons_and_drops_repeats():
    assert split_names("CORP\\a; CORP\\b\nCORP\\A,  ,CORP\\c") == ["CORP\\a", "CORP\\b", "CORP\\c"]


def test_cookie_round_trip_tamper_and_expiry():
    signer = CookieSigner(key=b"k" * 32, hours=1)
    grant = Grant("CORP\\jösé", ADMIN, "CORP\\PI Admins")
    value = signer.make(grant, now=1000)
    assert signer.read(value, now=1001) == grant
    assert signer.read(value, now=1000 + 3601) is None
    body, mac = value.split(".")
    forged = CookieSigner(key=b"x" * 32).make(Grant("CORP\\eve", ADMIN, "x"), now=1000)
    assert signer.read(forged.split(".")[0] + "." + mac, now=1001) is None
    assert signer.read("garbage", now=1001) is None
    assert CookieSigner().read(value, now=1001) is None   # a restarted server signs everyone out


def test_activity_log_newest_first(tmp_path):
    path = tmp_path / "log.txt"
    log_activity("CORP\\ann", "signed in", "admin", path=path)
    log_activity("CORP\\bob", "settings saved", "tabs\tand\nlines", path=path)
    rows = read_activity(path=path)
    assert [r[1] for r in rows] == ["CORP\\bob", "CORP\\ann"]
    assert rows[0][2:] == ("settings saved", "tabs and lines")
