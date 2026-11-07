; Inno-Setup-Skript für den Windows-Installer (gebaut von installer/build_windows.py, nicht direkt aufrufen).
; Installation pro Nutzer ohne Administratorrechte nach %LOCALAPPDATA%\Programs\AstroFlow; Daten liegen getrennt
; in %USERPROFILE%\AstroFlow (Einrichtungsassistent) und bleiben beim Deinstallieren erhalten.
; Komponenten (seit 0.44.0): ASTAP, GraXpert, GPU, SkyTrace-Engine, Demo werden NICHT mitgeliefert, sondern nach der Installation
; von AstroFlow selbst geladen (core/components.py, Fortschritt in „Aufträge“, Wiederaufnahme, erneut anbietbar). Der Installer
; schreibt dafür nur die Auswahl nach %APPDATA%\AstroFlow\komponenten-auftrag.txt. StarNet wird nie geladen (Lizenz).
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
UsePreviousSetupType=yes

[Languages]
Name: "de"; MessagesFile: "compiler:Languages\German.isl"

[Messages]
de.SelectComponentsLabel2=Optionale Werkzeuge lädt AstroFlow nach der Installation selbst aus dem Internet (Fortschritt unter „Aufträge“; fehlt etwas, startet AstroFlow trotzdem und bietet es später erneut an). GraXpert steht unter der GPL-3 (Quellcode: github.com/Steffenhir/GraXpert). StarNet lädt AstroFlow aus Lizenzgründen nicht – es lässt sich später als ZIP einspielen.

[Types]
Name: "full"; Description: "Vollständig (empfohlen)"
Name: "compact"; Description: "Nur AstroFlow"
Name: "custom"; Description: "Benutzerdefiniert"; Flags: iscustom

[Components]
Name: "core"; Description: "AstroFlow (Programm mit eigenem Python)"; Types: full compact custom; Flags: fixed
Name: "astap"; Description: "Plate-Solving: ASTAP + Sterndatenbank (Download von SourceForge)"; Types: full
Name: "astap\d05"; Description: "D05 – Smart-Teleskope wie Stellina, Seestar, Vespera (Bildfeld ab 0,6°)"; Types: full; Flags: exclusive; ExtraDiskSpaceRequired: 110000000
Name: "astap\d50"; Description: "D50 – klassische Teleskope mit kleinem Bildfeld"; Flags: exclusive; ExtraDiskSpaceRequired: 950000000
Name: "graxpert"; Description: "GraXpert 3.0.2 – KI-Hintergrund und -Entrauschen (GPL-3, Download von GitHub)"; Types: full; ExtraDiskSpaceRequired: 435000000
Name: "gpu"; Description: "GPU-Beschleunigung (pyopencl)"; Types: full; ExtraDiskSpaceRequired: 40000000
Name: "skytrace"; Description: "SkyTrace-Analyse-Engine: Lichtspuren finden (OpenCV, PyTorch für den Prozessor)"; Types: full; ExtraDiskSpaceRequired: 1500000000
Name: "demo"; Description: "Demo-Aufnahmen fürs erste Bild"; Types: full

[Tasks]
Name: "desktopicon"; Description: "Verknüpfung auf dem Desktop"; Flags: unchecked
Name: "autostart"; Description: "Beim Anmelden im Hintergrund starten (neue Aufnahmen werden sofort verarbeitet)"; Flags: unchecked

[InstallDelete]
; Update: eigenes Python vollständig ersetzen (keine Reste alter Paketstände); gewählte GPU-/SkyTrace-Pakete lädt AstroFlow danach neu
; (pip-Zwischenspeicher, deshalb schnell)
Type: filesandordirs; Name: "{app}\python"

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

// Auswahl der Komponenten für AstroFlow ablegen (eine je Zeile; AstroFlow installiert sie beim nächsten Start im Hintergrund)
procedure CurStepChanged(CurStep: TSetupStep);
var
  Lines: TArrayOfString;
  N: Integer;
  procedure Add(S: String);
  begin
    SetArrayLength(Lines, N + 1);
    Lines[N] := S;
    N := N + 1;
  end;
begin
  if CurStep <> ssPostInstall then
    exit;
  N := 0;
  if WizardIsComponentSelected('astap\d50') then Add('astap:d50')
  else if WizardIsComponentSelected('astap') then Add('astap:d05');
  if WizardIsComponentSelected('graxpert') then Add('graxpert');
  if WizardIsComponentSelected('gpu') then Add('gpu');
  if WizardIsComponentSelected('skytrace') then Add('skytrace');
  if WizardIsComponentSelected('demo') then Add('demo');
  if N > 0 then
  begin
    ForceDirectories(ExpandConstant('{userappdata}\AstroFlow'));
    SaveStringsToUTF8File(ExpandConstant('{userappdata}\AstroFlow\komponenten-auftrag.txt'), Lines, False);
  end;
end;

// Deinstallation: Nutzerdaten bleiben immer. Von AstroFlow geladene Werkzeuge (GraXpert, ASTAP + Sterndatenbank) nur auf Nachfrage
// entfernen – still: /WERKZEUGE=entfernen (Standard: behalten). Vorhandenes, nicht von AstroFlow installiertes ASTAP bleibt unberührt.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Remove: Boolean;
  Tools: String;
begin
  Tools := ExpandConstant('{localappdata}\AstroFlow\Werkzeuge');
  if (CurUninstallStep = usUninstall) and FileExists(Tools + '\komponenten.json') then
  begin
    if UninstallSilent then
      Remove := CompareText(ExpandConstant('{param:WERKZEUGE|behalten}'), 'entfernen') = 0
    else
      Remove := MsgBox('Sollen auch die von AstroFlow geladenen Werkzeuge entfernt werden (GraXpert, ASTAP + Sterndatenbank)?' + #13#10#13#10 +
                       'Deine Bilder, Stacks und Einstellungen bleiben in jedem Fall erhalten.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    if Remove then
    begin
      Exec(ExpandConstant('{app}\python\pythonw.exe'), '-I -m astroflow components remove graxpert astap', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code);
      DelTree(Tools, True, True, True);
    end;
  end;
  if (CurUninstallStep = usPostUninstall) and not UninstallSilent then
    MsgBox('AstroFlow wurde entfernt. Deine Daten (Datenbank, Stacks, Bilder) liegen weiterhin in ' +
           ExpandConstant('{%USERPROFILE}') + '\AstroFlow bzw. im gewählten Datenordner und können bei Bedarf von Hand gelöscht werden.',
           mbInformation, MB_OK);
end;
