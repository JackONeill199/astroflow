; Inno-Setup-Skript für den Windows-Installer (gebaut von installer/build_windows.py, nicht direkt aufrufen).
; Installation pro Nutzer ohne Administratorrechte nach %LOCALAPPDATA%\Programs\AstroFlow; Daten liegen getrennt
; in %USERPROFILE%\AstroFlow (Einrichtungsassistent) und bleiben beim Deinstallieren erhalten.
#ifndef AppVersion
  #error AppVersion fehlt (/DAppVersion=…)
#endif

[Setup]
AppId={{6E2B7A51-4C1D-4F7B-9A57-0A57F10A0F10}
AppName=AstroFlow
AppVersion={#AppVersion}
AppVerName=AstroFlow {#AppVersion}
AppPublisher=AstroFlow
AppPublisherURL=https://github.com/JackONeill199/astroflow
AppSupportURL=https://github.com/JackONeill199/astroflow/issues
AppUpdatesURL=https://github.com/JackONeill199/astroflow/releases
DefaultDirName={autopf}\AstroFlow
DefaultGroupName=AstroFlow
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutDir}
OutputBaseFilename=AstroFlow-Setup-{#AppVersion}
SetupIconFile={#SourceDir}\astroflow.ico
UninstallDisplayIcon={app}\astroflow.ico
UninstallDisplayName=AstroFlow
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
LicenseFile={#SourceDir}\LICENSE.txt

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop"; Flags: unchecked
Name: "autostart"; Description: "Beim Anmelden im Hintergrund starten (neue Aufnahmen werden sofort verarbeitet)"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "AstroFlow.cmd,AstroFlow beenden.cmd"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\AstroFlow"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m astroflow start"; WorkingDir: "{app}"; IconFilename: "{app}\astroflow.ico"; Comment: "AstroFlow starten und im Browser öffnen"
Name: "{group}\AstroFlow beenden"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m astroflow stop"; WorkingDir: "{app}"; IconFilename: "{app}\astroflow.ico"
Name: "{group}\AstroFlow deinstallieren"; Filename: "{uninstallexe}"
Name: "{autodesktop}\AstroFlow"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m astroflow start"; WorkingDir: "{app}"; IconFilename: "{app}\astroflow.ico"; Tasks: desktopicon
Name: "{userstartup}\AstroFlow"; Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m astroflow start --no-browser"; WorkingDir: "{app}"; IconFilename: "{app}\astroflow.ico"; Tasks: autostart

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m astroflow start"; WorkingDir: "{app}"; Description: "AstroFlow jetzt starten"; Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\python\pythonw.exe"; Parameters: "-I -m astroflow stop"; WorkingDir: "{app}"; Flags: runhidden waituntilterminated; RunOnceId: "StopAstroFlow"

[UninstallDelete]
; Programmordner samt später erzeugtem Bytecode und nachinstallierten Paketen (GPU) – die Daten liegen woanders
Type: filesandordirs; Name: "{app}"

[Code]
// Update über eine laufende Version: Server vorher beenden, sonst sind python*.dll gesperrt
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\python\pythonw.exe')) then
    Exec(ExpandConstant('{app}\python\pythonw.exe'), '-I -m astroflow stop', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('AstroFlow wurde entfernt. Deine Daten (Datenbank, Stacks, Bilder) liegen weiterhin in ' +
           ExpandConstant('{%USERPROFILE}') + '\AstroFlow bzw. im gewählten Datenordner und können bei Bedarf von Hand gelöscht werden.',
           mbInformation, MB_OK);
end;
