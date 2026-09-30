# AgentPair Windows inventory connector v0.1 (PowerShell 5.1+).
# Foreground only. Ctrl+C stops collection. No admin, scheduled task or remote shell.
param([Parameter(Mandatory=$true)][string]$Server,
      [string]$PairCode,
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
function Invoke-Api($path, $body, $token) {
    $headers = @{}
    if ($token) { $headers.Authorization = 'Bearer ' + $token }
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
do {
    try {
        $issues = @()
        $rawProcesses = @(Get-CimInstance Win32_Process)
        $processes = @($rawProcesses | Select-Object -First 2000 | ForEach-Object {
            @{name=[string]$_.Name;pid=[int]$_.ProcessId;parentPid=[int]$_.ParentProcessId}
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
        Invoke-Api '/api/endpoint/report' $body $identity.token | Out-Null
        Write-Host ('Inventory uploaded at ' + (Get-Date -Format T))
    } catch {
        # Do not print response/request bodies or tokens.
        Write-Warning 'Inventory upload failed. Check connectivity, HTTPS certificate, or device binding.'
        if ($Once) { exit 1 }
    }
    if (-not $Once) { Start-Sleep -Seconds 30 }
} while (-not $Once)
