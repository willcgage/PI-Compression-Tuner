@echo off
rem Runs the tuner's command line with the bundled Python. Usage: pict --help
setlocal
set "PYTHONNOUSERSITE=1"
set "PYTHONPATH="
set "PYTHONHOME="
set "PYTHONUTF8=1"
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
"%~dp0python\python.exe" -m pi_compression_tuner %*
exit /b %ERRORLEVEL%
