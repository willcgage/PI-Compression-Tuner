"""Build a self-contained Windows bundle of the tuner for machines on the PI network.

The bundle holds a private copy of Python (the python.org build published on NuGet),
the tuner and every dependency, so the target machine needs no internet access,
no installer and no admin rights. It runs on Linux or Windows:

    pip install uv build
    python packaging/build_windows_bundle.py            # writes dist/PI-Compression-Tuner-<version>-win64.zip

Dependencies are resolved for Windows x64 by uv, so Windows-only packages
(pythonnet for AF SDK, requests-negotiate-sspi for Kerberos sign-in) are included
even when building on Linux.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent
PYTHON_VERSION = "3.15.2"    # NuGet "python" package: the python.org Windows x64 build
EXTRAS = ("ui", "kerberos", "afsdk")
NUGET = "https://api.nuget.org/v3-flatcontainer/python/{v}/python.{v}.nupkg"


def run(*cmd: str | Path) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], check=True)


def fetch_python(cache: Path, dest: Path) -> None:
    """Unpack the NuGet python package's tools/ folder (python.exe, the stdlib, the VC runtime) into ``dest``."""
    pkg = cache / f"python.{PYTHON_VERSION}.nupkg"
    if not pkg.exists():
        cache.mkdir(parents=True, exist_ok=True)
        url = NUGET.format(v=PYTHON_VERSION)
        print(f"Downloading {url}", flush=True)
        with urllib.request.urlopen(url) as r:
            pkg.write_bytes(r.read())
    with zipfile.ZipFile(pkg) as z:
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith("tools/"):
                continue
            out = dest / info.filename[len("tools/"):]
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(info))
    if not (dest / "python.exe").exists():
        raise SystemExit(f"python.exe missing from {pkg}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=ROOT / "dist", help="Output folder (default: dist/).")
    ap.add_argument("--cache", type=Path, default=ROOT / "build" / "cache", help="Download cache.")
    args = ap.parse_args()

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    name = f"PI-Compression-Tuner-{version}-win64"
    work = ROOT / "build" / "bundle"
    bundle = work / name
    shutil.rmtree(work, ignore_errors=True)
    bundle.mkdir(parents=True)
    py_major_minor = ".".join(PYTHON_VERSION.split(".")[:2])

    fetch_python(args.cache, bundle / "python")

    # The tuner as a wheel, then its dependencies pinned for Windows x64.
    wheels = work / "wheels"
    run(sys.executable, "-m", "build", "--wheel", "--outdir", wheels, ROOT)
    wheel = next(wheels.glob("*.whl"))
    lock = work / "requirements-win64.txt"
    run("uv", "pip", "compile", ROOT / "pyproject.toml", *[a for e in EXTRAS for a in ("--extra", e)],
        "--python-platform", "x86_64-pc-windows-msvc", "--python-version", py_major_minor,
        "--no-header", "--no-annotate", "-o", lock)
    site = bundle / "python" / "Lib" / "site-packages"
    common = ("--target", site, "--python-platform", "x86_64-pc-windows-msvc", "--python-version", py_major_minor,
              "--only-binary", ":all:", "--link-mode", "copy")
    run("uv", "pip", "install", *common, "-r", lock)
    run("uv", "pip", "install", *common, "--no-deps", wheel)
    shutil.rmtree(site / "bin", ignore_errors=True)   # Linux console scripts; the .cmd launchers replace them

    # Launchers and the guide. .cmd files need CRLF line endings.
    for f in sorted((HERE / "windows").iterdir()):
        text = f.read_text(encoding="utf-8").replace("\r\n", "\n")
        newline = "\r\n" if f.suffix in (".cmd", ".txt") else "\n"
        (bundle / f.name).write_bytes(text.replace("\n", newline).encode("utf-8"))
    shutil.copy(lock, bundle / "python" / "requirements-win64.txt")

    # Build stamp and a manifest of every file, so the self-test can tell a complete,
    # current install from a partial extract or a copy mixed with an older bundle.
    try:
        commit = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        commit = "unknown"
    stamp = f"{version} build {commit} {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC"
    (bundle / "BUILD.txt").write_text(stamp + "\r\n", encoding="utf-8")
    files = sorted(p for p in bundle.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
    manifest = [f"{p.stat().st_size}\t{p.relative_to(bundle).as_posix()}" for p in files]
    (bundle / "python" / "MANIFEST.txt").write_text("\n".join(manifest) + "\n", encoding="utf-8")

    args.out.mkdir(parents=True, exist_ok=True)
    zpath = args.out / f"{name}.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(bundle.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts:
                z.write(p, p.relative_to(work).as_posix())
    digest = hashlib.sha256(zpath.read_bytes()).hexdigest()
    (args.out / f"{name}.zip.sha256").write_text(f"{digest}  {zpath.name}\n")
    longest = max((len(p.relative_to(work).as_posix()) for p in bundle.rglob("*")), default=0)
    print(f"Wrote {zpath} ({zpath.stat().st_size / 1e6:.0f} MB), sha256 {digest}; longest path inside {longest} characters.")


if __name__ == "__main__":
    main()
