#define AppName "ALumenans"
#define AppVersion "0.1.0"
#define AppPublisher "ALumenans"
#define AppExeName "pythonw.exe"

[Setup]
AppId={{4DE7CB5A-838A-4F8F-BD7D-060767FFAFA9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/hannah-arendt/alumenans
AppSupportURL=https://github.com/hannah-arendt/alumenans
AppCopyright=Copyright (C) 2026 ALumenans
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
OutputDir=output
OutputBaseFilename=ALumenans-Setup-{#AppVersion}
SetupIconFile=..\..\src\alumenans\resources\ALumenans_logo.ico
UninstallDisplayIcon={app}\ALumenans_logo.ico
UninstallDisplayName={#AppName}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Установка {#AppName}
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
; Solid LZMA2 is single-threaded by default: ~34,000 files took over six
; minutes on a 4-core CI runner. Blocks compressed in parallel cost a
; little size. Each thread needs ~700 MB at ultra64, more than the 32-bit
; compiler can address, so compression runs in a separate process
; (islzma64.exe on 64-bit Windows) that has no such limit.
LZMANumBlockThreads=4
LZMAUseSeparateProcess=yes
WizardStyle=modern
WizardSizePercent=110
CloseApplications=yes
RestartApplications=no
UsePreviousAppDir=yes

; The installer's language also becomes the program's interface language
; (see [Code]); the person can change it later in the program itself.
[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"; InfoBeforeFile: "first-run.txt"
Name: "english"; MessagesFile: "compiler:Default.isl"; InfoBeforeFile: "first-run.en.txt"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce

[Files]
; torch\include (~9,400 C++ headers) and torch\lib\*.lib (import libraries)
; are only for compiling extensions against torch, never used at runtime.
; *.dist-info\licenses\* keeps each wheel's own source tree: torch's goes
; nine folders deep (third_party\kineto\...\duktape-1.8.0\LICENSE.txt), past
; Windows' 260-character path limit under {localappdata}\Programs, and the
; installer could not write those files. The same texts ship flattened in
; {app}\licenses (third_party_licenses.py collects them before compiling).
Source: "python-runtime\*"; DestDir: "{app}\runtime"; Excludes: "Doc\*;Lib\test\*;tcl\tk8.6\demos\*;Lib\site-packages\torch\include\*;Lib\site-packages\torch\lib\*.lib;*.dist-info\licenses\*"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\src\alumenans\*"; DestDir: "{app}\alumenans"; Excludes: "__pycache__,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\src\alumenans\resources\ALumenans_logo.ico"; DestDir: "{app}"; Flags: ignoreversion
; Licenses: the program's own (GPLv3), the notices, and the full texts of
; every bundled component, collected by build-installer.ps1.
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; DestName: "THIRD-PARTY-NOTICES.txt"; Flags: ignoreversion
Source: "output\licenses\*"; DestDir: "{app}\licenses"; Flags: ignoreversion recursesubdirs createallsubdirs

; Before 0.3.0 the modules lived directly in {app}. When upgrading over an
; old version they would stay next to the package as dead weight. The marks,
; the log and the models (models\) are left alone: that's state, not code.
[InstallDelete]
Type: files; Name: "{app}\*.py"
Type: files; Name: "{app}\anonymizer.html"
Type: files; Name: "{app}\slovar-imen-i-organizacij.txt"
Type: files; Name: "{app}\requirements-transcriber.txt"
Type: files; Name: "{app}\python-setup.html"
Type: files; Name: "{app}\ALumenans_logo.png"
Type: filesandordirs; Name: "{app}\__pycache__"
; The license folder is rebuilt every time: a package that was dropped
; must not leave its license behind.
Type: filesandordirs; Name: "{app}\licenses"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\runtime\{#AppExeName}"; Parameters: "-m alumenans"; WorkingDir: "{app}"; IconFilename: "{app}\ALumenans_logo.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\runtime\{#AppExeName}"; Parameters: "-m alumenans"; WorkingDir: "{app}"; IconFilename: "{app}\ALumenans_logo.ico"; Tasks: desktopicon

[Run]
Filename: "{app}\runtime\{#AppExeName}"; Parameters: "-m alumenans"; WorkingDir: "{app}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}"

[Code]
// The interface language starts as the installer's language. An upgrade keeps
// whatever the person has chosen in the program since.
procedure CurStepChanged(CurStep: TSetupStep);
var
  LanguageFile: String;
  Code: String;
begin
  if CurStep = ssPostInstall then
  begin
    LanguageFile := ExpandConstant('{app}\.ui-language');
    if not FileExists(LanguageFile) then
    begin
      if ActiveLanguage = 'english' then Code := 'en' else Code := 'ru';
      SaveStringToFile(LanguageFile, Code, False);
    end;
  end;
end;
