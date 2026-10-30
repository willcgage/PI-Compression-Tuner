@echo off
rem Moves to the folder where the tuner writes its files: PICT_WORK if set,
rem otherwise "PI Compression Tuner" under the user's Documents folder.
if not defined PICT_WORK set "PICT_WORK=%USERPROFILE%\Documents\PI Compression Tuner"
if not exist "%PICT_WORK%" mkdir "%PICT_WORK%"
cd /d "%PICT_WORK%"
