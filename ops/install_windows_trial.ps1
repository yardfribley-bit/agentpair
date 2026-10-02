param([string]$PairCode,[string]$InstallerSHA256)
$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$dir='C:\AgentPairTrial';New-Item -ItemType Directory -Force $dir | Out-Null
function Stage($name){$name | Set-Content "$dir\install-stage.txt"}
try {
 Stage 'workbuddy_download'
 $url='https://download.codebuddy.cn/workbuddy/saas/win32-x64-user/WorkBuddy-win32-x64-user-5.1.2.30975940-b9604175.exe'
 & curl.exe -fL --max-time 360 -o "$dir\workbuddy-setup.exe" $url
 if($LASTEXITCODE -ne 0){throw 'Official WorkBuddy download failed'}
 $signature=Get-AuthenticodeSignature "$dir\workbuddy-setup.exe"
 if($signature.Status -ne 'Valid'){throw ('WorkBuddy installer signature invalid: '+$signature.Status)}
 Stage 'workbuddy_install'
 $p=Start-Process "$dir\workbuddy-setup.exe" -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /MERGETASKS=!runcode' -Wait -PassThru
 if($p.ExitCode -ne 0){throw ('WorkBuddy installer failed '+$p.ExitCode)}
 Stage 'applens_download'
 Invoke-WebRequest -UseBasicParsing -Uri 'https://50.118.187.180/downloads/AgentPair-Windows-Setup-0.1.0.exe' -OutFile "$dir\applens-setup.exe"
 if((Get-FileHash "$dir\applens-setup.exe" -Algorithm SHA256).Hash -ne $InstallerSHA256){throw 'AppLens installer hash mismatch'}
 Stage 'applens_install'
 $p=Start-Process "$dir\applens-setup.exe" -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="C:\AgentPairTrial\App"' -Wait -PassThru
 if($p.ExitCode -ne 0){throw 'AppLens installer failed'}
 $p=Start-Process "$dir\App\AgentPairWindows.exe" -ArgumentList '--self-test' -Wait -PassThru
 if($p.ExitCode -ne 0){throw 'AppLens self-test failed'}
 Stage 'applens_pair'
 $p=Start-Process "$dir\App\AgentPairWindows.exe" -ArgumentList @('--once','https://50.118.187.180/',$PairCode) -Wait -PassThru
 if($p.ExitCode -ne 0){throw 'AppLens pairing/inventory failed'}
 Stage 'installed_paired_inventory_verified'
} catch {('FAILED: '+$_.Exception.Message) | Set-Content "$dir\install-stage.txt";throw}
