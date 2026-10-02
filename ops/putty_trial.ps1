param([Parameter(Mandatory=$true)][string]$PairCode)
$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$root='C:\AgentPairNetworkTrial'
New-Item -ItemType Directory -Force -Path $root | Out-Null
$msi=Join-Path $root 'putty.msi'
Invoke-WebRequest -UseBasicParsing 'https://the.earth.li/~sgtatham/putty/0.85/w64/putty-64bit-0.85-installer.msi' -OutFile $msi
if ((Get-FileHash $msi -Algorithm SHA256).Hash -ne '2213ebacf962b709411d654a40087508a694756bc4e41c5a1c24ad86596b2511') { throw 'PuTTY checksum mismatch' }
$p=Start-Process msiexec.exe -ArgumentList ('/i "'+$msi+'" /qn /norestart') -Wait -PassThru
if ($p.ExitCode -notin @(0,3010)) { throw 'PuTTY installation failed' }
$exe='C:\Program Files\PuTTY\putty.exe'
if (-not (Test-Path $exe)) { throw 'PuTTY executable missing' }
$collector=Join-Path $root 'agentpair-windows.ps1'
Invoke-WebRequest -UseBasicParsing 'https://50.118.187.180/downloads/agentpair-windows.ps1' -OutFile $collector
$errors=$null;$tokens=$null
[Management.Automation.Language.Parser]::ParseFile($collector,[ref]$tokens,[ref]$errors) | Out-Null
if ($errors.Count) { throw 'Collector parse failed' }
Start-Process $exe -ArgumentList '-ssh 50.118.187.180 -P 22 -l agentpair'
$arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$collector+'" -Server https://50.118.187.180/ -PairCode "'+$PairCode+'" -AnalyzeProcess putty.exe -Goal "Describe observed application identity and live network connections. Distinguish evidence from limitations. Return an understandable analysis."'
Start-Process powershell.exe -ArgumentList $arguments -RedirectStandardOutput (Join-Path $root 'collector.log') -RedirectStandardError (Join-Path $root 'collector-error.log')
'PUTTY_AND_COLLECTOR_STARTED' | Set-Content (Join-Path $root 'ready.txt')
Clear-Host
Write-Host 'PuTTY installed; collector started.'
