@echo off
rem Starts the tuner's web UI at http://localhost:8501 and opens it in the browser.
rem Close this window to stop the UI.
title PI Compression Tuner UI
call "%~dp0work-folder.cmd"
"%~dp0python\python.exe" -s "%~dp0check_components.py" --install-only || (pause & exit /b 1)
echo Starting the UI. It opens in your browser at http://localhost:8501
echo Close this window to stop it.
call "%~dp0pict.cmd" ui
pause
