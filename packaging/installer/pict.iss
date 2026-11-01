; Windows installer for the PI Compression Tuner (Inno Setup 6).
;
; Built by .github/workflows/windows-bundle.yml from the unpacked offline bundle:
;   iscc /DAppVersion=0.1.0 /DSourceDir=C:\path\to\PI-Compression-Tuner-0.1.0-win64 /DOutputDir=dist packaging\installer\pict.iss
;
; It installs the bundle under Program Files with Start menu shortcuts. One optional task sets the tuner up
; as a Windows service that several people use from their browsers with Windows sign-in (`pict service install`).
;
; Silent install, with the service:
;   PI-Compression-Tuner-Setup.exe /VERYSILENT /TASKS="service,firewall" /PORT=8501 /ACCOUNT="NT AUTHORITY\NetworkService" /ADMINS="CORP\PI Admins"
; For a named service account, set PICT_SERVICE_PASSWORD in the environment first.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #error Pass /DSourceDir=<unpacked bundle folder>
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif

[Setup]
AppId={{6C1F3B7E-2A54-4D7B-9E0B-5D3C8A41F2C9}
AppName=PI Compression Tuner
AppVersion={#AppVersion}
AppVerName=PI Compression Tuner {#AppVersion}
AppPublisher=PI Compression Tuner
DefaultDirName={autopf}\PI Compression Tuner
DefaultGroupName=PI Compression Tuner
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=PI-Compression-Tuner-{#AppVersion}-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=PI Compression Tuner
SetupLogging=yes
CloseApplications=no

[Tasks]
Name: "service"; Description: "Run the tuner as a Windows service that several people use from their browsers (Windows sign-in)"; Flags: unchecked
Name: "firewall"; Description: "Allow the service's port through Windows Firewall"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[InstallDelete]
; A fresh copy of the bundle each time, so files from an older version can't linger.
Type: filesandordirs; Name: "{app}\python"

[Icons]
Name: "{group}\Open tuner UI (this computer only)"; Filename: "{app}\Open tuner UI.cmd"; WorkingDir: "{app}"
Name: "{group}\PI command prompt"; Filename: "{app}\PI command prompt.cmd"; WorkingDir: "{app}"
Name: "{group}\Run self-test"; Filename: "{app}\Run self-test.cmd"; WorkingDir: "{app}"
Name: "{group}\Read me first"; Filename: "{app}\START-HERE.txt"
Name: "{group}\Shared tuner (browser)"; Filename: "http://localhost:{code:GetPort}/"; Tasks: service

[Run]
Filename: "{app}\python\python.exe"; Parameters: "-s -m compileall -q -j 0 ""{app}\python\Lib"""; StatusMsg: "Preparing Python (one time)..."; Flags: runhidden

[UninstallRun]
Filename: "{app}\python\python.exe"; Parameters: "-s -m pi_compression_tuner service uninstall"; RunOnceId: "RemoveService"; Flags: runhidden

[UninstallDelete]
Type: filesandordirs; Name: "{app}\python"
Type: files; Name: "{app}\service\pict-service.xml"

[Code]
var
  ServicePage: TInputQueryWizardPage;

function SetEnvironmentVariable(lpName: string; lpValue: string): BOOL;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

procedure InitializeWizard;
begin
  ServicePage := CreateInputQueryPage(wpSelectTasks, 'Shared server',
    'How the tuner runs as a Windows service',
    'People open http://THIS-SERVER:PORT/ and sign in with their Windows account. Admins may change the settings ' +
    'and who may sign in; more people can be added later on the Admin page. The service reads PI as its account, ' +
    'so map that account to a read-only PI identity. NetworkService reads as this computer''s domain account (DOMAIN\COMPUTER$).');
  ServicePage.Add('Port:', False);
  ServicePage.Add('Service account (reads PI):', False);
  ServicePage.Add('Password (only for a named account; leave empty for NetworkService or a gMSA):', True);
  ServicePage.Add('Admins, accounts or groups separated by ; (empty keeps the saved list, at first BUILTIN\Administrators):', False);
  ServicePage.Values[0] := ExpandConstant('{param:PORT|8501}');
  ServicePage.Values[1] := ExpandConstant('{param:ACCOUNT|NT AUTHORITY\NetworkService}');
  ServicePage.Values[2] := '';
  ServicePage.Values[3] := ExpandConstant('{param:ADMINS|}');
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = ServicePage.ID) and not WizardIsTaskSelected('service');
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Port: Integer;
begin
  Result := True;
  if CurPageID = ServicePage.ID then
  begin
    Port := StrToIntDef(Trim(ServicePage.Values[0]), 0);
    if (Port < 1) or (Port > 65535) then
    begin
      MsgBox('Enter a port between 1 and 65535.', mbError, MB_OK);
      Result := False;
    end
    else if Trim(ServicePage.Values[1]) = '' then
    begin
      MsgBox('Enter the account the service runs as, for example NT AUTHORITY\NetworkService.', mbError, MB_OK);
      Result := False;
    end;
  end;
end;

function GetPort(Param: string): string;
begin
  Result := Trim(ServicePage.Values[0]);
end;

function HostExe: string;
begin
  Result := ExpandConstant('{app}\service\pict-service.exe');
end;

function Pict(Args: string; LogName: string): Integer;
var
  LogFile, Cmd: string;
begin
  LogFile := ExpandConstant('{commonappdata}\PI Compression Tuner\' + LogName);
  ForceDirectories(ExtractFileDir(LogFile));
  Cmd := '/S /C ""' + ExpandConstant('{app}\python\python.exe') + '" -s -m pi_compression_tuner ' + Args +
         ' > "' + LogFile + '" 2>&1"';
  if not Exec(ExpandConstant('{cmd}'), Cmd, ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Result) then
    Result := -1;
  Log(Format('pict %s exited with %d; output in %s', [Args, Result, LogFile]));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  { An upgrade replaces files the running service uses, so stop it first. }
  if FileExists(HostExe) then
    Exec(HostExe, 'stop', '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Args, Password: string;
  Code: Integer;
begin
  if CurStep <> ssPostInstall then
    Exit;
  if WizardIsTaskSelected('service') then
  begin
    Args := 'service install --port ' + Trim(ServicePage.Values[0]) + ' --account "' + Trim(ServicePage.Values[1]) + '"';
    if Trim(ServicePage.Values[3]) <> '' then
      Args := Args + ' --admins "' + Trim(ServicePage.Values[3]) + '"';
    if WizardIsTaskSelected('firewall') then
      Args := Args + ' --open-firewall';
    Password := ServicePage.Values[2];
    if Password <> '' then
      SetEnvironmentVariable('PICT_SERVICE_PASSWORD', Password);
    Code := Pict(Args, 'service-install.log');
    SetEnvironmentVariable('PICT_SERVICE_PASSWORD', '');
    if Code <> 0 then
      SuppressibleMsgBox('The files were installed, but the service could not be set up. Details are in ' +
        ExpandConstant('{commonappdata}\PI Compression Tuner\service-install.log') +
        '. You can try again from an administrator prompt with: pict service install', mbError, MB_OK, IDOK);
  end
  else
    { Without the service task, make sure no service from an earlier install is left running old settings. }
    Pict('service uninstall', 'service-uninstall.log');
end;
