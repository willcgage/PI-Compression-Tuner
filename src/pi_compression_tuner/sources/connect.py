"""Pick a PI source: the PI Web API first, with AF SDK as the fallback."""

from __future__ import annotations

from dataclasses import dataclass

from .base import PISource


@dataclass
class Connection:
    source: PISource
    kind: str           # "webapi" or "afsdk"
    note: str = ""      # why the fallback was used, if it was


def connect(server: str, source: str = "auto", url: str | None = None, auth: str = "kerberos",
            verify: bool | str = True, afsdk_dir: str | None = None) -> Connection:
    """Connect to ``server`` through the requested source.

    ``source="auto"`` tries the PI Web API (when ``url`` is given) and falls back to AF SDK
    if the Web API can't be reached, refuses the credentials, or can't find the server.
    """
    if source not in ("auto", "webapi", "afsdk"):
        raise ValueError("source must be auto, webapi or afsdk")
    web_error = None
    if source in ("auto", "webapi"):
        if not url:
            if source == "webapi":
                raise ValueError("the PI Web API needs --url")
            web_error = "no PI Web API URL given"
        else:
            from .piwebapi import PIWebAPI
            try:
                api = PIWebAPI(url, auth=auth, verify=verify)
                api.check(server)
                return Connection(api, "webapi")
            except Exception as e:  # network, TLS, auth or lookup failure
                if source == "webapi":
                    raise
                web_error = f"PI Web API unavailable ({type(e).__name__}: {e})"
    from .afsdk import AFSDK
    try:
        sdk = AFSDK(afsdk_dir)
        sdk.check(server)
    except Exception as e:
        if web_error:
            raise ConnectionError(f"{web_error}; AF SDK fallback failed too ({type(e).__name__}: {e})") from e
        raise
    return Connection(sdk, "afsdk", f"Using AF SDK: {web_error}." if web_error else "")
