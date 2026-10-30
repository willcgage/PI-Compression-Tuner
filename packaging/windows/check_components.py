"""Part of the self-test: check the install is complete, then import each component and report what works.

``--install-only`` stops after the install check (the UI launcher runs that).
"""

import sys
from pathlib import Path

failed = False


def check(label, test, optional_note=""):
    global failed
    try:
        detail = test()
        print(f"  {label}: OK{f' ({detail})' if detail else ''}")
    except Exception as e:
        if optional_note:
            print(f"  {label}: not found on this machine ({e}). {optional_note}")
        else:
            print(f"  {label}: FAILED ({type(e).__name__}: {e})")
            failed = True


def core():
    import numpy, pandas, plotly, requests, streamlit  # noqa: F401, E401
    return f"Python {sys.version.split()[0]}, streamlit {streamlit.__version__}"


def sspi():
    import requests_negotiate_sspi  # noqa: F401


def dotnet():
    import clr  # noqa: F401
    import System
    return f".NET {System.Environment.Version}"


def afsdk():
    from pi_compression_tuner.sources.afsdk import load_afsdk
    load_afsdk()


def install():
    """Every file the build shipped is here, at its shipped size."""
    root = Path(__file__).resolve().parent
    missing, changed = [], []
    for line in (root / "python" / "MANIFEST.txt").read_text(encoding="utf-8").splitlines():
        size, rel = line.split("\t", 1)
        p = root / rel
        if not p.is_file():
            missing.append(rel)
        elif p.stat().st_size != int(size):
            changed.append(rel)
    if missing or changed:
        shown = ", ".join((missing + changed)[:3])
        raise RuntimeError(f"{len(missing)} files missing and {len(changed)} files differ from this build, e.g. {shown}. "
                           "Unzip the bundle again into a new, empty folder")
    build = (root / "BUILD.txt").read_text(encoding="utf-8").strip()
    return f"{build}, all files present"


check("install", install)
if "--install-only" in sys.argv:
    sys.exit(1 if failed else 0)
check("core and UI", core)
check("Windows sign-in for the PI Web API", sspi)
check("pythonnet", dotnet)
check("AF SDK", afsdk, "That is fine if you use the PI Web API.")
sys.exit(1 if failed else 0)
