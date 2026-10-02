# Authorized test VM only: install a real network application and production host.
param([Parameter(Mandatory=$true)][string]$PairCode)
$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$root='C:\AgentPairNetworkTrial'
New-Item -ItemType Directory -Force -Path $root | Out-Null
Start-Transcript -Path (Join-Path $root 'trial.log') -Force | Out-Null
try {
    $installer=Join-Path $root 'pair-setup.exe'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://50.118.187.180/downloads/AgentPair-Windows-Setup-0.1.0.exe' -OutFile $installer
    $p=Start-Process $installer -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\AgentPairNetworkTrial\Pair"' -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw 'Pair install failed' }
    $firefoxInstaller=Join-Path $root 'firefox-setup.exe'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://download.mozilla.org/?product=firefox-latest-ssl&os=win64&lang=en-US' -OutFile $firefoxInstaller
    if ((Get-AuthenticodeSignature $firefoxInstaller).Status -ne 'Valid') { throw 'Firefox installer signature invalid' }
    $p=Start-Process $firefoxInstaller -ArgumentList '-ms' -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw 'Firefox install failed' }
    $exe='C:\Program Files\Mozilla Firefox\firefox.exe'
    if (-not (Test-Path $exe)) { throw 'Firefox executable missing' }
    Start-Process $exe -ArgumentList '-new-window https://www.mozilla.org/en-US/firefox/releases/'
    Start-Sleep -Seconds 4
    $collector='C:\AgentPairNetworkTrial\Pair\agentpair-windows.ps1'
    & $collector -Server 'https://50.118.187.180/' -PairCode $PairCode -Once
    if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw 'Enrollment/report failed' }
    $arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$collector+'" -Server https://50.118.187.180/'
    Start-Process powershell.exe -ArgumentList $arguments -RedirectStandardOutput (Join-Path $root 'collector.log') -RedirectStandardError (Join-Path $root 'collector-error.log')
    'APPLICATION_AND_PAIR_READY' | Set-Content (Join-Path $root 'ready.txt')
    Start-Process 'C:\AgentPairNetworkTrial\Pair\AgentPairWindows.exe'
} finally { Stop-Transcript | Out-Null }
