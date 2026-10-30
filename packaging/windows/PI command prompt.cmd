@echo off
rem Opens a command prompt where `pict` works, in the tuner's work folder.
call "%~dp0work-folder.cmd"
set "PATH=%~dp0;%PATH%"
title PI Compression Tuner
echo PI Compression Tuner command prompt. Files are written to:
echo   %CD%
echo.
echo Start with:  pict pi check --server YOUR_PI_SERVER --url https://YOUR_WEBAPI_HOST/piwebapi
echo Help:        pict --help    or    pict pi --help
echo.
cmd /k
