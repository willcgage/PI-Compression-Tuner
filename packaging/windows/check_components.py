"""Part of the self-test: import each component of the bundle and report what works."""

import sys

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


check("core and UI", core)
check("Windows sign-in for the PI Web API", sspi)
check("pythonnet", dotnet)
check("AF SDK", afsdk, "That is fine if you use the PI Web API.")
sys.exit(1 if failed else 0)
