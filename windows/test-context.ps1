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
if($script:posted[0].body -ne $body){throw 'Original body changed'}
Sync-WorkBuddyContext @{deviceId='fixture';token='fixture'} $data (Join-Path $fixture 'workbuddy')
if($script:posted.Count -ne 1){throw 'Already acknowledged context was resent'}
Write-Host 'PASS: generation-only collection, original bytes, receipt confirmation, dedup and metadata state'
