; Inno Setup — AI Command Center installer.
; Builds AICommandCenterSetup-<version>.exe from the PyInstaller onedir output.
; Compile:  iscc /DAppVersion=0.12.0 desktop/installer.iss
#ifndef AppVersion
  #define AppVersion "0.12.0"
#endif
#define AppName "AI Command Center"
#define AppExe "AICommandCenter.exe"
#define AppPublisher "AI Command Center"

[Setup]
AppId={{B7E2A1C4-9F3D-4E5A-8C1B-2D6F0A4E9C11}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\AICommandCenter
DefaultGroupName={#AppName}
OutputDir=..\dist-installer
OutputBaseFilename=AICommandCenterSetup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"

[Files]
Source: "..\dist-desktop\AICommandCenter\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
