# App-scoped capture coordinator. No system proxy, trust-store edits or TLS bypass.
param([ValidateSet('Enable','Restore')][string]$Action='Enable',[string]$WorkBuddyExe)
$ErrorActionPreference='Stop'
$folder=Join-Path $env:LOCALAPPDATA 'AgentPair'
New-Item -ItemType Directory -Force $folder | Out-Null
$stopFile=Join-Path $folder 'capture-stop'
$gate=Join-Path $folder 'capture-enabled'
$backup=Join-Path $folder 'workbuddy-proxy-original.json'
$settings=Join-Path $env:USERPROFILE '.workbuddy\settings.json'
$url='http://127.0.0.1:18893'
function Restore-Proxy {
    if(!(Test-Path $backup)){return}
    $original=Get-Content $backup -Raw -Encoding UTF8|ConvertFrom-Json
    $current=if(Test-Path $settings){Get-Content $settings -Raw -Encoding UTF8|ConvertFrom-Json}else{New-Object PSObject}
    # Never overwrite a proxy the user changed during collection.
    if($current.'http.proxy' -ne $url){return}
    foreach($name in @('http.proxy','http.proxySupport')){
        $current.PSObject.Properties.Remove($name)
        if($original.$name.present){$current|Add-Member -NotePropertyName $name -NotePropertyValue $original.$name.value}
    }
    [IO.File]::WriteAllText($settings,($current|ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false))
    Remove-Item $backup -Force
}
if($Action -eq 'Restore'){[IO.File]::WriteAllText($stopFile,'stop');Restore-Proxy;exit}
$proxy=$null;$mutex=New-Object Threading.Mutex($false,'Local\AppLensWorkBuddyCapture');$locked=$false
try{
    $locked=$mutex.WaitOne(0);if(!$locked){throw '完整正文采集已运行'}
    $exe=Join-Path $PSScriptRoot 'capture\mitmdump.exe'
    if(!(Test-Path $exe)){throw '安装包缺少采集运行时，请重新安装最新 AppLens'}
    if(!$WorkBuddyExe){
        $running=@(Get-Process -Name WorkBuddy -ErrorAction SilentlyContinue|Where-Object {$_.Path})
        if($running.Count){$WorkBuddyExe=$running[0].Path}
        else{foreach($candidate in @((Join-Path $env:LOCALAPPDATA 'Programs\WorkBuddy\WorkBuddy.exe'),(Join-Path $env:LOCALAPPDATA 'WorkBuddy\WorkBuddy.exe'))){if(Test-Path $candidate){$WorkBuddyExe=$candidate;break}}}
    }
    if(!$WorkBuddyExe -or !(Test-Path $WorkBuddyExe) -or [IO.Path]::GetFileName($WorkBuddyExe) -ine 'WorkBuddy.exe'){throw '找不到 WorkBuddy.exe，请先启动 WorkBuddy 再启用采集'}
    if(Get-NetTCPConnection -LocalPort 18893 -State Listen -ErrorAction SilentlyContinue){throw '采集端口已占用，不接管未知代理'}
    Remove-Item $stopFile -Force -ErrorAction SilentlyContinue
    [IO.File]::WriteAllText($gate,'enabled')
    $caFolder=Join-Path $folder 'capture-ca'
    New-Item -ItemType Directory -Force $caFolder | Out-Null
    $env:APPLENS_WORKBUDDY_NETWORK_JSONL=Join-Path $folder 'workbuddy-network.jsonl'
    $env:APPLENS_CAPTURE_ENABLED_FILE=$gate
    $args='--listen-host 127.0.0.1 --listen-port 18893 --set connection_strategy=lazy --set confdir="'+$caFolder+'" --scripts "'+(Join-Path $PSScriptRoot 'workbuddy-network-capture.py')+'"'
    $proxy=Start-Process $exe -ArgumentList $args -PassThru -WindowStyle Hidden -RedirectStandardOutput (Join-Path $folder 'capture.log') -RedirectStandardError (Join-Path $folder 'capture-error.log')
    $ca=Join-Path $caFolder 'mitmproxy-ca-cert.pem'
    $ready=$false
    for($i=0;$i -lt 120;$i++){
        if($proxy.HasExited){throw '采集代理启动失败，查看本机 capture-error.log'}
        if((Test-Path $ca) -and (Get-NetTCPConnection -LocalPort 18893 -State Listen -ErrorAction SilentlyContinue)){$ready=$true;break}
        Start-Sleep -Milliseconds 500
    }
    if(!$ready){throw '采集代理启动超时'}
    $current=if(Test-Path $settings){Get-Content $settings -Raw -Encoding UTF8|ConvertFrom-Json}else{New-Object PSObject}
    if(!(Test-Path $backup)){
        $saved=@{};foreach($name in @('http.proxy','http.proxySupport')){$saved[$name]=@{present=($null -ne $current.PSObject.Properties[$name]);value=$current.$name}}
        [IO.File]::WriteAllText($backup,($saved|ConvertTo-Json -Depth 5),[Text.UTF8Encoding]::new($false))
    }
    # Graceful close; never force-kill another application.
    foreach($p in @(Get-Process -Name WorkBuddy -ErrorAction SilentlyContinue|Where-Object {$_.Path -eq $WorkBuddyExe -and $_.MainWindowHandle -ne 0})){$null=$p.CloseMainWindow();if(!$p.WaitForExit(10000)){throw 'WorkBuddy 未退出，请保存工作并手动关闭后重试'}}
    if(@(Get-Process -Name WorkBuddy -ErrorAction SilentlyContinue|Where-Object {$_.Path -eq $WorkBuddyExe}).Count){throw 'WorkBuddy 后台进程仍在运行，请手动退出后重试'}
    $current.PSObject.Properties.Remove('http.proxy');$current.PSObject.Properties.Remove('http.proxySupport')
    $current|Add-Member -NotePropertyName 'http.proxy' -NotePropertyValue $url
    New-Item -ItemType Directory -Force (Split-Path $settings) | Out-Null
    [IO.File]::WriteAllText($settings,($current|ConvertTo-Json -Depth 30),[Text.UTF8Encoding]::new($false))
    $env:HTTP_PROXY=$url;$env:HTTPS_PROXY=$url;$env:NODE_EXTRA_CA_CERTS=$ca
    Start-Process $WorkBuddyExe | Out-Null
    Write-Output '完整正文代理已启动；等待真实模型请求，尚不代表采集成功'
    while(!(Test-Path $stopFile)){
        if($proxy.HasExited){throw '采集代理已退出，已恢复原代理配置'}
        Start-Sleep -Milliseconds 500
    }
}catch{Write-Output $_.Exception.Message;exit 1}
finally{
    if($locked){
    Remove-Item $gate -Force -ErrorAction SilentlyContinue
    Restore-Proxy
    # A running WorkBuddy still remembers its proxy. Gracefully restart after
    # restoration before stopping forwarding; if closing fails keep forwarding,
    # with recording disabled, instead of silently breaking the user's network.
    $remaining=@(Get-Process -Name WorkBuddy -ErrorAction SilentlyContinue|Where-Object {$_.Path -eq $WorkBuddyExe})
    $restart=($remaining.Count -gt 0 -and (Test-Path $stopFile))
    if($restart){foreach($p in $remaining|Where-Object {$_.MainWindowHandle -ne 0}){$null=$p.CloseMainWindow();$null=$p.WaitForExit(10000)}}
    $remaining=@(Get-Process -Name WorkBuddy -ErrorAction SilentlyContinue|Where-Object {$_.Path -eq $WorkBuddyExe})
    if(!$remaining.Count){
        if($proxy -and !$proxy.HasExited){Stop-Process -Id $proxy.Id -ErrorAction SilentlyContinue}
        if($restart){foreach($name in @('HTTP_PROXY','HTTPS_PROXY','NODE_EXTRA_CA_CERTS','APPLENS_WORKBUDDY_NETWORK_JSONL','APPLENS_CAPTURE_ENABLED_FILE')){Remove-Item ('Env:\'+$name) -ErrorAction SilentlyContinue};Start-Process $WorkBuddyExe|Out-Null}
    }else{Write-Output '已停止记录并恢复配置；WorkBuddy 未退出，代理仅转发。请退出 WorkBuddy 后关闭采集代理。'}
    $mutex.ReleaseMutex()
    }
    $mutex.Dispose()
}
