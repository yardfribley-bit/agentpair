# Runs the production module executor against the CI process, without enrollment.
$ErrorActionPreference='Stop'
$hostScript=Join-Path $PSScriptRoot '..\agentpair\web_assets\agentpair-windows.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Resolve-Path $hostScript),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Host syntax errors' }
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-CapabilityModule'},$true)
Invoke-Expression $function.Extent.Text
foreach ($name in @('Invoke-Api','Invoke-DriverTask')) {
    $definition=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    Invoke-Expression $definition.Extent.Text
}
$folder=Join-Path $env:TEMP ('agentpair-module-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $folder | Out-Null
$hash=[Security.Cryptography.SHA256]::Create()
$fixture=$null
try {
    foreach ($id in @('process_details','process_tcp')) {
        $path=Join-Path $PSScriptRoot ('..\agentpair\endpoint_modules\'+$id+'.ps1')
        $moduleAst=[Management.Automation.Language.Parser]::ParseFile((Resolve-Path $path),[ref]$tokens,[ref]$errors)
        if ($errors.Count) { throw 'Module syntax errors' }
        $bytes=[IO.File]::ReadAllBytes((Resolve-Path $path))
        $manifest=@{protocolVersion=1;id=$id;version='1.0.0';runtime='powershell-5.1';permissions=@('target-process-read');timeoutSeconds=30;maxOutputBytes=524288;sourceBase64=[Convert]::ToBase64String($bytes);sha256=([BitConverter]::ToString($hash.ComputeHash($bytes))).Replace('-','').ToLowerInvariant();parameters=@{pid=$PID;startedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')}}
        $diagnosticInput=Join-Path $folder 'diagnostic-input.json'
        [IO.File]::WriteAllText($diagnosticInput,($manifest.parameters | ConvertTo-Json),[Text.Encoding]::UTF8)
        Write-Host ('Direct module test: '+$id)
        & $path -InputPath $diagnosticInput | Out-Null
        Write-Host ('Production executor test: '+$id)
        $result=Invoke-CapabilityModule $manifest
        if ($result.output.target.pid -ne $PID) { throw 'Evidence mismatch' }
        $manifest.sha256='invalid'
        $rejected=$false
        try { Invoke-CapabilityModule $manifest | Out-Null } catch { $rejected=$true }
        if (-not $rejected) { throw 'Tampered module was accepted' }
    }
    $fixtureScript=Join-Path $PSScriptRoot 'module_protocol_fixture.py'
    $startedAt=(Get-Process -Id $PID).StartTime.ToUniversalTime().ToString('o')
    $arguments='"'+$fixtureScript+'" --directory "'+$folder+'" --pid '+$PID+' --started-at "'+$startedAt+'"'
    $fixture=Start-Process -FilePath (Get-Command python).Source -ArgumentList $arguments -PassThru -WindowStyle Hidden
    $ready=Join-Path $folder 'ready.json'
    for ($i=0; $i -lt 100 -and -not (Test-Path $ready); $i++) { Start-Sleep -Milliseconds 100 }
    $settings=Get-Content -Raw -LiteralPath $ready | ConvertFrom-Json
    $Server=$settings.server; $identity=$settings.identity
    foreach ($id in @('process_details','process_tcp')) {
        $pulled=Invoke-Api '/api/endpoint/tasks' $null $identity.token 'GET'
        if ($pulled.task.payload.module.id -ne $id) { throw 'Wrong dispatched module' }
        Invoke-DriverTask $pulled.task
    }
    $assertion=Invoke-Api '/assert' $null $identity.token 'GET'
    if (-not $assertion.passed) { throw 'Protocol acceptance failed' }
    Write-Host 'Production device store -> HTTP task pull -> Windows execution -> HTTP evidence upload passed.'
    Invoke-Api '/reuse' $null $identity.token 'GET' | Out-Null
    foreach ($id in @('process_details','process_tcp')) {
        $pulled=Invoke-Api '/api/endpoint/tasks' $null $identity.token 'GET'
        if (-not $pulled.task.payload.experienceRef) { throw 'Successful method was not reused' }
        Invoke-DriverTask $pulled.task
    }
    $assertion=Invoke-Api '/experiences' $null $identity.token 'GET'
    if (-not $assertion.passed) { throw 'Replay evidence not retained' }
    Write-Host 'Successful methods saved, matched, replayed and validated with fresh Windows evidence.'
    Write-Host 'Both dynamic modules executed; integrity rejection passed.'
} finally {
    if ($fixture -and -not $fixture.HasExited) { Stop-Process -Id $fixture.Id -Force }
    $hash.Dispose()
    Remove-Item -LiteralPath $folder -Recurse -Force
}
