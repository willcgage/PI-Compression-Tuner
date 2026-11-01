@echo off
rem Installs the shared tuner server as a Windows service, with Windows sign-in.
rem Needs an administrator prompt. Run it again to change the settings; the saved
rem settings and access lists are kept.
title PI Compression Tuner shared server setup
net session >nul 2>&1 || (echo Right-click this file and choose "Run as administrator". & pause & exit /b 1)
echo This installs the PI Compression Tuner as a Windows service that several people
echo can use from their browsers, signing in with their Windows accounts.
echo.
set "PORT=8501"
set /p "PORT=Port [8501]: "
set "ACCOUNT=NT AUTHORITY\NetworkService"
set /p "ACCOUNT=Service account, which reads PI [NT AUTHORITY\NetworkService]: "
set "ADMINS="
set /p "ADMINS=Admin accounts or groups, separated by ; [keep the saved list]: "
set "ARGS=--port %PORT% --open-firewall"
if defined ADMINS set ARGS=%ARGS% --admins "%ADMINS%"
call "%~dp0pict.cmd" service install %ARGS% --account "%ACCOUNT%"
pause
