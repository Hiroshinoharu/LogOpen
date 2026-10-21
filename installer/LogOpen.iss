; Build with scripts/build.ps1 -Installer. No administrator rights are required.
; User scan history, preferences, and credentials deliberately survive uninstall.
#ifndef AppVersion
  #error Supply AppVersion using scripts/build.ps1 or ISCC /DAppVersion=MAJOR.MINOR.PATCH
#endif
#ifndef SourceRoot
  #define SourceRoot ".."
#endif

[Setup]
AppId={{B81DE21D-F07E-44D6-A17A-901109EA2D30}
AppName=LogOpen
AppVersion={#AppVersion}
AppPublisher=LogOpen
DefaultDirName={localappdata}\Programs\LogOpen
DefaultGroupName=LogOpen
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#SourceRoot}\dist\installer
OutputBaseFilename=LogOpen-{#AppVersion}-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\LogOpen.exe
CloseApplications=yes
RestartApplications=no
SetupLogging=yes
SetupIconFile={#SourceRoot}\assets\logopen.ico

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"

[Files]
Source: "{#SourceRoot}\dist\LogOpen\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\LogOpen"; Filename: "{app}\LogOpen.exe"; WorkingDir: "{localappdata}\LogOpen"
Name: "{autodesktop}\LogOpen"; Filename: "{app}\LogOpen.exe"; IconFilename: "{app}\LogOpen.exe"; WorkingDir: "{localappdata}\LogOpen"; Tasks: desktopicon

[Dirs]
Name: "{localappdata}\LogOpen"; Flags: uninsneveruninstall

[Run]
Filename: "{app}\LogOpen.exe"; Description: "Launch LogOpen"; Flags: nowait postinstall skipifsilent
