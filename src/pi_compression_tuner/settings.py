"""Saved PI connection settings, so the server, URL and sign-in are entered once.

The settings live in a small TOML file:

    [pi]
    server = "PISRV"
    url = "https://pihost/piwebapi"
    source = "auto"          # auto, webapi or afsdk
    auth = "anonymous"       # kerberos, basic or anonymous
    verify = true            # check the PI Web API's TLS certificate
    ca_bundle = ""           # CA certificate (PEM) for the PI Web API, if not in the system store
    afsdk_dir = ""           # folder holding OSIsoft.AFSDK.dll, if not the standard one

The file is found at ``PICT_CONFIG`` if set, else ``pict.toml`` in the current folder if there is
one, else ``pict.toml`` in the user's settings folder (``%APPDATA%\\pict`` on Windows,
``~/.config/pict`` elsewhere). No passwords are stored: basic sign-in reads them from the environment.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass
class PISettings:
    server: str = ""
    url: str = ""
    source: str = "auto"
    auth: str = "kerberos"
    verify: bool = True
    ca_bundle: str = ""
    afsdk_dir: str = ""

    def tls_verify(self) -> bool | str:
        """The ``verify`` value for requests: False, a CA bundle path, or True."""
        return False if not self.verify else (self.ca_bundle or True)


def settings_path() -> Path:
    if os.environ.get("PICT_CONFIG"):
        return Path(os.environ["PICT_CONFIG"])
    local = Path.cwd() / "pict.toml"
    if local.exists():
        return local
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "pict" / "pict.toml"


def load_settings(path: Path | None = None) -> PISettings:
    path = path or settings_path()
    if not path.exists():
        return PISettings()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8")).get("pi", {})
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path} is not valid TOML: {e}") from e
    known = {f.name for f in fields(PISettings)}
    return PISettings(**{k: v for k, v in data.items() if k in known})


def save_settings(settings: PISettings, path: Path | None = None) -> Path:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# PI Compression Tuner connection settings. No passwords are stored here.", "[pi]"]
    for k, v in asdict(settings).items():
        lines.append(f"{k} = {'true' if v is True else 'false' if v is False else json.dumps(str(v))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
