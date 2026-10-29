; E.A.G.L.E. のインストーラ定義(Inno Setup 6)
; 作成: scripts\build_installer.ps1(ISCC.exe /DAppVersion=x.y.z packaging\installer.iss)
; 入力: dist\EAGLE\(scripts\build_desktop.ps1 で作った exe 一式)
; 出力: dist\EAGLE-Setup-<版>.exe

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppName "E.A.G.L.E."
#define AppPublisher "saltea-Giraffe"
#define AppURL "https://github.com/saltea-Giraffe/E.A.G.L.E."

[Setup]
AppId={{F859AFBE-0502-4ED1-8D93-63BEA6119347}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\EAGLE
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; 既定は管理者権限なし(自分だけ)。ダイアログで「すべてのユーザー」も選べる
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
LicenseFile=..\LICENSE
SetupIconFile=eagle.ico
UninstallDisplayIcon={app}\EAGLE.exe
UninstallDisplayName={#AppName}
OutputDir=..\dist
OutputBaseFilename=EAGLE-Setup-{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "japanese"; MessagesFile: "compiler:Languages\Japanese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
japanese.ServerShortcut=E.A.G.L.E. サーバー(Webアプリ版)
english.ServerShortcut=E.A.G.L.E. Server (web edition)
japanese.ReadmeShortcut=E.A.G.L.E. の説明
english.ReadmeShortcut=E.A.G.L.E. README
japanese.VCPreparing=Visual C++ ランタイムを準備しています(読み取りエンジンに必要です)...
english.VCPreparing=Preparing the Visual C++ runtime (required by the reading engine)...
japanese.VCFailed=Visual C++ ランタイムを自動で導入できませんでした。%n読み取りエンジンを使うには、Microsoft の Web サイトから「Visual C++ 再頒布可能パッケージ (x64)」を導入してください。%n%n%1
english.VCFailed=The Visual C++ runtime could not be installed automatically.%nTo use the reading engine, install "Visual C++ Redistributable (x64)" from Microsoft.%n%n%1
japanese.FirstRun=初回起動時に、読み取りエンジン(AI モデル・数 GB)のダウンロードを案内します。
english.FirstRun=On first launch, the app will guide you to download the reading engine (AI model, several GB).

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\EAGLE\*"; DestDir: "{app}"; Excludes: "portable.txt"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\EAGLE.exe"
Name: "{autoprograms}\{cm:ServerShortcut}"; Filename: "{app}\EAGLE-Server.exe"; Parameters: "server"
Name: "{autoprograms}\{cm:ReadmeShortcut}"; Filename: "{app}\README.md"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\EAGLE.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\EAGLE.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  VCRedistURL = 'https://aka.ms/vs/17/release/vc_redist.x64.exe';
  VCKey = 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64';

{ llama.cpp は新しい Visual C++ ランタイム(14.40 以降)を必要とする }
function VCRuntimeOK: Boolean;
var
  Installed, Major, Minor: Cardinal;
begin
  Result := False;
  if RegQueryDWordValue(HKLM64, VCKey, 'Installed', Installed) and (Installed = 1) and
     RegQueryDWordValue(HKLM64, VCKey, 'Major', Major) and
     RegQueryDWordValue(HKLM64, VCKey, 'Minor', Minor) then
    Result := (Major > 14) or ((Major = 14) and (Minor >= 40));
end;

procedure InstallVCRuntime;
var
  ResultCode: Integer;
begin
  WizardForm.StatusLabel.Caption := CustomMessage('VCPreparing');
  try
    DownloadTemporaryFile(VCRedistURL, 'vc_redist.x64.exe', '', nil);
    { 管理者権限が必要なため、権限の確認画面を出して実行する(既に新しい版があれば何もしない) }
    if not ShellExec('runas', ExpandConstant('{tmp}\vc_redist.x64.exe'), '/install /passive /norestart', '',
                     SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      SuppressibleMsgBox(FmtMessage(CustomMessage('VCFailed'), [SysErrorMessage(ResultCode)]), mbInformation, MB_OK, IDOK)
    else if (ResultCode <> 0) and (ResultCode <> 1638) and (ResultCode <> 3010) then
      SuppressibleMsgBox(FmtMessage(CustomMessage('VCFailed'), ['exit code ' + IntToStr(ResultCode)]), mbInformation, MB_OK, IDOK);
  except
    SuppressibleMsgBox(FmtMessage(CustomMessage('VCFailed'), [GetExceptionMessage]), mbInformation, MB_OK, IDOK);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and (not VCRuntimeOK) then
    InstallVCRuntime;
end;

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpFinished then
    WizardForm.FinishedLabel.Caption := WizardForm.FinishedLabel.Caption + #13#10#13#10 + CustomMessage('FirstRun');
end;
