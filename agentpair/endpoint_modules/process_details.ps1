param([Parameter(Mandatory=$true)][string]$InputPath)
$ErrorActionPreference='Stop'
$target=Get-Content -Raw -LiteralPath $InputPath | ConvertFrom-Json
$process=Get-Process -Id $target.pid
# CIM timestamps can round sub-millisecond process creation precision.
if ([Math]::Abs($process.StartTime.ToUniversalTime().Ticks - ([DateTimeOffset]::Parse($target.startedAt)).UtcDateTime.Ticks) -ge 10000) { throw 'Target process changed; refresh selection.' }
$cim=Get-CimInstance Win32_Process -Filter ('ProcessId='+[int]$target.pid)
$path=$process.Path
$fileHash=$null; $signature=$null
if ($path) {
    $fileHash=(Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    $signature=[string](Get-AuthenticodeSignature -LiteralPath $path).Status
}
@{schemaVersion=1;capability='process_details';capturedAt=[DateTime]::UtcNow.ToString('o');target=@{pid=[int]$target.pid;startedAt=$target.startedAt};evidence=@{name=$process.ProcessName;parentPid=[int]$cim.ParentProcessId;path=$path;sha256=$fileHash;signatureStatus=$signature}} | ConvertTo-Json -Depth 8 -Compress
