$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force build | Out-Null
$compiler = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $compiler /nologo /target:winexe /platform:x64 /out:build\AgentPairWindows.exe /reference:System.Windows.Forms.dll /reference:System.Drawing.dll AgentPairWindows.cs
if ($LASTEXITCODE -ne 0) { throw 'C# build failed' }
$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '..\agentpair\web_assets\agentpair-windows.ps1'),[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count) {throw 'Collector PowerShell syntax errors'}
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (!(Test-Path $iscc)) {throw 'Inno Setup 6 is required on the build machine'}
& $iscc setup.iss
if ($LASTEXITCODE -ne 0) {throw 'Installer build failed'}
$install=Join-Path $env:TEMP 'AgentPairInstallerAcceptance'
$p=Start-Process -FilePath (Resolve-Path 'dist\AgentPair-Windows-Setup-0.1.0.exe') -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/DIR="'+$install+'"')) -Wait -PassThru
if($p.ExitCode -ne 0) {throw 'Installer acceptance failed'}
$p=Start-Process (Join-Path $install 'AgentPairWindows.exe') -ArgumentList '--self-test' -Wait -PassThru
if($p.ExitCode -ne 0) {throw 'Installed binary self-test failed'}
$p=Start-Process (Join-Path $install 'AgentPairWindows.exe') -PassThru
Start-Sleep -Seconds 3
if($p.HasExited) {throw 'Installed GUI exited unexpectedly'}
Stop-Process -Id $p.Id
Get-FileHash 'dist\AgentPair-Windows-Setup-0.1.0.exe' -Algorithm SHA256 | Format-List
