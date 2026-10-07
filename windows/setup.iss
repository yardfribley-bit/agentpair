[Setup]
AppId=AgentPairWindowsConnector
AppName=AppLens 应用透镜
AppVersion=0.1.0
AppPublisher=AgentPair
SetupIconFile=..\assets\applens\applens.ico
DefaultDirName={localappdata}\Programs\AgentPair Windows
DefaultGroupName=AgentPair
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=AgentPair-Windows-Setup-0.1.0
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\AgentPairWindows.exe
[Files]
Source: "..\client-ui\context.js"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\macos\collector.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\assets\applens\applens.svg"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\client-ui\capture.html"; DestDir: "{app}"; Flags: ignoreversion
Source: "build\AgentPairWindows.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\agentpair\web_assets\agentpair-windows.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "workbuddy-context.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "software-install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "workbuddy-network.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\macos\workbuddy_network_capture.py"; DestDir: "{app}"; DestName: "workbuddy-network-capture.py"; Flags: ignoreversion
Source: "build\capture\mitmdump.exe"; DestDir: "{app}\capture"; Flags: ignoreversion
Source: "build\capture\LICENSE.txt"; DestDir: "{app}\capture"; Flags: ignoreversion
[Icons]
Name: "{group}\AgentPair Windows"; Filename: "{app}\AgentPairWindows.exe"
Name: "{autodesktop}\AgentPair Windows"; Filename: "{app}\AgentPairWindows.exe"
[Run]
Filename: "{app}\AgentPairWindows.exe"; Description: "Launch AgentPair Windows"; Flags: nowait postinstall skipifsilent
