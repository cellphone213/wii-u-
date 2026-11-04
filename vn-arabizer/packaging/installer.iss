; Inno Setup script: builds VNArabizer-Setup-<version>.exe from the PyInstaller folder.
;   iscc /DAppVersion=0.1.0 packaging\installer.iss

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6F4C2D51-9A0B-4E1C-8F3A-7B2D9E5A1C40}
AppName=VN Arabizer
AppVersion={#AppVersion}
AppPublisher=VN Arabizer
DefaultDirName={localappdata}\Programs\VNArabizer
DefaultGroupName=VN Arabizer
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=VNArabizer-Setup-{#AppVersion}
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\VNArabizer.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\VNArabizer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\VN Arabizer"; Filename: "{app}\VNArabizer.exe"
Name: "{group}\{cm:UninstallProgram,VN Arabizer}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\VN Arabizer"; Filename: "{app}\VNArabizer.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\VNArabizer.exe"; Description: "{cm:LaunchProgram,VN Arabizer}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Downloaded models, GPU libraries and projects live in %LOCALAPPDATA%\VNArabizer.
; They are kept on uninstall so a reinstall does not re-download gigabytes;
; delete that folder by hand to remove everything.
