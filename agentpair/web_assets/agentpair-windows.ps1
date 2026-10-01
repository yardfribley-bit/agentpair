# AgentPair Windows inventory connector v0.1 (PowerShell 5.1+).
# Foreground only. Ctrl+C stops collection. No admin, scheduled task or remote shell.
param([Parameter(Mandatory=$true)][string]$Server,
      [string]$PairCode,
      [string]$AnalyzeProcess,
      [string]$Goal,
      [switch]$Once)
$ErrorActionPreference = 'Stop'
$uri = [Uri]$Server
if ($uri.Scheme -ne 'https' -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') {
    throw 'Use the trusted HTTPS origin of your AgentPair platform.'
}
$Server = $Server.TrimEnd('/')
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Security
$folder = Join-Path $env:LOCALAPPDATA 'AgentPair'
New-Item -ItemType Directory -Force -Path $folder | Out-Null
$hash = [Security.Cryptography.SHA256]::Create()
$key = ([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($Server)))).Replace('-','')
$identityPath = Join-Path $folder ($key + '.identity')
function Invoke-Api($path, $body, $token, $method='POST') {
    $headers = @{}
    if ($token) { $headers.Authorization = 'Bearer ' + $token }
    if ($method -eq 'GET') { return Invoke-RestMethod -Uri ($Server+$path) -Method Get -Headers $headers -TimeoutSec 20 }
    $bytes = [Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 8 -Compress))
    Invoke-RestMethod -Uri ($Server+$path) -Method Post -Headers $headers -ContentType 'application/json' -Body $bytes -TimeoutSec 20
}
if ($PairCode) {
    $identity = Invoke-Api '/api/endpoint/enroll' @{code=$PairCode;name=$env:COMPUTERNAME} $null
    $plain = [Text.Encoding]::UTF8.GetBytes(($identity | ConvertTo-Json -Compress))
    $protected = [Security.Cryptography.ProtectedData]::Protect($plain,$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    [IO.File]::WriteAllBytes($identityPath,$protected)
} elseif (Test-Path $identityPath) {
    $plain = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($identityPath),$null,[Security.Cryptography.DataProtectionScope]::CurrentUser)
    $identity = [Text.Encoding]::UTF8.GetString($plain) | ConvertFrom-Json
} else { throw 'Generate a pairing code on the My Devices page, then pass -PairCode.' }
Write-Host 'Connected. Collecting process names/IDs and installed application metadata only. Ctrl+C stops.'
$activeAnalysis=$null
function Invoke-CapabilityModule($module) {
    if ($module.protocolVersion -ne 1 -or $module.runtime -ne 'powershell-5.1' -or
        $module.timeoutSeconds -lt 1 -or $module.timeoutSeconds -gt 30 -or
        $module.maxOutputBytes -lt 1 -or $module.maxOutputBytes -gt 524288 -or
        @($module.permissions).Count -ne 1 -or $module.permissions[0] -ne 'target-process-read') {
        throw 'Unsupported module manifest'
    }
    $source=[Convert]::FromBase64String([string]$module.sourceBase64)
    if ($source.Length -gt 262144) { throw 'Module too large' }
    $actual=([BitConverter]::ToString($hash.ComputeHash($source))).Replace('-','').ToLowerInvariant()
    if ($actual -ne $module.sha256) { throw 'Module integrity check failed' }
    $runFolder=Join-Path $folder ('module-'+[Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $runFolder | Out-Null
    $child=$null
    try {
        $scriptPath=Join-Path $runFolder 'module.ps1'
        $inputPath=Join-Path $runFolder 'input.json'
        $outPath=Join-Path $runFolder 'output.json'
        $errPath=Join-Path $runFolder 'error.txt'
        [IO.File]::WriteAllBytes($scriptPath,$source)
        [IO.File]::WriteAllText($inputPath,($module.parameters | ConvertTo-Json -Depth 8 -Compress),[Text.Encoding]::UTF8)
        $exe=Join-Path $PSHOME 'powershell.exe'
        $arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "'+$scriptPath+'" -InputPath "'+$inputPath+'"'
        $child=Start-Process -FilePath $exe -ArgumentList $arguments -PassThru -WindowStyle Hidden -RedirectStandardOutput $outPath -RedirectStandardError $errPath
        # Retain the process handle so PowerShell 5.1 can read ExitCode after exit.
        $null=$child.Handle
        if (-not $child.WaitForExit([int]$module.timeoutSeconds*1000)) {
            $child.Kill(); $child.WaitForExit(); throw 'Module timed out'
        }
        $child.Refresh()
        if ($child.ExitCode -ne 0) { throw 'Module failed; target may have exited or access was denied' }
        if ((Get-Item -LiteralPath $outPath).Length -gt $module.maxOutputBytes) { throw 'Module output too large' }
        $output=Get-Content -Raw -LiteralPath $outPath | ConvertFrom-Json
        if ($output.schemaVersion -ne 1 -or $output.capability -ne $module.id -or
            $output.target.pid -ne $module.parameters.pid -or $output.target.startedAt -ne $module.parameters.startedAt) { throw 'Invalid module evidence' }
        return @{moduleId=$module.id;moduleVersion=$module.version;sha256=$actual;output=$output}
    } finally {
        if ($child -and -not $child.HasExited) { $child.Kill(); $child.WaitForExit() }
        Remove-Item -LiteralPath $runFolder -Recurse -Force
    }
}
function Invoke-DriverTask($task) {
    $taskId=[string]$task.taskId; $lease=[string]$task.lease
    try {
        # Build the payload in separate variables.  This is deliberately verbose because
        # Windows PowerShell 5.1 has brittle parsing around nested hashtables in pipelines.
        $runningResult = New-Object PSObject -Property ([ordered]@{
            state = 'running'
            summary = 'Windows Driver 已接收任务，正在采集证据。'
        })
        $runningBody = New-Object PSObject -Property ([ordered]@{
            taskId = $taskId
            lease = $lease
            result = $runningResult
        })
        Invoke-Api '/api/endpoint/tasks/result' $runningBody $identity.token | Out-Null
        if ($task.payload.experienceRef) {
            Write-Host ('Reusing validated collection method: '+$task.payload.experienceRef.id+'; collecting fresh evidence.')
        }
        if ($task.payload.action -eq 'run_module') {
            try {
                $moduleEvidence=Invoke-CapabilityModule $task.payload.module
                $moduleResult=@{state='completed';summary='采集模块执行成功，已返回目标进程证据；分析结论仍需云端验收。';evidence=$moduleEvidence}
            } catch {
                $moduleResult=@{state='failed';summary=[string]$_.Exception.Message;moduleId=$task.payload.module.id}
            }
            Invoke-Api '/api/endpoint/tasks/result' @{taskId=$taskId;lease=$lease;result=$moduleResult} $identity.token | Out-Null
            return
        }
        # The first task protocol is intentionally allowlisted: no arbitrary shell or file execution.
        $raw=@(Get-CimInstance Win32_Process)
        $evidence = New-Object PSObject -Property ([ordered]@{ processCount = [Math]::Min($raw.Count,2000) })
        $required=@($task.payload.requiredEvidence)
        $missing=@($required | Where-Object {$_ -ne 'processes'})
        if($missing.Count -eq 0){$state='completed';$summary='Windows Driver 已完成任务。'}else{$state='waiting_for_evidence';$summary='当前采集器不具备所需证据采集能力。'}
        $result = New-Object PSObject -Property ([ordered]@{
            state = $state
            summary = $summary
            evidence = $evidence
            missingEvidence = $missing
            nextSteps = @('为缺失证据增加经过授权的采集器能力')
        })
        $completeBody = New-Object PSObject -Property ([ordered]@{
            taskId = $taskId
            lease = $lease
            result = $result
        })
        Invoke-Api '/api/endpoint/tasks/result' $completeBody $identity.token | Out-Null
        Write-Host ('Task '+$taskId+' -> '+$state)
    } catch { Write-Warning 'Driver task execution or result upload failed.' }
}
do {
    try {
        $issues = @()
        $rawProcesses = @(Get-CimInstance Win32_Process)
        $processes = @($rawProcesses | Select-Object -First 2000 | ForEach-Object {
            $startedAt=$null
            if ($_.CreationDate) { $startedAt=$_.CreationDate.ToUniversalTime().ToString('o') }
            @{name=[string]$_.Name;pid=[int]$_.ProcessId;parentPid=[int]$_.ParentProcessId;startedAt=$startedAt}
        })
        $apps = @()
        $roots = @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
                   'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*')
        # Install paths are used only locally for association; never uploaded.
        $installed = @($roots | ForEach-Object {Get-ItemProperty $_ -ErrorAction SilentlyContinue} |
            Where-Object {$_.DisplayName} | Sort-Object DisplayName,DisplayVersion -Unique | Select-Object -First 2000)
        foreach ($app in $installed) {
            $names = @()
            if ($app.InstallLocation) {
                $prefix = ([string]$app.InstallLocation).TrimEnd('\') + '\'
                $names = @($rawProcesses | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)} |
                    Select-Object -ExpandProperty Name -Unique | Select-Object -First 100)
            }
            $apps += @{name=([string]$app.DisplayName).Substring(0,[Math]::Min(300,([string]$app.DisplayName).Length));version=[string]$app.DisplayVersion;publisher=[string]$app.Publisher;processNames=$names}
        }
        $issues += 'Installed apps from uninstall registry only; Store/portable apps may be missing. Protected process paths may be unavailable.'
        $body = @{os='Windows';architecture=$env:PROCESSOR_ARCHITECTURE;processes=$processes;applications=$apps;errors=$issues}
        $body.applens=@{product='AppLens';protocolVersion=1;capabilities=@{
            process_inventory='available';application_inventory='available';process_details='available';process_tcp='available';
            process_events='unsupported';network_events='unsupported';file_events='unsupported';cloud_requests='available'}}
        $body.osVersion=[Environment]::OSVersion.Version.ToString()
        $body.hostRuntimeVersion=$PSVersionTable.PSVersion.ToString()
        Invoke-Api '/api/endpoint/report' $body $identity.token | Out-Null
        Write-Host ('Inventory uploaded at ' + (Get-Date -Format T))
        if ($AnalyzeProcess -and -not $activeAnalysis) {
            if (-not $Goal) { throw 'Analysis goal required' }
            $target=@($processes | Where-Object {$_.name -eq $AnalyzeProcess} | Sort-Object startedAt | Select-Object -First 1)
            if ($target.Count -eq 0) { throw 'Requested application is not running' }
            $request=@{title=('Analyze '+$AnalyzeProcess);message=$Goal;processTarget=@{pid=$target[0].pid;startedAt=$target[0].startedAt}}
            $activeAnalysis=Invoke-Api '/api/endpoint/requests' $request $identity.token
            Write-Host ('Application analysis submitted: '+$activeAnalysis.analysisId)
        }
        $task=Invoke-Api '/api/endpoint/tasks' $null $identity.token 'GET'
        if ($task.task) { Invoke-DriverTask $task.task }
        if ($activeAnalysis) {
            $analysis=Invoke-Api ('/api/endpoint/analyses/'+$activeAnalysis.analysisId) $null $identity.token 'GET'
            Write-Host ('Analysis state: '+$analysis.state+' / '+$analysis.cloudState)
            if ($analysis.results) {
                $last=@($analysis.results)[-1]
                $answer=$last.outputs.review.answer
                if ($answer.finalAnswer) { Write-Host $answer.finalAnswer } else { Write-Host $answer.summary }
            }
        }
    } catch {
        # Do not print response/request bodies or tokens.
        Write-Warning 'Inventory upload failed. Check connectivity, HTTPS certificate, or device binding.'
        if ($Once) { exit 1 }
    }
    if (-not $Once) { Start-Sleep -Seconds 30 }
} while (-not $Once)
