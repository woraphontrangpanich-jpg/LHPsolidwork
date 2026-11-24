; SheetFlat installer (Inno Setup 6). Built by:  python tools/build_portable.py --installer
; It packs build\SheetFlat (bundled Python + packages + program), so the customer's PC needs
; nothing installed first: no Python, no internet, no administrator rights.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define Root ".."

[Setup]
AppId={{6C1F7D2E-3B0A-4C55-9E7B-5A1E2F0B9D41}
AppName=SheetFlat
AppVersion={#AppVersion}
AppVerName=SheetFlat {#AppVersion}
AppPublisher=SheetFlat
VersionInfoVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\SheetFlat
DefaultGroupName=SheetFlat
DisableProgramGroupPage=yes
DisableDirPage=auto
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Root}\dist
OutputBaseFilename=SheetFlat-Setup
SetupIconFile={#Root}\build\sheetflat.ico
UninstallDisplayIcon={app}\sheetflat.ico
UninstallDisplayName=SheetFlat
Compression=lzma2/max
SolidCompression=yes
LZMAUseSeparateProcess=yes
WizardStyle=modern
ShowLanguageDialog=auto
CloseApplications=yes

[Languages]
Name: "th"; MessagesFile: "compiler:Languages\Thai.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
th.RunNow=เปิด SheetFlat เลย
en.RunNow=Start SheetFlat now
th.DesktopIcon=สร้างไอคอนบนหน้าจอ (Desktop)
en.DesktopIcon=Create a desktop icon
th.Tagline=คลี่แผ่นเหล็กจากไฟล์ 3D
en.Tagline=Unfold sheet metal from 3D files

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"

[InstallDelete]
; an update replaces the program; the user's data (BD table, prices) is in %LOCALAPPDATA%\SheetFlat\data
Type: filesandordirs; Name: "{app}\app"
Type: filesandordirs; Name: "{app}\python"

[Files]
Source: "{#Root}\build\SheetFlat\python\*"; DestDir: "{app}\python"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#Root}\build\SheetFlat\app\*"; DestDir: "{app}\app"; Flags: recursesubdirs createallsubdirs ignoreversion
Source: "{#Root}\build\sheetflat.ico"; DestDir: "{app}"; Flags: ignoreversion
; a copy of this Setup.exe: the owner makes the staff installer from it, and a staff installer carries
; the owner's passcodes at its end, which the program takes over when it starts (sheetflat/core/access.py)
Source: "{srcexe}"; DestDir: "{app}\setup"; DestName: "SheetFlat-Setup.exe"; Flags: external ignoreversion

[Icons]
Name: "{autoprograms}\SheetFlat"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\desktop.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\sheetflat.ico"; Comment: "{cm:Tagline}"
Name: "{autodesktop}\SheetFlat"; Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\desktop.py"""; WorkingDir: "{app}\app"; IconFilename: "{app}\sheetflat.ico"; Comment: "{cm:Tagline}"; Tasks: desktopicon

[Run]
Filename: "{app}\python\pythonw.exe"; Parameters: "-s ""{app}\app\desktop.py"""; WorkingDir: "{app}\app"; Description: "{cm:RunNow}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; files Python writes while running (__pycache__) and the window profile; data stays
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{localappdata}\SheetFlat\window*"
Type: files; Name: "{localappdata}\SheetFlat\sheetflat.log"
