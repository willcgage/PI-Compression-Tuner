"""Run the shared tuner server (``pict serve``) as a Windows service.

The Windows bundle ships WinSW, a small open-source service host, as ``service\\pict-service.exe``.
``pict service install`` writes its configuration next to it and registers the service, which then
runs the bundle's own Python with ``-m pi_compression_tuner serve``. The service:

* starts with Windows and restarts itself if it stops unexpectedly;
* keeps its settings, activity log and service logs in ``%ProgramData%\\PI Compression Tuner``,
  a folder only administrators and the service account may change (the access lists live there);
* runs as ``NT AUTHORITY\\NetworkService`` unless told otherwise. On the network that is the server's
  computer account (DOMAIN\\HOST$): map it, or the account you choose, to a read-only PI identity.

Everything here needs an elevated (Run as administrator) prompt.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape, quoteattr

SERVICE_ID = "PICompressionTuner"
DISPLAY_NAME = "PI Compression Tuner"
DESCRIPTION = ("Shared PI Compression Tuner web UI with Windows sign-in. Reads PI and recommends exception and "
               "compression settings; writes nothing to PI.")
HOST_EXE = "pict-service.exe"
FIREWALL_RULE = "PI Compression Tuner"
DEFAULT_ACCOUNT = "NT AUTHORITY\\NetworkService"
BUILTIN_ACCOUNTS = {"localsystem": "LocalSystem", "nt authority\\system": "LocalSystem",
                    "nt authority\\networkservice": "NT AUTHORITY\\NetworkService",
                    "nt authority\\localservice": "NT AUTHORITY\\LocalService"}
PASSWORD_ENV = "PICT_SERVICE_PASSWORD"


@dataclass
class ServiceOptions:
    port: int = 8501
    host: str = "0.0.0.0"
    account: str = DEFAULT_ACCOUNT
    cert: str = ""
    key: str = ""
    sign_in: bool = True


def data_dir() -> Path:
    """The service's settings, activity log and logs: %ProgramData%\\PI Compression Tuner."""
    return Path(os.environ.get("ProgramData") or r"C:\ProgramData") / "PI Compression Tuner"


def install_dir() -> Path:
    """The bundle folder: the parent of the bundled Python's folder (``<bundle>\\python\\python.exe``)."""
    return Path(sys.executable).resolve().parent.parent


def host_exe(root: Path | None = None) -> Path:
    return (root or install_dir()) / "service" / HOST_EXE


def normalize_account(account: str) -> str:
    account = (account or DEFAULT_ACCOUNT).strip()
    return BUILTIN_ACCOUNTS.get(account.lower(), account)


def needs_password(account: str) -> bool:
    """A named domain or local account does; built-in accounts and group managed service accounts (ending in $) don't."""
    account = normalize_account(account)
    return account not in BUILTIN_ACCOUNTS.values() and not account.endswith("$")


def serve_arguments(opts: ServiceOptions) -> list[str]:
    args = ["-s", "-m", "pi_compression_tuner", "serve", "--host", opts.host, "--port", str(opts.port)]
    if opts.cert:
        args += ["--cert", opts.cert, "--key", opts.key]
    if not opts.sign_in:
        args.append("--no-sign-in")
    return args


def _quote_arg(a: str) -> str:
    return f'"{a}"' if (not a or any(c in a for c in ' \t"')) else a


def service_xml(opts: ServiceOptions, python: Path, data: Path) -> str:
    """WinSW (2.x) configuration for the service."""
    env = {"PICT_CONFIG": str(data / "pict.toml"), "PICT_WORK": str(data / "work"), "PYTHONNOUSERSITE": "1",
           "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    args = " ".join(_quote_arg(a) for a in serve_arguments(opts))
    lines = [
        "<service>",
        f"  <id>{SERVICE_ID}</id>",
        f"  <name>{escape(DISPLAY_NAME)}</name>",
        f"  <description>{escape(DESCRIPTION)}</description>",
        f"  <executable>{escape(str(python))}</executable>",
        f"  <arguments>{escape(args)}</arguments>",
        f"  <workingdirectory>{escape(str(data))}</workingdirectory>",
        *[f"  <env name={quoteattr(k)} value={quoteattr(v)} />" for k, v in env.items()],
        "  <startmode>Automatic</startmode>",
        '  <onfailure action="restart" delay="10 sec" />',
        '  <onfailure action="restart" delay="60 sec" />',
        "  <resetfailure>1 hour</resetfailure>",
        "  <stoptimeout>20 sec</stoptimeout>",
        f"  <logpath>{escape(str(data / 'logs'))}</logpath>",
        '  <log mode="roll-by-size">',
        "    <sizeThreshold>10240</sizeThreshold>",
        "    <keepFiles>8</keepFiles>",
        "  </log>",
        "</service>",
    ]
    return "\n".join(lines) + "\n"


def folder_acl_commands(folder: Path, account: str, writable: bool) -> list[list[str]]:
    """icacls commands so only SYSTEM and Administrators may change ``folder``, and ``account`` may read it
    (and change it, if ``writable``). Ordinary users may read the bundle folder, not the data folder."""
    account = normalize_account(account)
    who = {"LocalSystem": "*S-1-5-18", "NT AUTHORITY\\NetworkService": "*S-1-5-20",
           "NT AUTHORITY\\LocalService": "*S-1-5-19"}.get(account, account)
    grants = ["*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F", f"{who}:(OI)(CI){'M' if writable else 'RX'}"]
    if not writable:
        grants.append("*S-1-5-32-545:(OI)(CI)RX")   # Users may run the bundle's own tools
    # Set on the folder only; files and subfolders inherit it.
    return [["icacls", str(folder), "/inheritance:r", "/grant:r", *grants, "/C", "/Q"]]


def firewall_commands(port: int, add: bool = True) -> list[list[str]]:
    delete = ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"]
    if not add:
        return [delete]
    return [delete, ["netsh", "advfirewall", "firewall", "add", "rule", f"name={FIREWALL_RULE}", "dir=in",
                     "action=allow", "protocol=TCP", f"localport={port}", "profile=domain,private"]]


# --- doing it ---------------------------------------------------------------

Runner = Callable[[list[str]], subprocess.CompletedProcess]


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} {' '.join(cmd[1:2])} failed ({p.returncode}): "
                           f"{(p.stdout + p.stderr).strip()}")
    return p


def is_elevated() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except AttributeError:
        return False


def _require_windows_admin():
    if sys.platform != "win32":
        raise RuntimeError("The Windows service can only be set up on Windows.")
    if not is_elevated():
        raise RuntimeError("Setting up the service needs administrator rights: open the prompt with Run as administrator.")


def _host(root: Path | None = None) -> Path:
    exe = host_exe(root)
    if not exe.exists():
        raise RuntimeError(f"{exe} is missing. The service host ships with the Windows bundle and installer.")
    return exe


def status() -> str:
    """Started, Stopped or NonExistent, as the service host reports it."""
    if sys.platform != "win32":
        return "NonExistent"
    p = _run([str(_host()), "status"], check=False)
    return (p.stdout.strip().splitlines() or ["NonExistent"])[-1].strip()


def _set_account(account: str, password: str):
    """Run the service as ``account`` and let it log on as a service."""
    import win32security
    import win32service
    account = normalize_account(account)
    if account == "LocalSystem":
        return
    scm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    try:
        svc = win32service.OpenService(scm, SERVICE_ID, win32service.SERVICE_ALL_ACCESS)
        try:
            win32service.ChangeServiceConfig(svc, win32service.SERVICE_NO_CHANGE, win32service.SERVICE_NO_CHANGE,
                                             win32service.SERVICE_NO_CHANGE, None, None, 0, None, account,
                                             password or "", None)
        finally:
            win32service.CloseServiceHandle(svc)
    finally:
        win32service.CloseServiceHandle(scm)
    if account in BUILTIN_ACCOUNTS.values():
        return
    sid = win32security.LookupAccountName(None, account)[0]
    policy = win32security.LsaOpenPolicy(None, win32security.POLICY_ALL_ACCESS)
    try:
        win32security.LsaAddAccountRights(policy, sid, ["SeServiceLogonRight"])
    finally:
        win32security.LsaClose(policy)


def seed_settings(data: Path, admins: list[str] | None, say: Callable[[str], None]) -> Path:
    """Create the service's settings file if needed, from the installing admin's own file when there is one,
    and set the admins list if ``admins`` is given."""
    from .settings import load_app_settings, save_app_settings, settings_path
    target = data / "pict.toml"
    if not target.exists():
        mine = settings_path()
        if mine.exists() and mine.resolve() != target.resolve():
            shutil.copyfile(mine, target)
            say(f"Copied your settings from {mine} to {target}.")
    if admins:
        app = load_app_settings(target)
        app.access.admins = admins
        save_app_settings(app, target)
        say(f"Admins: {', '.join(admins)}")
    return target


def install(opts: ServiceOptions, password: str = "", admins: list[str] | None = None, open_firewall: bool = False,
            start: bool = True, say: Callable[[str], None] = print) -> None:
    _require_windows_admin()
    exe = _host()
    root = install_dir()
    data = data_dir()
    account = normalize_account(opts.account)
    if needs_password(account) and not password:
        raise RuntimeError(f"{account} needs its password: set {PASSWORD_ENV}, or answer the prompt.")
    if status() != "NonExistent":
        say("Removing the existing service first.")
        uninstall(say=lambda _: None, keep_firewall=True)
    for sub in ("", "logs", "work"):
        (data / sub).mkdir(parents=True, exist_ok=True)
    seed_settings(data, admins, say)
    exe.with_suffix(".xml").write_text(service_xml(opts, root / "python" / "python.exe", data), encoding="utf-8")
    _run([str(exe), "install"])
    _set_account(account, password)
    for cmd in folder_acl_commands(data, account, writable=True) + folder_acl_commands(root, account, writable=False):
        _run(cmd)
    say(f"Locked {data} and {root} so that only administrators (and the service, for its data) can change them.")
    if open_firewall:
        for cmd in firewall_commands(opts.port):
            _run(cmd, check=cmd[3] != "delete")
        say(f"Opened TCP port {opts.port} in Windows Firewall (domain and private networks).")
    say(f"Installed the {DISPLAY_NAME} service, running as {account}.")
    if account not in BUILTIN_ACCOUNTS.values():
        host = os.environ.get("COMPUTERNAME", "this-server")
        say(f"For Kerberos sign-in, register the web address to this account once (a domain admin can):\n"
            f"  setspn -S HTTP/{host} {account}\n  setspn -S HTTP/<full DNS name of {host}> {account}")
    if start:
        _run([str(exe), "start"])
        say(f"Started. Open {'https' if opts.cert else 'http'}://{os.environ.get('COMPUTERNAME', 'localhost')}:{opts.port}/")


def uninstall(say: Callable[[str], None] = print, keep_firewall: bool = False) -> None:
    _require_windows_admin()
    exe = _host()
    if status() == "NonExistent":
        say("The service is not installed.")
    else:
        _run([str(exe), "stop"], check=False)
        _run([str(exe), "uninstall"])
        say(f"Removed the {DISPLAY_NAME} service. Its settings and logs stay in {data_dir()}.")
    if not keep_firewall:
        for cmd in firewall_commands(0, add=False):
            _run(cmd, check=False)


def control(action: str, say: Callable[[str], None] = print) -> None:
    _require_windows_admin()
    p = _run([str(_host()), action], check=False)
    if p.returncode != 0:
        raise RuntimeError((p.stdout + p.stderr).strip() or f"{action} failed")
    say(f"{action.capitalize()}: {status()}")
