[Setup]
AppId=AgentPairWindowsConnector
AppName=AppLens 应用透镜
AppVersion=0.1.0
AppPublisher=AgentPair
DefaultDirName={localappdata}\Programs\AgentPair Windows
DefaultGroupName=AgentPair
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=AgentPair-Windows-Setup-0.1.0
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\AgentPairWindows.exe
[Files]
Source: "build\AgentPairWindows.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\agentpair\web_assets\agentpair-windows.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "workbuddy-context.ps1"; DestDir: "{app}"; Flags: ignoreversion
[Icons]
Name: "{group}\AgentPair Windows"; Filename: "{app}\AgentPairWindows.exe"
Name: "{autodesktop}\AgentPair Windows"; Filename: "{app}\AgentPairWindows.exe"
[Run]
Filename: "{app}\AgentPairWindows.exe"; Description: "Launch AgentPair Windows"; Flags: nowait postinstall skipifsilent
