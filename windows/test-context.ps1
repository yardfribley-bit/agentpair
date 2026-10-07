$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'workbuddy-context.ps1')
$fixture=Join-Path $env:TEMP ('applens-context-'+[Guid]::NewGuid().ToString('N'))
$trace=Join-Path $fixture 'workbuddy\traces\1';$data=Join-Path $fixture 'data'
New-Item -ItemType Directory -Force $trace,$data | Out-Null
$Server='https://fixture.invalid';$key='test';$script:posted=@()
function Invoke-Api($path,$body,$token){if($path -ne '/api/applens/model-context'){throw 'Unexpected route'};$script:posted+=@($body.requests);return @{receipts=@($body.requests|ForEach-Object {@{id=$_.id;bodySHA256=$_.bodySHA256}})}}
$body=@(@{role='system';content='rules'},@{role='user';content='hello world'})|ConvertTo-Json -Depth 5 -Compress
$document=@{trace=@{traceId='trace';workerPid=1};spans=@(@{type='generation';spanId='span';startedAt=[DateTimeOffset]::UtcNow.ToString('o');model='fixture-model';toolInput=$body},@{type='function';spanId='ignored';toolInput='do not collect tool'})}
[IO.File]::WriteAllText((Join-Path $trace 'trace.json'),($document|ConvertTo-Json -Depth 8),[Text.Encoding]::UTF8)
Sync-WorkBuddyContext @{deviceId='fixture';token='fixture'} $data (Join-Path $fixture 'workbuddy')
$state=Get-Content (Join-Path $data 'context-state.json') -Raw -Encoding UTF8|ConvertFrom-Json
if(@($state.calls).Count -ne 1 -or !$state.calls[0].receipt -or $state.calls[0].model -ne 'fixture-model' -or $state.calls[0].recordStatus -ne 'parseable'){throw 'Context/receipt acceptance failed'}
$event=Get-Content (Join-Path $data 'capture-event.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($event.phase -ne 'received' -or !$event.record.receipt -or $event.record.preview -ne $body -or $event.record.source -ne 'workbuddy_generation_context'){throw 'Capture scene acknowledgement state failed'}
if($script:posted[0].body -ne $body){throw 'Original body changed'}
$bodyFile=Join-Path (Join-Path $data 'contexts') ($state.calls[0].id+'-'+$state.calls[0].bodySHA256+'.txt')
if(!(Test-Path $bodyFile) -or [IO.File]::ReadAllText($bodyFile,[Text.Encoding]::UTF8) -ne $body){throw 'Selected historical context body not retained locally'}
[IO.File]::WriteAllText($bodyFile,'broken partial cache',[Text.Encoding]::UTF8)
Sync-WorkBuddyContext @{deviceId='fixture';token='fixture'} $data (Join-Path $fixture 'workbuddy')
if([IO.File]::ReadAllText($bodyFile,[Text.Encoding]::UTF8) -ne $body){throw 'Damaged selected context cache not repaired'}
if($script:posted.Count -ne 1){throw 'Cache repair resent already acknowledged context'}
Sync-WorkBuddyContext @{deviceId='fixture';token='fixture'} $data (Join-Path $fixture 'workbuddy')
if($script:posted.Count -ne 1){throw 'Already acknowledged context was resent'}
$networkBody=@{model='wire-model';messages=@(@{role='system';content=('A'*150000)},@{role='user';content='END_MARKER'})}|ConvertTo-Json -Depth 5 -Compress
$raw=[Text.Encoding]::UTF8.GetBytes($networkBody)
$network=@{host='copilot.tencent.com';path='/v2/chat/completions';flowID='fixture-flow';observedAt=[DateTimeOffset]::UtcNow.ToString('o');requestBodyBase64=[Convert]::ToBase64String($raw);requestSHA256=(Get-ContextDigest $networkBody);declaredContentLength=$raw.Length;capturedWireBodyBytes=$raw.Length}
[IO.File]::WriteAllText((Join-Path $data 'workbuddy-network.jsonl'),($network|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
Sync-WorkBuddyContext @{deviceId='fixture';token='fixture'} $data (Join-Path $fixture 'workbuddy')
$wire=@($script:posted|Where-Object {$_.source -eq 'workbuddy_network_context'})
if($wire.Count -ne 1 -or $wire[0].body -ne $networkBody -or !$wire[0].wireLengthMatched -or $wire[0].model -ne 'wire-model'){throw 'Complete HTTP body upload acceptance failed'}
$logs=Join-Path $fixture 'workbuddy\logs';New-Item -ItemType Directory -Force $logs|Out-Null
$busyLog=Join-Path $logs 'workbuddy-active.log'
[IO.File]::WriteAllText($busyLog,'active log',[Text.Encoding]::UTF8)
$locked=[IO.File]::Open($busyLog,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
try {
    $network.flowID='locked-log-network-flow'
    [IO.File]::WriteAllText((Join-Path $data 'workbuddy-network.jsonl'),($network|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    Sync-WorkBuddyContext @{deviceId='fixture';token='fixture'} $data (Join-Path $fixture 'workbuddy')
    if(@($script:posted|Where-Object {$_.source -eq 'workbuddy_network_context' -and $_.sessionId -eq 'network:locked-log-network-flow'}).Count -ne 1){throw 'Locked log blocked independent network upload'}
} finally {$locked.Dispose()}
$network.requestSHA256='invalid'
[IO.File]::WriteAllText((Join-Path $data 'workbuddy-network.jsonl'),($network|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
if(@(Get-WorkBuddyNetworkContext $data (Get-Date).AddDays(-1)).Count){throw 'Invalid digest accepted'}
# A changed record that fails to upload must remain locally queued, never acknowledged.
$document.spans[0].toolInput=$body+' changed'
[IO.File]::WriteAllText((Join-Path $trace 'trace.json'),($document|ConvertTo-Json -Depth 8),[Text.Encoding]::UTF8)
function Invoke-Api($path,$body,$token){throw 'Fixture offline'}
Sync-WorkBuddyContext @{deviceId='fixture';token='fixture'} $data (Join-Path $fixture 'workbuddy')
$event=Get-Content (Join-Path $data 'capture-event.json') -Raw -Encoding UTF8|ConvertFrom-Json
if($event.phase -ne 'failed' -or $event.record.receipt){throw 'Failed upload falsely displayed as received'}
Write-Host 'PASS: generation-only collection, original bytes, receipt confirmation, dedup and metadata state'
