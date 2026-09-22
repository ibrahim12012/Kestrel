; installer/Kestrel.iss
; =====================
; Inno Setup script for Kestrel. Wraps the `dist/Kestrel/` folder produced by
; `pyinstaller Kestrel.spec` (see README.md's "Building a Windows Executable")
; into a real installer: Start Menu/Desktop shortcuts, an Add/Remove
; Programs entry with a proper uninstaller, an optional "launch at Windows
; startup" task, a first-run page to paste in a GEMINI_API_KEY, and an
; uninstall prompt asking whether to keep or wipe saved data.
;
; Build with Inno Setup (https://jrsoftware.org/isinfo.php, free):
;   1. pyinstaller Kestrel.spec      (produces dist\Kestrel\Kestrel.exe)
;   2. Open this file in Inno Setup, or run:
;        iscc installer\Kestrel.iss
;   3. Output lands in installer\Output\KestrelSetup.exe
;
; #define MyAppVersion below is the ONE thing to keep in sync with
; paths.APP_VERSION by hand. Kestrel.spec generates the .exe's own version
; resource from APP_VERSION directly, so this is the only hand-copied one
; left; same as APP_VERSION's own comment already notes for CHANGELOG.md.
;
; Antivirus note: an unsigned installer gets a SmartScreen "Unknown
; publisher" prompt no matter what is in this file. VersionInfo* below and
; the SignTool line are what change that. See docs/ANTIVIRUS.md.
#define MyAppName "Kestrel"
#define MyAppVersion "0.2.1"
#define MyAppPublisher "Kestrel"
#define MyAppExeName "Kestrel.exe"
#define MyAppDescription "AI desktop assistant and automation for Windows"
; Keep these three in step with branding.py's EXPORT_SUFFIX,
; FILE_TYPE_PROGID and FILE_TYPE_NAME. The [Registry] section below
; registers the association from them.
#define MyAppExtension ".ktl"
#define MyAppProgID "Kestrel.MacroPack.1"
#define MyAppFileType "Kestrel macro pack"

[Setup]
AppId={{4F1E9C2A-7B3D-4A6F-9E1C-1A2B3C4D5E6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
; Shown as a scrollable license page the installer won't proceed past
; without an explicit "I accept". See EULA.md for the actual terms
; (includes the liability disclaimer for autonomous AI actions this
; app can take on the user's machine).
LicenseFile=..\EULA.md

; The installer's own VERSIONINFO resource. Same reasoning as the one
; Kestrel.spec compiles into Kestrel.exe: a setup executable carrying no
; publisher or product name at all is scored on that alone by static
; heuristics, and costs nothing to fill in.
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoDescription={#MyAppDescription} Setup
VersionInfoCopyright=Copyright (C) 2026 the Kestrel authors

; Uncomment once a code-signing certificate is available (see
; docs/ANTIVIRUS.md). This signs the INSTALLER itself; Kestrel.exe
; inside dist\Kestrel\ needs to be signed separately, BEFORE running
; iscc, via a signtool call in your build script.
; SignTool=signtool sign /a /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $f

; {autopf} resolves to Program Files for an admin/machine-wide install, or
; a per-user equivalent if the person doesn't have admin rights (see
; PrivilegesRequiredOverridesAllowed below). Either way, user DATA still
; goes under %LOCALAPPDATA%\Kestrel regardless of where the PROGRAM itself
; is installed (see paths.py's USER_DATA_PATH), so this choice doesn't
; affect where settings/macros/chat history end up.
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=Output
OutputBaseFilename=KestrelSetup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
; PyInstaller's own --onedir output already contains everything needed;
; nothing here needs admin beyond what installing to Program Files does.
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "startupshortcut"; Description: "&Launch {#MyAppName} when Windows starts"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
; Unchecked by default, and a Startup-folder shortcut rather than a
; registry Run key. Both are deliberate. Launch-at-startup is a real
; behavior change (an always-listening app running before the person
; opened it themselves), not something to opt someone into silently, and
; a shortcut the person can see and delete in their own Startup folder is
; a different thing from a Run key, which is where software that does not
; want to be found puts itself. Worth turning on explicitly for anyone
; actually using wake-word mode day to day.

[Files]
; Mirrors PyInstaller's --onedir output tree exactly, same reasoning as
; Kestrel.spec's own `datas` list mirroring the source tree (see README's
; "Building a Windows Executable"): whatever's actually in dist\Kestrel\
; is what ships, nothing hand-picked or assumed here.
Source: "..\dist\Kestrel\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Registry]
; Associate .ktl (macro packs) with Kestrel, so double-clicking one
; opens it rather than landing in the "how do you want to open this
; file?" dialog.
;
; Written under HKA, which Inno resolves to HKLM for an admin install
; and HKCU for a per-user one - matching PrivilegesRequired=lowest
; above. Writing HKLM unconditionally would fail the whole install for
; a user without admin rights.
;
; uninsdeletekey removes these on uninstall, so the extension does not
; stay pointing at an executable that is no longer there.
Root: HKA; Subkey: "Software\Classes\{#MyAppExtension}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppProgID}"; Flags: uninsdeletevalue uninsdeletekeyifempty
Root: HKA; Subkey: "Software\Classes\{#MyAppProgID}"; ValueType: string; ValueName: ""; ValueData: "{#MyAppFileType}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\{#MyAppProgID}\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName},0"
Root: HKA; Subkey: "Software\Classes\{#MyAppProgID}\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""
; Declares the app as a handler Windows will offer in "Open with", even
; when something else currently owns the extension.
Root: HKA; Subkey: "Software\Classes\Applications\{#MyAppExeName}\SupportedTypes"; ValueType: string; ValueName: "{#MyAppExtension}"; ValueData: ""

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{userstartup}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startupshortcut

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent

[Code]
var
  ApiKeyPage: TInputQueryWizardPage;

{ Same directory paths.py's USER_DATA_PATH resolves to for a frozen
  (installed) build: %LOCALAPPDATA%\Kestrel, which is branding.APP_NAME,
  not APP_SLUG. Duplicated here rather than imported (this is Pascal
  Script, not Python) so if that function's logic ever changes, this
  needs updating to match. }
function KestrelUserDataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\{#MyAppName}');
end;

procedure InitializeWizard();
begin
  { A custom page, not [Code]-only silent writing, so the person can see
    what's being asked and why, and can leave it blank and paste the key
    into Settings later instead, exactly like a from-source/manual install
    already allows. Never required: the app already runs fine without a
    key, it just can't make AI requests until one's set (see
    ai.main.get_api_key_present's own "no API key configured" message). }
  ApiKeyPage := CreateInputQueryPage(wpSelectTasks,
    'Gemini API Key', 'Optional. You can also add this later in Settings',
    'Paste your Gemini API key below, or leave this blank and add it from '
    + '{#MyAppName}''s own Settings page after it''s running. Get a free key at '
    + 'https://aistudio.google.com/app/apikey');
  ApiKeyPage.Add('GEMINI_API_KEY:', False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  DataDir, EnvPath, Key: String;
  EnvFile: TArrayOfString;
begin
  if CurStep = ssPostInstall then
  begin
    Key := Trim(ApiKeyPage.Values[0]);
    if Key <> '' then
    begin
      { Same location ai.main.ENV_PATH now resolves to (see that
        module's own comment on why this moved off BASE_PATH/Program
        Files, which a standard installed-app user can't write to
        anyway). Writing it here means a fresh install can have AI
        requests working the moment it first launches, with no extra
        step. Never overwrites an existing .env (a previous install's
        settings/macros survive an upgrade over top of it): only
        creates one if this location doesn't have one yet. }
      DataDir := KestrelUserDataDir();
      EnvPath := DataDir + '\.env';
      if not FileExists(EnvPath) then
      begin
        ForceDirectories(DataDir);
        SetArrayLength(EnvFile, 1);
        EnvFile[0] := 'GEMINI_API_KEY=' + Key;
        SaveStringsToFile(EnvPath, EnvFile, False);
      end;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
begin
  { Just confirming the uninstall itself proceeds. The actual
    keep-or-wipe-data choice happens in CurUninstallStepChanged below,
    after program files are already gone, so the prompt reflects what's
    actually left to decide about (user data), not the uninstall itself. }
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
  Response: Integer;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := KestrelUserDataDir();
    if DirExists(DataDir) then
    begin
      Response := MsgBox(
        '{#MyAppName} has been uninstalled. Do you also want to permanently ' +
        'delete your saved data (macros, chat history, API key, and ' +
        'settings) stored at:' + #13#10 + DataDir + #13#10#13#10 +
        'Choose "No" to keep this data (e.g. if you plan to reinstall ' +
        'later), or "Yes" to remove it completely.',
        mbConfirmation, MB_YESNO or MB_DEFBUTTON2);
      if Response = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
