#define AppName    "Reolink Viewer"
#define AppVersion "4.7"
#define AppExe     "Reolink Viewer.exe"

[Setup]
AppName                = {#AppName}
AppVersion             = {#AppVersion}
AppVerName             = {#AppName} {#AppVersion}
; Install per-user so no admin rights are needed and the app can write config.yaml
DefaultDirName         = {localappdata}\Programs\{#AppName}
DefaultGroupName       = {#AppName}
DisableProgramGroupPage= yes
OutputDir              = .
OutputBaseFilename     = Reolink Viewer Setup
Compression            = lzma2
SolidCompression       = yes
WizardStyle            = modern
PrivilegesRequired     = lowest
; Allow elevation when available so the firewall rule [Run] step can succeed
PrivilegesRequiredOverridesAllowed = commandline dialog
UninstallDisplayIcon   = {app}\{#AppExe}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut";             GroupDescription: "Additional icons:"
Name: "startup";     Description: "Start automatically &with Windows";      GroupDescription: "On login:"

[Files]
; Entire PyInstaller output folder — recurse so all DLLs and the static/ tree are included
Source: "dist\Reolink Viewer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; Ship config.example.yaml as config.yaml on first install only — never overwrite existing config
Source: "config.example.yaml"; DestDir: "{app}"; DestName: "config.yaml"; Flags: onlyifdoesntexist uninsneveruninstall

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; Startup — only added when the user ticks that task; removed cleanly on uninstall
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; \
  Flags: uninsdeletevalue; Tasks: startup

[Run]
; Add Windows Firewall inbound rule covering Private+Domain+Public so UDP camera
; discovery (WS-Discovery multicast) works without the user being prompted.
; powershell is available on all Win10/11 machines and handles the rule correctly.
; runascurrentuser means it runs in the installer's elevated context if UAC was accepted.
Filename: "powershell.exe"; \
  Parameters: "-NonInteractive -WindowStyle Hidden -Command ""Remove-NetFirewallRule -DisplayName '{#AppName}' -ErrorAction SilentlyContinue; New-NetFirewallRule -DisplayName '{#AppName}' -Direction Inbound -Action Allow -Program '{app}\{#AppExe}' -Profile Private,Domain,Public -Enabled True | Out-Null"""; \
  Flags: runhidden waituntilterminated; \
  StatusMsg: "Configuring firewall rule for camera discovery..."

; Offer to launch the app at the end of the installer wizard
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the firewall rule on uninstall
Filename: "powershell.exe"; \
  Parameters: "-NonInteractive -WindowStyle Hidden -Command ""Remove-NetFirewallRule -DisplayName '{#AppName}' -ErrorAction SilentlyContinue"""; \
  Flags: runhidden waituntilterminated
