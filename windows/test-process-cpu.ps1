# Read-only named-process sample. Scene and window visibility are NOT inferred.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$cores = [int](Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
$rows = @()
for ($sample = 0; $sample -lt 5; $sample++) {
    $before = @{}
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match '^(WorkBuddy|AgentReins)([-.].*)?$' } | ForEach-Object {
        $before[$_.Id] = @{ cpu = $_.TotalProcessorTime.TotalSeconds; started = $_.StartTime.ToUniversalTime().ToString('o') }
    }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    Start-Sleep -Seconds 2
    $elapsed = $watch.Elapsed.TotalSeconds
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -match '^(WorkBuddy|AgentReins)([-.].*)?$' } | ForEach-Object {
        $previous = $before[$_.Id]
        $started = $_.StartTime.ToUniversalTime().ToString('o')
        if ($null -ne $previous -and $started -eq $previous.started) {
            $rows += [ordered]@{ sample = $sample; pid = $_.Id; name = $_.ProcessName;
                cpuPercentMachine = [math]::Round(100 * ($_.TotalProcessorTime.TotalSeconds - $previous.cpu) / ($elapsed * $cores), 3);
                workingSetBytes = $_.WorkingSet64; elapsedSeconds = $elapsed }
        }
    }
}
[ordered]@{ logicalProcessors = $cores; samples = $rows; observedProcessCount = @($rows | Select-Object -ExpandProperty pid -Unique).Count;
    scope = 'Named WorkBuddy/AgentReins processes only; excludes differently named child processes; scene supplied by operator';
    checkedAt = [DateTime]::UtcNow.ToString('o') } | ConvertTo-Json -Depth 5 -Compress
