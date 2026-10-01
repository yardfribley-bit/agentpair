# Trusted server recipe only. No caller-supplied commands or installer arguments.
function Invoke-SoftwareInstall($recipe, $report) {
    $ErrorActionPreference='Stop'
    if($recipe.platform -ne 'Windows' -or $recipe.installer -notin @('inno','msi') -or
       $recipe.sha256 -notmatch '^[0-9a-f]{64}$') {throw 'Invalid installation recipe'}
    $url=[Uri]$recipe.url
    if($url.Scheme -ne 'https' -or $url.UserInfo -or $url.Fragment) {throw 'Trusted HTTPS installer required'}
    $findInstalled={
        @(Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
            Where-Object {$_.DisplayName -eq $recipe.displayName -and $_.DisplayVersion -eq $recipe.version})
    }
    $found=& $findInstalled
    if($found.Count -gt 0) {
        return @{state='completed';summary='指定软件版本已存在，未重复安装';evidence=@{
            softwareId=$recipe.id;verified=$true;installedVersion=$recipe.version;sha256=$recipe.sha256;reused=$true}}
    }
    $dir=Join-Path $env:LOCALAPPDATA ('AgentPair\software-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $extension=if($recipe.installer -eq 'msi'){'.msi'}else{'.exe'}
    $file=Join-Path $dir ('setup'+$extension);$log=Join-Path $dir 'install.log'
    try {
        & $report @{state='running';summary='正在下载安装包';stage='download'}
        $client=New-Object Net.WebClient
        try {
            $download=$client.DownloadFileTaskAsync($url,$file);$started=Get-Date
            while(-not $download.IsCompleted) {
                Start-Sleep -Seconds 10
                & $report @{state='running';summary='正在下载安装包';stage='download';downloadBytes=if(Test-Path $file){(Get-Item $file).Length}else{0}}
                if(((Get-Date)-$started).TotalSeconds -gt 600){$client.CancelAsync();throw 'Download timed out'}
            }
            try {$download.GetAwaiter().GetResult()} catch {
                if(-not $recipe.officialUrl){throw}
                $official=[Uri]$recipe.officialUrl
                if($official.Scheme -ne 'https' -or $official.UserInfo -or $official.Fragment){throw 'Invalid official fallback'}
                & $report @{state='running';summary='缓存不可达，回退官方来源';stage='download'}
                $fallback=$client.DownloadFileTaskAsync($official,$file)
                if(-not $fallback.Wait(600000)){$client.CancelAsync();throw 'Official download timed out'}
                $fallback.GetAwaiter().GetResult()
            }
        } finally {$client.Dispose()}
        $hash=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
        if($hash -ne $recipe.sha256) {throw 'Installer SHA256 mismatch; not executed'}
        & $report @{state='running';summary='下载哈希验证通过，正在安装';stage='install';sha256=$hash}
        if($recipe.installer -eq 'msi') {
            $child=Start-Process msiexec.exe -ArgumentList @('/i',('"'+$file+'"'),'/qn','/norestart','/L*v',('"'+$log+'"')) -PassThru
        } else {
            $child=Start-Process $file -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="'+$log+'"')) -PassThru
        }
        $started=Get-Date
        while(-not $child.WaitForExit(10000)) {
            & $report @{state='running';summary='安装程序正在执行';stage='install';
                log=if(Test-Path $log){[string]((Get-Content $log -Tail 25)-join "`n")}else{''}}
            if(((Get-Date)-$started).TotalSeconds -gt 600) {throw 'Installer still running after deadline; manual reconciliation required'}
        }
        if($child.ExitCode -notin @(0,3010)) {throw ('Installer failed: '+$child.ExitCode)}
        $found=& $findInstalled
        if($found.Count -eq 0) {throw 'Installation returned success but exact installed version was not found'}
        return @{state='completed';summary='安装及版本验证通过；应用启动/账号登录仍需单独验证';evidence=@{
            softwareId=$recipe.id;verified=$true;installedVersion=$recipe.version;sha256=$hash;
            rebootRequired=($child.ExitCode -eq 3010);launchVerified=$false;logPath=$log}}
    } catch {
        return @{state='failed';summary=[string]$_.Exception.Message;evidence=@{softwareId=$recipe.id;logPath=$log}}
    }
}
