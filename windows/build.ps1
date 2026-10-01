$ErrorActionPreference='Stop'
Set-Location $PSScriptRoot
New-Item -ItemType Directory -Force build | Out-Null
& (Join-Path $PSScriptRoot 'prepare-capture.ps1')
# Windows PowerShell 5.1 reads BOM-less UTF-8 as ANSI. Normalize the shipped
# host before parsing/packaging so Chinese messages cannot corrupt syntax.
$hostPath=Join-Path $PSScriptRoot '..\agentpair\web_assets\agentpair-windows.ps1'
$utf8Bom=New-Object System.Text.UTF8Encoding($true)
[IO.File]::WriteAllText($hostPath,[IO.File]::ReadAllText($hostPath,[Text.Encoding]::UTF8),$utf8Bom)
$compiler = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $compiler /nologo /codepage:65001 /target:winexe /platform:x64 /out:build\AgentPairWindows.exe /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll AgentPairWindows.cs
if ($LASTEXITCODE -ne 0) { throw 'C# build failed' }
$tokens=$null; $errors=$null
[System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '..\agentpair\web_assets\agentpair-windows.ps1'),[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count) { $errors | Format-List * | Out-String | Write-Host; throw 'Collector PowerShell syntax errors' }
$contextPath=Join-Path $PSScriptRoot 'workbuddy-context.ps1'
[IO.File]::WriteAllText($contextPath,[IO.File]::ReadAllText($contextPath,[Text.Encoding]::UTF8),$utf8Bom)
[System.Management.Automation.Language.Parser]::ParseFile($contextPath,[ref]$tokens,[ref]$errors) | Out-Null
if($errors.Count){throw 'WorkBuddy context collector syntax errors'}
$networkPath=Join-Path $PSScriptRoot 'workbuddy-network.ps1'
[IO.File]::WriteAllText($networkPath,[IO.File]::ReadAllText($networkPath,[Text.Encoding]::UTF8),$utf8Bom)
[System.Management.Automation.Language.Parser]::ParseFile($networkPath,[ref]$tokens,[ref]$errors)|Out-Null
if($errors.Count){throw 'WorkBuddy network capture syntax errors'}
& (Join-Path $PSScriptRoot 'test-context.ps1')
if(Test-Path (Join-Path $PSScriptRoot 'test-modules.ps1')){& (Join-Path $PSScriptRoot 'test-modules.ps1')}
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
