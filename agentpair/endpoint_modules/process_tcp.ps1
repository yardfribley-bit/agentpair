param([Parameter(Mandatory=$true)][string]$InputPath)
$ErrorActionPreference='Stop'
$target=Get-Content -Raw -LiteralPath $InputPath | ConvertFrom-Json
$process=Get-Process -Id $target.pid
if ([Math]::Abs($process.StartTime.ToUniversalTime().Ticks - ([DateTimeOffset]::Parse($target.startedAt)).UtcDateTime.Ticks) -ge 10000) { throw 'Target process changed; refresh selection.' }
$all=@(Get-CimInstance Win32_Process)
$ids=@([int]$target.pid)
for ($i=0;$i -lt 16;$i++) {
    $children=@($all | Where-Object {$ids -contains [int]$_.ParentProcessId -and $ids -notcontains [int]$_.ProcessId -and $_.CreationDate -and $_.CreationDate.ToUniversalTime() -ge $process.StartTime.ToUniversalTime()} | Select-Object -ExpandProperty ProcessId)
    if (-not $children.Count) { break }
    $ids+=@($children | ForEach-Object {[int]$_})
}
$rows=@(Get-NetTCPConnection | Where-Object {$ids -contains [int]$_.OwningProcess} | Select-Object -First 1000 | ForEach-Object {
    @{pid=[int]$_.OwningProcess;localAddress=[string]$_.LocalAddress;localPort=[int]$_.LocalPort;remoteAddress=[string]$_.RemoteAddress;remotePort=[int]$_.RemotePort;state=[string]$_.State}
})
@{schemaVersion=1;capability='process_tcp';capturedAt=[DateTime]::UtcNow.ToString('o');target=@{pid=[int]$target.pid;startedAt=$target.startedAt};evidence=@{processIds=$ids;connections=$rows;limitations=@('Point-in-time TCP snapshot of root and observed descendants; not DNS, UDP/QUIC, payload capture or proof of exfiltration.')}} | ConvertTo-Json -Depth 8 -Compress
