# Runs the production module executor against the CI process, without enrollment.
$ErrorActionPreference='Stop'
$hostScript=Join-Path $PSScriptRoot '..\agentpair\web_assets\agentpair-windows.ps1'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Resolve-Path $hostScript),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Host syntax errors' }
$function=$ast.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Invoke-CapabilityModule'},$true)
Invoke-Expression $function.Extent.Text
$folder=Join-Path $env:TEMP ('agentpair-module-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $folder | Out-Null
$hash=[Security.Cryptography.SHA256]::Create()
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
    Write-Host 'Both dynamic modules executed; integrity rejection passed.'
} finally {
    $hash.Dispose()
    Remove-Item -LiteralPath $folder -Recurse -Force
}
