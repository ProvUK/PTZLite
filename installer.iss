#define AppName    "Reolink Viewer"
#define AppVersion "3.6"
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
; Offer to launch the app at the end of the installer wizard
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName} now"; \
  Flags: nowait postinstall skipifsilent
