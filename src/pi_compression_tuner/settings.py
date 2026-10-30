"""Admin settings: the PI servers to connect to, search limits and the point templates.

The settings live in a small TOML file, edited from the UI's Admin page or by hand:

    default_server = "Plant historian"
    max_results = 1000          # most points a search returns

    [[server]]
    name = "Plant historian"    # the label shown in the server list
    server = "PISRV"            # PI Data Archive name
    url = "https://pihost/piwebapi"
    source = "auto"             # auto, webapi or afsdk
    auth = "anonymous"          # kerberos, basic or anonymous
    verify = true               # check the PI Web API's TLS certificate
    ca_bundle = ""              # CA certificate (PEM) for the PI Web API, if not in the system store
    afsdk_dir = ""              # folder holding OSIsoft.AFSDK.dll, if not the standard one

    [[template]]
    name = "Temperature, °F"
    match_units = ["degF", "F"]
    compdev = 0.5
    ...

A file from an earlier version with a single ``[pi]`` table is read as one server.
Until templates are saved, the starter list in :mod:`.templates` is used.

The file is found at ``PICT_CONFIG`` if set, else ``pict.toml`` in the current folder if there is
one, else ``pict.toml`` in the user's settings folder (``%APPDATA%\\pict`` on Windows,
``~/.config/pict`` elsewhere). No passwords are stored: basic sign-in reads them from the environment.
"""

from __future__ import annotations

import json
import math
import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .templates import Template, starter_templates

DEFAULT_MAX_RESULTS = 1000
MAX_RESULTS_LIMIT = 100000


@dataclass
class PISettings:
    server: str = ""
    url: str = ""
    source: str = "auto"
    auth: str = "kerberos"
    verify: bool = True
    ca_bundle: str = ""
    afsdk_dir: str = ""
    name: str = ""          # label in the server list; the Data Archive name if empty

    def tls_verify(self) -> bool | str:
        """The ``verify`` value for requests: False, a CA bundle path, or True."""
        return False if not self.verify else (self.ca_bundle or True)

    @property
    def label(self) -> str:
        return self.name or self.server


@dataclass
class AppSettings:
    servers: list[PISettings] = field(default_factory=list)
    default_server: str = ""
    max_results: int = DEFAULT_MAX_RESULTS
    templates: list[Template] = field(default_factory=starter_templates)

    def server(self, label: str | None = None) -> PISettings:
        """The server with this label, else the default one, else the first, else empty settings."""
        for want in (label, self.default_server):
            for s in self.servers:
                if want and s.label == want:
                    return s
        return self.servers[0] if self.servers else PISettings()

    def labels(self) -> list[str]:
        return [s.label for s in self.servers]


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


def _server_from(d: dict) -> PISettings:
    known = {f.name for f in fields(PISettings)}
    return PISettings(**{k: v for k, v in d.items() if k in known})


def load_app_settings(path: Path | None = None) -> AppSettings:
    path = path or settings_path()
    if not path.exists():
        return AppSettings()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path} is not valid TOML: {e}") from e
    servers = [_server_from(d) for d in data.get("server", [])]
    if not servers and data.get("pi", {}).get("server"):
        servers = [_server_from(data["pi"])]
    out = AppSettings(servers=servers, default_server=str(data.get("default_server", "")))
    try:
        out.max_results = min(max(int(data.get("max_results", DEFAULT_MAX_RESULTS)), 1), MAX_RESULTS_LIMIT)
    except (TypeError, ValueError):
        pass
    if "template" in data or data.get("templates_saved"):
        out.templates = [Template.from_dict(d) for d in data.get("template", [])]
    return out


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            raise ValueError("settings values must be finite numbers")
        return repr(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_toml_value(x) for x in v) + "]"
    return json.dumps(str(v))


def _table(header: str, d: dict) -> list[str]:
    lines = ["", header]
    lines += [f"{k} = {_toml_value(v)}" for k, v in d.items() if v is not None]
    return lines


def save_app_settings(settings: AppSettings, path: Path | None = None) -> Path:
    path = path or settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# PI Compression Tuner admin settings. No passwords are stored here.",
             f"default_server = {_toml_value(settings.default_server)}",
             f"max_results = {int(settings.max_results)}",
             "templates_saved = true"]
    for s in settings.servers:
        lines += _table("[[server]]", asdict(s))
    for t in settings.templates:
        lines += _table("[[template]]", t.to_dict())
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def load_settings(path: Path | None = None) -> PISettings:
    """The default PI server's connection settings."""
    return load_app_settings(path).server()


def save_settings(settings: PISettings, path: Path | None = None) -> Path:
    """Save one server's settings as the default server, keeping the other servers and the templates."""
    app = load_app_settings(path)
    current = app.server() if app.servers else None
    if current is not None and (current.label == settings.label or not settings.name):
        settings.name = settings.name or current.name
        app.servers[app.servers.index(current)] = settings
    else:
        app.servers = [s for s in app.servers if s.label != settings.label] + [settings]
    app.default_server = settings.label
    return save_app_settings(app, path)
