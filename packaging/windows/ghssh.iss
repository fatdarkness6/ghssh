; Per-user installer. Keep AppId stable so upgrades replace this installation.
#ifndef MyAppVersion
  #error MyAppVersion must be supplied by the build workflow
#endif

[Setup]
AppId={{C809FCE4-6641-4282-B178-A74B596843CD}
AppName=ghssh
AppVersion={#MyAppVersion}
AppPublisher=fatdarkness6
AppPublisherURL=https://github.com/fatdarkness6/ghssh
AppSupportURL=https://github.com/fatdarkness6/ghssh/issues
AppUpdatesURL=https://github.com/fatdarkness6/ghssh/releases
DefaultDirName={localappdata}\Programs\ghssh
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=ghssh-{#MyAppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ChangesEnvironment=yes
UninstallDisplayName=ghssh
UninstallDisplayIcon={app}\ghssh.exe

[Tasks]
Name: "addtopath"; Description: "Add ghssh to my user PATH (recommended)"; Flags: checkedonce

[Files]
Source: "..\..\dist\ghssh\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Messages]
FinishedLabel=ghssh is installed. Open a NEW CMD or PowerShell window and run ghssh --help.%n%nGit for Windows and Windows OpenSSH Client must be installed. Your SSH keys and backups are kept in your user profile and are preserved when uninstalling.

[Code]
const
  EnvironmentKey = 'Environment';
  SettingsKey = 'Software\ghssh';

function NormalizePathEntry(Value: String): String;
begin
  Value := Trim(Value);
  if (Length(Value) >= 2) and (Value[1] = '"') and (Value[Length(Value)] = '"') then
    Value := Copy(Value, 2, Length(Value) - 2);
  while (Length(Value) > 3) and (Value[Length(Value)] = '\') do
    Delete(Value, Length(Value), 1);
  Result := Lowercase(Value);
end;

function HasEntry(PathValue, Entry: String): Boolean;
var
  Token: String;
  Separator: Integer;
begin
  Result := False;
  repeat
    Separator := Pos(';', PathValue);
    if Separator = 0 then Separator := Length(PathValue) + 1;
    Token := Copy(PathValue, 1, Separator - 1);
    if NormalizePathEntry(Token) = NormalizePathEntry(Entry) then begin
      Result := True;
      Exit;
    end;
    Delete(PathValue, 1, Separator);
  until PathValue = '';
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if (CurPageID = wpSelectDir) and (Pos(';', WizardDirValue) > 0) then begin
    MsgBox('Choose a folder without semicolons so it can be added to PATH.', mbError, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  UserPath, AppDirectory: String;
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then begin
    AppDirectory := ExpandConstant('{app}');
    RegQueryStringValue(HKCU, EnvironmentKey, 'Path', UserPath);
    if not HasEntry(UserPath, AppDirectory) then begin
      if (UserPath <> '') and (UserPath[Length(UserPath)] <> ';') then
        UserPath := UserPath + ';';
      if RegWriteExpandStringValue(HKCU, EnvironmentKey, 'Path', UserPath + AppDirectory) then
        RegWriteStringValue(HKCU, SettingsKey, 'AddedPath', AppDirectory)
      else
        MsgBox('Could not update your user PATH. Add ' + AppDirectory + ' manually.', mbError, MB_OK);
    end;
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  UserPath, AddedPath, Token, UpdatedPath: String;
  Separator: Integer;
  First: Boolean;
begin
  if (CurUninstallStep = usPostUninstall) and
     RegQueryStringValue(HKCU, SettingsKey, 'AddedPath', AddedPath) and
     (NormalizePathEntry(AddedPath) = NormalizePathEntry(ExpandConstant('{app}'))) then begin
    if RegQueryStringValue(HKCU, EnvironmentKey, 'Path', UserPath) then begin
      UpdatedPath := '';
      First := True;
      repeat
        Separator := Pos(';', UserPath);
        if Separator = 0 then Separator := Length(UserPath) + 1;
        Token := Copy(UserPath, 1, Separator - 1);
        if NormalizePathEntry(Token) <> NormalizePathEntry(AddedPath) then begin
          if not First then UpdatedPath := UpdatedPath + ';';
          UpdatedPath := UpdatedPath + Token;
          First := False;
        end;
        Delete(UserPath, 1, Separator);
      until UserPath = '';
      RegWriteExpandStringValue(HKCU, EnvironmentKey, 'Path', UpdatedPath);
    end;
    RegDeleteValue(HKCU, SettingsKey, 'AddedPath');
    RegDeleteKeyIfEmpty(HKCU, SettingsKey);
  end;
end;
