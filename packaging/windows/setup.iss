#define AppVersion "0.12.0"
[Setup]
AppId=LocalAssessmentAI
AppName=Yerel Envanter
AppVersion={#AppVersion}
DefaultDirName={localappdata}\LocalAssessmentAI\app
PrivilegesRequired=lowest
OutputBaseFilename=YerelEnvanter-{#AppVersion}-windows-unverified
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\YerelEnvanter.exe
[Files]
Source: "..\..\dist\YerelEnvanter\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{userprograms}\Yerel Envanter"; Filename: "{app}\YerelEnvanter.exe"
; Kullanıcı verisi ve model deposu için UninstallDelete kuralı yoktur.
; Windows ağ yalıtımı doğrulanana kadar uygulamadaki güvenli engel korunur.
