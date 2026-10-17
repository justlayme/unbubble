; Inno Setup script for Unbubble (per-user install; only the Apple driver needs admin).
;   iscc packaging\windows\installer.iss      → dist\Unbubble-Setup.exe
; Code signing: open-source projects can get free signing from the SignPath Foundation, or use
; Azure Trusted Signing. Unsigned installers show a SmartScreen warning.

#define AppName "Unbubble"
#define AppVersion GetEnv("GM_VERSION") == "" ? "1.0.2" : GetEnv("GM_VERSION")
#define AppExe "Unbubble.exe"

[Setup]
AppId={{8C1E4E2B-5B5F-4E7E-9E54-3F2A6A1C0B7D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=Grey Mirror
AppPublisherURL=https://thegreymirror.com
AppSupportURL=https://github.com/justlayme/unbubble
DefaultDirName={localappdata}\Programs\{#AppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
OutputBaseFilename=Unbubble-Setup
SetupIconFile=..\icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
LicenseFile=..\..\LICENSE
CloseApplications=force
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763

[Files]
Source: "..\..\dist\Unbubble\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Registry]
; Lets the helper know the installer already asked about starting at sign-in.
Root: HKCU; Subkey: "Software\Unbubble"; ValueType: string; ValueName: "Version"; ValueData: "{#AppVersion}"; Flags: uninsdeletekey
; unbubble:// links from thegreymirror.com wake the helper (and carry Safari pairing).
Root: HKCU; Subkey: "Software\Classes\unbubble"; ValueType: string; ValueName: ""; ValueData: "URL:Unbubble"; Flags: uninsdeletekey
Root: HKCU; Subkey: "Software\Classes\unbubble"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\unbubble\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExe},0"
Root: HKCU; Subkey: "Software\Classes\unbubble\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExe}"" ""%1"""
; Start quietly at sign-in so thegreymirror.com finds it without the person opening anything.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "Unbubble"; ValueData: """{app}\{#AppExe}"" --background"; Flags: uninsdeletevalue; Tasks: startup

[Tasks]
Name: "startup"; Description: "Start Unbubble when I sign in (recommended, so the website can find it)"
Name: "driver"; Description: "Install Apple's iPhone driver (free, from Apple; Windows will ask for permission)"; Check: not AppleDriverInstalled

[Run]
; Apple's official driver package, without iTunes. winget shows the Windows admin prompt.
Filename: "{cmd}"; Parameters: "/c winget install --exact --id Apple.AppleMobileDeviceSupport --silent --accept-package-agreements --accept-source-agreements --disable-interactivity"; StatusMsg: "Installing Apple's iPhone driver…"; Flags: runhidden waituntilterminated; Tasks: driver; Check: WingetAvailable
Filename: "ms-windows-store://pdp/?productid=9NP83LWLPZ9K"; Flags: shellexec postinstall skipifsilent; Description: "Get Apple Devices from the Microsoft Store (includes the iPhone driver)"; Tasks: driver; Check: not WingetAvailable
Filename: "{app}\{#AppExe}"; Description: "Open {#AppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /im {#AppExe} /f"; Flags: runhidden; RunOnceId: "StopHelper"

[Code]
function AppleDriverInstalled(): Boolean;
begin
  Result := RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\Apple Mobile Device Service');
end;

function WingetAvailable(): Boolean;
var Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/c winget --version', '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\Unbubble');
    if DirExists(DataDir) then
      if MsgBox('Also delete the messages Unbubble copied from your iPhone? ' +
                'Exports you saved to other folders are not affected.', mbConfirmation, MB_YESNO) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
