function Write-CaptureEvent($folder,$call,$phase,$deviceId) {
    $record=@{};foreach($name in $call.Keys){if($name -ne 'body'){$record[$name]=$call[$name]}}
    $record.preview=$call.body.Substring(0,[Math]::Min(1200,$call.body.Length))
    $record.bodyBytes=[Text.Encoding]::UTF8.GetByteCount($call.body)
    $record.receipt=($phase -eq 'received')
    $event=@{server=$Server;deviceId=$deviceId;id=$call.id;bodySHA256=$call.bodySHA256;captureBody=$call.body;phase=$phase;record=$record;updatedAt=(Get-Date).ToString('o')}
    $path=Join-Path $folder 'capture-event.json';$temp=$path+'.tmp'
    [IO.File]::WriteAllText($temp,($event|ConvertTo-Json -Depth 6 -Compress),[Text.Encoding]::UTF8)
    Move-Item $temp $path -Force
}
# Generation input only. No independent tool events, reply collection or request rewriting.
function Get-ContextDigest([string]$text) {
    $sha=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($text)))).Replace('-','').ToLowerInvariant() } finally {$sha.Dispose()}
}
function Read-WorkBuddyLines([string]$path) {
    $stream=$null;$reader=$null
    try {
        $stream=[IO.File]::Open($path,[IO.FileMode]::Open,[IO.FileAccess]::Read,
            ([IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete))
        $reader=New-Object IO.StreamReader($stream)
        while(-not $reader.EndOfStream){$reader.ReadLine()}
    } catch [IO.IOException] {
        # A live WorkBuddy log may briefly deny reads. Continue with the
        # independent network capture; retry this log on the next poll.
        return
    } finally {
        if($reader){$reader.Dispose()}elseif($stream){$stream.Dispose()}
    }
}
function Get-WorkBuddyNetworkContext($folder,$since) {
    $path=Join-Path $folder 'workbuddy-network.jsonl'
    if(!(Test-Path $path)){return @()}
    $result=@{}
    foreach($line in @(Read-WorkBuddyLines $path)){
        try{
            $r=$line|ConvertFrom-Json
            if($r.host -ne 'copilot.tencent.com' -or $r.path -notin @('/v1/chat/completions','/v2/chat/completions','/v3/chat/completions')){continue}
            $date=[DateTimeOffset]::Parse($r.observedAt);if($date.LocalDateTime -lt $since){continue}
            $raw=[Convert]::FromBase64String($r.requestBodyBase64);if($raw.Length -gt 1000000){continue}
            $body=([Text.UTF8Encoding]::new($false,$true)).GetString($raw)
            $digest=Get-ContextDigest $body;if($digest -ne $r.requestSHA256){continue}
            $parsed=$body|ConvertFrom-Json;if($parsed.messages -isnot [Array]){continue}
            $matched=(($r.declaredContentLength -is [int] -or $r.declaredContentLength -is [long]) -and $r.declaredContentLength -ge 0 -and $r.declaredContentLength -eq $r.capturedWireBodyBytes)
            $id=Get-ContextDigest ('network:'+$r.flowID)
            $result[$id]=@{id=$id;source='workbuddy_network_context';body=$body;bodySHA256=$digest;timestamp=($date.ToUnixTimeMilliseconds()/1000.0);model=$parsed.model;modelEvidence='同一 HTTP 请求体 model 字段';sessionId=('network:'+$r.flowID);sessionName=('HTTP 请求 '+([string]$r.flowID).Substring(0,[Math]::Min(8,([string]$r.flowID).Length)));truncated=$false;complete=$false;wireLengthMatched=$matched;destination=('copilot.tencent.com'+$r.path);recordStatus=if($matched){'wire_length_matched'}else{'wire_length_unknown'};recordJSONValid=$true;integrityEvidence='完整 HTTP 正文 SHA256 校验；声明/捕获长度一致不代表远端处理成功'}
        }catch{continue}
    }
    return @($result.Values)
}
function Get-WorkBuddyRequestMetadata($root,$since) {
    $rows=New-Object System.Collections.Generic.List[object]
    $logs=Join-Path $root 'logs'
    if (!(Test-Path $logs)) {return @()}
    foreach($file in @(Get-ChildItem $logs -Filter '*.log' -Recurse -File -ErrorAction SilentlyContinue | Where-Object {$_.LastWriteTime -ge $since})) {
        foreach($line in @(Read-WorkBuddyLines $file.FullName)) {
            if($line -notmatch '\[ModelProvider\]' -or ($line -notmatch 'Sending request:' -and $line -notmatch 'message-to-model-request latency')) {continue}
            $time=[regex]::Match($line,'^\[(\d+/\d+/\d+), (\d+:\d+:\d+) (AM|PM)\.(\d+)\]')
            $pidMatch=[regex]::Match($line,'\[pid=(\d+)\]');$modelMatch=[regex]::Match($line,'\bmodel(?:Name|Id)?=([A-Za-z0-9_.:/-]+)')
            if(!$time.Success -or !$pidMatch.Success -or !$modelMatch.Success){continue}
            try {$date=[DateTime]::ParseExact(($time.Groups[1].Value+' '+$time.Groups[2].Value+' '+$time.Groups[3].Value),'M/d/yyyy h:mm:ss tt',[Globalization.CultureInfo]::InvariantCulture).AddSeconds([double]('0.'+$time.Groups[4].Value))}catch{continue}
            $session=[regex]::Match($line,'\bsessionId=([A-Za-z0-9_-]+)')
            $rows.Add(@{date=$date;workerPid=[int]$pidMatch.Groups[1].Value;model=$modelMatch.Groups[1].Value;sessionId=$session.Groups[1].Value;sending=$line.Contains('Sending request:')})
        }
    }
    return $rows.ToArray()
}
function Sync-WorkBuddyContext($identity,$folder,$WorkBuddyRoot=(Join-Path $env:USERPROFILE '.workbuddy')) {
    $root=$WorkBuddyRoot;$since=(Get-Date).AddDays(-1)
    $metadata=@(Get-WorkBuddyRequestMetadata $root $since);$calls=New-Object System.Collections.Generic.List[object]
    $receiptsPath=Join-Path $folder ($key+'.context-receipts.json');$receipts=@{}
    if(Test-Path $receiptsPath){try{$saved=Get-Content $receiptsPath -Raw -Encoding UTF8 | ConvertFrom-Json;if($saved.deviceId -eq $identity.deviceId){foreach($prop in $saved.receipts.PSObject.Properties){$receipts[$prop.Name]=$prop.Value}}}catch{}}
    $traceRoot=Join-Path $root 'traces'
    foreach($file in @(Get-ChildItem $traceRoot -Filter '*.json' -Recurse -File -ErrorAction SilentlyContinue | Where-Object {$_.LastWriteTime -ge $since} | Sort-Object LastWriteTime -Descending)) {
        try{$doc=[IO.File]::ReadAllText($file.FullName,[Text.Encoding]::UTF8)|ConvertFrom-Json}catch{continue}
        foreach($span in $doc.spans){
            if($span.type -ne 'generation' -or $span.toolInput -isnot [string] -or !$span.toolInput){continue}
            try{$date=[DateTimeOffset]::Parse($span.startedAt)}catch{continue};if($date.LocalDateTime -lt $since){continue}
            $body=[string]$span.toolInput;$id=Get-ContextDigest ([string]$doc.trace.traceId+[string]$span.spanId)
            $candidates=@($metadata|Where-Object {$_.workerPid -eq $doc.trace.workerPid -and [Math]::Abs(($_.date-$date.LocalDateTime).TotalSeconds) -le 1})
            $models=@($candidates|Where-Object {$_.sending}|ForEach-Object {$_.model}|Sort-Object -Unique)
            $model=$span.model;$evidence='generation metadata'
            if(!$model){$model=if($models.Count -eq 1){$models[0]}else{$null};$evidence=if($model){'发送日志：同进程、1秒内、候选模型唯一'}else{'缺少唯一可关联的模型证据'}}
            $sessions=@($candidates|Where-Object {$_.sessionId -and $_.model -eq $model}|ForEach-Object {$_.sessionId}|Sort-Object -Unique)
            $sessionId=if($doc.trace.sessionId){$doc.trace.sessionId}elseif($sessions.Count -eq 1){$sessions[0]}else{'unknown'}
            $valid=$false;try{$parsed=$body|ConvertFrom-Json;$valid=($parsed -is [Array]) -or ($null -ne $parsed.messages)}catch{}
            $status=if($body.Length -eq 100003 -and $body.EndsWith('...')){'truncated'}elseif($valid){'parseable'}else{'unparseable'}
            $calls.Add(@{id=$id;source='workbuddy_generation_context';body=$body;bodySHA256=(Get-ContextDigest $body);timestamp=($date.ToUnixTimeMilliseconds()/1000.0);model=$model;modelEvidence=$evidence;sessionId=$sessionId;sessionName=if($sessionId -eq 'unknown'){'会话未识别'}else{'已关联会话'};truncated=($status -eq 'truncated');complete=$false;recordStatus=$status;recordJSONValid=$valid;integrityEvidence='源记录截断上限100000 UTF-16单元；JSON解析不证明网络请求完整'})
            if($calls.Count -ge 100){break}
        }
        if($calls.Count -ge 100){break}
    }
    foreach($network in @(Get-WorkBuddyNetworkContext $folder $since)){$calls.Add($network)}
    $calls=@($calls|Sort-Object timestamp -Descending|Select-Object -First 100)
    $errorMessage='';$view=New-Object System.Collections.Generic.List[object]
    $contextFolder=Join-Path $folder 'contexts';New-Item -ItemType Directory -Force $contextFolder|Out-Null
    $visibleBodies=@{}
    foreach($call in $calls){
        # Keep selected historical bodies local; the window verifies the hash before showing one.
        $bodyPath=Join-Path $contextFolder ($call.id+'-'+$call.bodySHA256+'.txt')
        $visibleBodies[[IO.Path]::GetFileName($bodyPath)]=$true
        $validBody=$false
        if(Test-Path $bodyPath){try {$validBody=((Get-ContextDigest ([IO.File]::ReadAllText($bodyPath,[Text.Encoding]::UTF8))) -eq $call.bodySHA256)}catch [IO.IOException] {}}
        if(!$validBody){[IO.File]::WriteAllText(($bodyPath+'.tmp'),$call.body,(New-Object Text.UTF8Encoding($false)));Move-Item ($bodyPath+'.tmp') $bodyPath -Force}
        $signature=$call.bodySHA256+'|'+$call.model+'|'+$call.recordStatus+'|'+$call.sessionId
        if($receipts[$call.id] -ne $signature){try{
            Write-CaptureEvent $folder $call 'queued' $identity.deviceId
            Write-CaptureEvent $folder $call 'uploading' $identity.deviceId
            $result=Invoke-Api '/api/applens/model-context' @{requests=@($call)} $identity.token
            $ack=@($result.receipts|Where-Object {$_.id -eq $call.id -and $_.bodySHA256 -eq $call.bodySHA256})
            if($ack.Count -ne 1){throw 'Receipt mismatch'};$receipts[$call.id]=$signature
            Write-CaptureEvent $folder $call 'received' $identity.deviceId
        }catch{$errorMessage='上传失败，等待重试；未确认平台接收';Write-CaptureEvent $folder $call 'failed' $identity.deviceId}}
        $summary=@{};foreach($name in $call.Keys){if($name -ne 'body'){$summary[$name]=$call[$name]}}
        $summary.preview=$call.body.Substring(0,[Math]::Min(1200,$call.body.Length));$summary.bodyBytes=[Text.Encoding]::UTF8.GetByteCount($call.body);$summary.receipt=($receipts[$call.id] -eq $signature);$view.Add($summary)
    }
    # This is a bounded view cache for the 100 visible requests, not the source archive.
    # Original traces/network logs remain untouched. Prune only our own cache filenames.
    Get-ChildItem $contextFolder -Filter '*.txt'|Where-Object {$_.Name -match '^[a-f0-9]{64}-[a-f0-9]{64}\.txt$' -and !$visibleBodies.ContainsKey($_.Name)}|Remove-Item -Force
    [IO.File]::WriteAllText($receiptsPath,(@{deviceId=$identity.deviceId;receipts=$receipts}|ConvertTo-Json -Depth 5 -Compress),[Text.Encoding]::UTF8)
    $first=@($calls|Select-Object -First 1);$firstBody=if($first.Count){$first[0].body}else{''};$firstId=if($first.Count){$first[0].id}else{''};$firstHash=if($first.Count){$first[0].bodySHA256}else{''}
    $state=@{captureBody=$firstBody;captureBodyId=$firstId;captureBodySHA256=$firstHash;deviceId=$identity.deviceId;server=$Server;active=$true;updatedAt=(Get-Date).ToString('o');calls=$view.ToArray();error=$errorMessage;appRunning=(@(Get-Process -Name '*workbuddy*' -ErrorAction SilentlyContinue).Count -gt 0)}
    $statePath=Join-Path $folder 'context-state.json';$temp=$statePath+'.tmp'
    [IO.File]::WriteAllText($temp,($state|ConvertTo-Json -Depth 6 -Compress),[Text.Encoding]::UTF8);Move-Item $temp $statePath -Force
}
