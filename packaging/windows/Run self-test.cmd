@echo off
rem Checks the bundle works on this machine without touching PI:
rem the Python runtime, each optional component, and a full analysis of demo data.
rem Set PICT_QUIET=1 to skip opening the report and the closing pause.
title PI Compression Tuner self-test
call "%~dp0work-folder.cmd"
set "FAILED="
echo [1/4] Version
call "%~dp0pict.cmd" --version || set "FAILED=1"
echo.
echo [2/4] Components
"%~dp0python\python.exe" -s -c "import numpy, pandas, requests, streamlit, plotly; print('  core and UI: OK')" || set "FAILED=1"
"%~dp0python\python.exe" -s -c "import requests_negotiate_sspi; print('  Windows sign-in for the PI Web API: OK')" || set "FAILED=1"
"%~dp0python\python.exe" -s -c "import clr, System; print('  pythonnet (.NET', System.Environment.Version, '): OK')" || set "FAILED=1"
"%~dp0python\python.exe" -s -c "from pi_compression_tuner.sources.afsdk import load_afsdk; load_afsdk(); print('  AF SDK: OK')" || echo   AF SDK: not found on this machine. That is fine if you use the PI Web API.
echo.
echo [3/4] Demo analysis (synthetic data, no PI)
call "%~dp0pict.cmd" demo selftest\demo --days 1 || set "FAILED=1"
call "%~dp0pict.cmd" analyze selftest\demo\data.csv --config selftest\demo\config.csv --mode raw --out selftest\out || set "FAILED=1"
echo.
echo [4/4] Result
if defined FAILED goto failed
echo Self-test passed. Opening the demo report.
if not defined PICT_QUIET start "" "%CD%\selftest\out\report.html"
if not defined PICT_QUIET pause
exit /b 0
:failed
echo SELF-TEST FAILED. Copy this window's text when reporting the problem.
if not defined PICT_QUIET pause
exit /b 1
