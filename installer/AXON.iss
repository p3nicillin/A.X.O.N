#define MyAppName "AXON"
#define MyAppVersion "1.4.0"
#define MyAppPublisher "p3nicillin"
#define MyAppExeName "AXON.exe"

[Setup]
AppId={{F846B9D2-B62E-4E29-922B-ED7E484CF2E7}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\AXON
DefaultGroupName=AXON
OutputDir=..\dist\installer
OutputBaseFilename=AXON-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
WizardStyle=modern

[Files]
Source: "..\dist\AXON\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AXON"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\AXON"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\AXON"; Filename: "{app}\{#MyAppExeName}"; Tasks: startup

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; Flags: unchecked
Name: "startup"; Description: "Start AXON when I sign in"; Flags: unchecked

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch AXON"; Flags: nowait postinstall skipifsilent
