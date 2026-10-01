param([Parameter(Mandatory=$true)][string]$InputPath)
$ErrorActionPreference='Stop'
$target=Get-Content -Raw -LiteralPath $InputPath | ConvertFrom-Json
$process=Get-Process -Id $target.pid
if ([Math]::Abs($process.StartTime.ToUniversalTime().Ticks - ([DateTimeOffset]::Parse($target.startedAt)).UtcDateTime.Ticks) -ge 10000) { throw 'Target process changed; refresh selection.' }
$rows=@(Get-NetTCPConnection | Where-Object {$_.OwningProcess -eq $target.pid} | Select-Object -First 1000 | ForEach-Object {
    @{localAddress=[string]$_.LocalAddress;localPort=[int]$_.LocalPort;remoteAddress=[string]$_.RemoteAddress;remotePort=[int]$_.RemotePort;state=[string]$_.State}
})
@{schemaVersion=1;capability='process_tcp';capturedAt=[DateTime]::UtcNow.ToString('o');target=@{pid=[int]$target.pid;startedAt=$target.startedAt};evidence=@{connections=$rows;limitations=@('Point-in-time TCP snapshot; not DNS, payload capture or proof of exfiltration.')}} | ConvertTo-Json -Depth 8 -Compress
