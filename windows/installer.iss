; Inno Setup script for Magick Studio (per-user install, no admin rights).
; Built by windows/build.ps1:  ISCC /DAppVersion=1.1.1 /DSourceDir=... /DOutputDir=...

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\build\windows\dist\MagickStudio"
#endif
#ifndef OutputDir
  #define OutputDir "..\build\windows\installer"
#endif

[Setup]
; Never change AppId: it lets newer versions upgrade the existing installation.
AppId={{6F3C2A8E-4B1D-4E7A-9C55-2D8F1B7E9A31}
AppName=Magick Studio
AppVersion={#AppVersion}
AppVerName=Magick Studio {#AppVersion}
AppPublisher=Magick Studio contributors
AppPublisherURL=https://github.com/yassermaina/magick-studio
AppSupportURL=https://github.com/yassermaina/magick-studio/issues
AppUpdatesURL=https://github.com/yassermaina/magick-studio/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\Magick Studio
DefaultGroupName=Magick Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
; Qt 6 needs Windows 10 version 1809 (build 17763) or later.
MinVersion=10.0.17763
OutputDir={#OutputDir}
OutputBaseFilename=MagickStudio-Setup-{#AppVersion}-x64
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\MagickStudio.exe
UninstallDisplayName=Magick Studio
LicenseFile=..\LICENSE
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
; Created by the running app: setup and uninstall ask the user to close it first.
AppMutex=MagickStudio.Running
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[InstallDelete]
; Clean upgrades: remove files from the previous version before copying new ones.
; Only when the folder holds Magick Studio, never another app's "_internal".
Type: filesandordirs; Name: "{app}\_internal"; Check: IsMagickStudioFolder

[UninstallDelete]
; Only the application's own folder; user images are never stored here.
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\Magick Studio"; Filename: "{app}\MagickStudio.exe"; AppUserModelID: "MagickStudio.MagickStudio"
Name: "{autodesktop}\Magick Studio"; Filename: "{app}\MagickStudio.exe"; AppUserModelID: "MagickStudio.MagickStudio"; Tasks: desktopicon

[Run]
Filename: "{app}\MagickStudio.exe"; Description: "{cm:LaunchProgram,Magick Studio}"; Flags: nowait postinstall skipifsilent

[Code]
function IsMagickStudioFolder: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\MagickStudio.exe'));
end;
