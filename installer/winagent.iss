; WinAgent silent installer (Inno Setup 6). Per-user install, no admin rights, no wizard pages.
; IT runs it silently with the company's server address and the organization's enrollment token:
;   WinAgentSetup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /APIURL=https://monitoring.example.com/api/agent/v1 /ORGTOKEN=...
; The token comes from the server (manage.py issue_enrollment_token --org "Name"). It is required.
; Optional: /CAFILE=C:\path\to\company-ca.pem (only when the server uses a private certificate authority).
; Build: run build.ps1 first (creates dist\winagent), then: ISCC.exe installer\winagent.iss

#define AppVersion "0.1.0"

#ifndef DefaultDashboardUrl
  #define DefaultDashboardUrl ""
#endif
#ifndef DefaultApiUrl
  #define DefaultApiUrl ""
#endif
; Screenshots are off on the PC unless this is "1" (build.ps1 with WINAGENT_SCREENSHOTS=1) or the installer gets /SCREENSHOTS=1.
; The organization's screenshot policy must also be on. Both must agree before anything is captured.
#ifndef DefaultScreenshots
  #define DefaultScreenshots ""
#endif
; Optional CA file for a private or demo certificate (build.ps1 with WINAGENT_CA_FILE). Leave empty for a trusted certificate.
#ifndef DefaultCaFile
  #define DefaultCaFile ""
#endif

[Setup]
AppId={{6B0E2F7A-3C1D-4E8B-9A57-0C1A6E0B0001}
AppName=WinAgent
AppVersion={#AppVersion}
AppPublisher=Employee Monitoring
DefaultDirName={localappdata}\WinAgent\app
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableWelcomePage=yes
DisableFinishedPage=yes
PrivilegesRequired=lowest
Uninstallable=yes
; Setup does not try to close the agent itself. InitializeSetup stops it first (see below).
CloseApplications=no
UninstallDisplayName=WinAgent
OutputDir=..\dist
OutputBaseFilename=WinAgentSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
; Unsigned build (code signing is a TAD 13 open decision). Do not distribute until it is signed.

[Files]
Source: "..\dist\winagent\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Code]
var
  ApiUrl: string;
  CaFile: string;
  OrgToken: string;
  ScreenshotsOn: Boolean;

function DashboardUrl(Param: string): string;
var
  P: Integer;
  Root: string;
begin
  // The employee's dashboard: /DASHBOARDURL, else the address set at build time, else the API's own address.
  Result := ExpandConstant('{param:DASHBOARDURL|}');
  if Result = '' then Result := '{#DefaultDashboardUrl}';
  if Result = '' then
  begin
    P := Pos('/api/agent/v1', ApiUrl);
    if P > 0 then Root := Copy(ApiUrl, 1, P - 1) else Root := ApiUrl;
    Result := Root + '/dashboard/me/';
  end;
end;

function TokenIsSafe(Token: string): Boolean;
var
  I: Integer;
  C: Char;
begin
  // The token is written into a TOML string, so only URL-safe characters are allowed.
  Result := Length(Token) > 0;
  for I := 1 to Length(Token) do
  begin
    C := Token[I];
    if not (((C >= 'A') and (C <= 'Z')) or ((C >= 'a') and (C <= 'z')) or ((C >= '0') and (C <= '9')) or (C = '-') or (C = '_')) then
      Result := False;
  end;
end;

function TokenFromFileName(): string;
var
  Name: string;
  P: Integer;
begin
  // The download is named WinAgentSetup-<token>.exe. Windows adds " (1)" to a repeated download.
  Result := '';
  Name := ExtractFileName(ExpandConstant('{srcexe}'));
  if Pos('WinAgentSetup-', Name) <> 1 then exit;
  Result := Copy(Name, 15, Length(Name));
  P := Pos(' (', Result);
  if P > 0 then Result := Copy(Result, 1, P - 1);
  P := Pos('.exe', LowerCase(Result));
  if P > 0 then Result := Copy(Result, 1, P - 1);
end;

function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
begin
  // Stop a running agent before Setup checks files. Otherwise Setup shows "Closing applications" and fails.
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM winagent.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  ApiUrl := ExpandConstant('{param:APIURL|}');
  // Without /APIURL, use the address this installer was built for (build.ps1 with WINAGENT_API_URL).
  if ApiUrl = '' then ApiUrl := '{#DefaultApiUrl}';
  CaFile := ExpandConstant('{param:CAFILE|}');
  if CaFile = '' then CaFile := '{#DefaultCaFile}';
  ScreenshotsOn := (ExpandConstant('{param:SCREENSHOTS|}') = '1') or ('{#DefaultScreenshots}' = '1');
  OrgToken := ExpandConstant('{param:ORGTOKEN|}');
  // The one-click download carries its token in the file name.
  if OrgToken = '' then OrgToken := TokenFromFileName();
  // The server address is required and must be HTTPS. Without it, nothing is installed.
  Result := (Copy(ApiUrl, 1, 8) = 'https://');
  if not Result then
    MsgBox('WinAgent setup needs /APIURL=https://... to know the monitoring server.', mbError, MB_OK);
  // Without the organization's token the PC cannot ask to connect, so stop here.
  if Result and not TokenIsSafe(OrgToken) then
  begin
    Result := False;
    MsgBox('WinAgent setup needs /ORGTOKEN=... from your administrator.', mbError, MB_OK);
  end;
  // A CA file that does not exist would make the agent refuse to start, so stop here instead.
  if Result and (CaFile <> '') and not FileExists(CaFile) then
  begin
    Result := False;
    MsgBox('The /CAFILE path does not exist: ' + CaFile, mbError, MB_OK);
  end;
end;

// Uninstall: stop a running agent first. Its files are locked while it runs, so they would be left behind.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
begin
  if CurUninstallStep = usUninstall then
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM winagent.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Config: string;
  CaPath: string;
  ResultCode: Integer;
begin
  // Written before the files are copied, so it already exists when the [Run] sign-in step starts.
  if CurStep = ssPostInstall then
  begin
    // The installer opens the employee's dashboard itself (see [Run]). This marker tells the agent not to open it again.
    ForceDirectories(ExpandConstant('{localappdata}\WinAgent'));
    SaveStringToFile(ExpandConstant('{localappdata}\WinAgent\dashboard_opened'), 'opened', False);
  end;
  if CurStep = ssInstall then
  begin
    // Stop a running agent first, so its files can be replaced (otherwise Windows reports "Access is denied").
    Exec(ExpandConstant('{sys}\taskkill.exe'), '/F /IM winagent.exe', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    // Every install starts a new pairing: an old credential is removed, so the agent asks the server to
    // connect and the employee's dashboard opens for approval. Without this, an old credential skips pairing.
    DeleteFile(ExpandConstant('{localappdata}\WinAgent\credential.bin'));
    DeleteFile(ExpandConstant('{localappdata}\WinAgent\health.json'));
    // A fresh install opens the employee dashboard once (the agent remembers it with this file).
    DeleteFile(ExpandConstant('{localappdata}\WinAgent\dashboard_opened'));
    ForceDirectories(ExpandConstant('{app}'));
    // The config is written by the installer, so the employee never edits a file.
    Config := '[api]' + #13#10 + 'base_url = "' + ApiUrl + '"' + #13#10 + 'timeout_seconds = 10' + #13#10 + 'org_token = "' + OrgToken + '"' + #13#10;
    if CaFile <> '' then
    begin
      // TOML strings treat a backslash as an escape, so write the path with forward slashes (Windows accepts them).
      CaPath := CaFile;
      StringChange(CaPath, '\', '/');
      Config := Config + 'ca_bundle = "' + CaPath + '"' + #13#10;
    end;
    if ScreenshotsOn then
      Config := Config + #13#10 + '[screenshots]' + #13#10 + 'enabled = true' + #13#10;
    SaveStringToFile(ExpandConstant('{app}\config.toml'), Config, False);
  end;
end;

[Run]
; Start at sign-in (per user, no admin rights). Pairing also does this once the PC is connected.
Filename: "{app}\winagent.exe"; Parameters: "autostart enable --config ""{app}\config.toml"""; Flags: runhidden
; Start now, hidden. With no credential yet, the agent asks the server to connect and waits for approval.
Filename: "{app}\winagent.exe"; Parameters: "run --config ""{app}\config.toml"""; Flags: runhidden nowait
; After installing, open the employee's dashboard in the browser of the signed-in user, which uses their existing session.
Filename: "{code:DashboardUrl|}"; Flags: shellexec postinstall nowait runasoriginaluser

[UninstallDelete]
; Uninstall removes what the agent wrote, so a reinstall starts clean and no enrollment token stays on disk.
Type: files; Name: "{app}\config.toml"
Type: files; Name: "{localappdata}\WinAgent\queue.sqlite3"
Type: files; Name: "{localappdata}\WinAgent\health.json"
Type: files; Name: "{localappdata}\WinAgent\dashboard_opened"
Type: filesandordirs; Name: "{localappdata}\WinAgent\logs"

[UninstallRun]
Filename: "{app}\winagent.exe"; Parameters: "autostart disable --config ""{app}\config.toml"""; Flags: runhidden; RunOnceId: "DisableAutostart"
