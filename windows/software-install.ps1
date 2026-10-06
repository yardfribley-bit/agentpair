# Trusted server recipe only. No caller-supplied commands or installer arguments.
function Get-AgentPairSafePortablePath([string]$value, [bool]$directory=$false) {
    $normalized=$value.Replace('\','/')
    if($directory -and $normalized.EndsWith('/')) {$normalized=$normalized.Substring(0,$normalized.Length-1)}
    if(-not $normalized -or $normalized.Length -gt 240) {throw 'Invalid portable relative path'}
    foreach($part in $normalized.Split('/')) {
        if(-not $part -or $part -in @('.','..') -or $part -match '[\x00-\x1f\x7f:*?"<>|]' -or
           $part.EndsWith('.') -or $part.EndsWith(' ') -or
           $part -match '^(CON|CONIN\$|CONOUT\$|PRN|AUX|NUL|CLOCK\$|COM[0-9\u00b9\u00b2\u00b3]|LPT[0-9\u00b9\u00b2\u00b3]) *(\..*)?$') {
            throw 'Unsafe portable relative path'
        }
    }
    return $normalized
}

function Assert-AgentPairNoReparse([string]$path, [bool]$tree=$false) {
    $current=[IO.Path]::GetFullPath($path)
    while($current) {
        $item=Get-Item -LiteralPath $current -Force -ErrorAction SilentlyContinue
        if($item -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'Portable paths cannot contain symbolic links or reparse points'
        }
        $current=[IO.Path]::GetDirectoryName($current)
    }
    if($tree -and (Test-Path -LiteralPath $path -PathType Container)) {
        $pending=New-Object 'Collections.Generic.Queue[string]'
        $pending.Enqueue($path);$count=0
        while($pending.Count) {
            foreach($item in Get-ChildItem -LiteralPath $pending.Dequeue() -Force -ErrorAction Stop) {
                $count++
                if($count -gt 20000) {throw 'Existing portable tree exceeds entry limit'}
                if($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {throw 'Portable tree contains a reparse point'}
                if($item.PSIsContainer) {$pending.Enqueue($item.FullName)}
            }
        }
    }
}

function Get-AgentPairPortableFingerprint($recipe) {
    # Length-prefixed UTF-8 fields match portable_recipe_fingerprint in Python.
    $body=''
    foreach($key in @('id','platform','installer','url','sha256','name','displayName','version','entryPoint')) {
        $value=[string]$recipe.$key
        if($key -eq 'url' -and $recipe.officialUrl) {$value=[string]$recipe.officialUrl}
        $body+=$key+':'+[Text.Encoding]::UTF8.GetByteCount($value)+':'+$value+"`n"
    }
    $hasher=[Security.Cryptography.SHA256]::Create()
    try {return ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($body)))).Replace('-','').ToLowerInvariant()}
    finally {$hasher.Dispose()}
}

function Test-AgentPairPortableTarget([string]$target, [string]$softwareId) {
    Assert-AgentPairNoReparse $target $true
    if(-not (Test-Path -LiteralPath $target)) {return $false}
    $marker=Join-Path $target '.agentpair-portable.json'
    if(-not (Test-Path -LiteralPath $target -PathType Container) -or -not (Test-Path -LiteralPath $marker -PathType Leaf)) {
        throw 'Portable target exists and is not an AgentPair-managed installation'
    }
    if((Get-Item -LiteralPath $marker).Length -gt 16384) {throw 'Portable ownership marker exceeds size limit'}
    $owned=Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
    if($owned.managedBy -cne 'AgentPair' -or $owned.installer -cne 'portable_zip' -or $owned.softwareId -cne $softwareId) {
        throw 'Portable target ownership does not match this software'
    }
    return $true
}

function Expand-AgentPairPortableZip([string]$archivePath, [string]$destination, [string]$entryPoint,
                                    [long]$maxArchiveBytes=1073741824, [int]$maxEntries=20000,
                                    [long]$maxFileBytes=536870912, [long]$maxTotalBytes=2147483648) {
    $ErrorActionPreference='Stop'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    if((Get-Item -LiteralPath $archivePath).Length -gt $maxArchiveBytes) {throw 'Portable archive exceeds size limit'}
    Assert-AgentPairNoReparse $destination
    if(Test-Path -LiteralPath $destination) {throw 'Portable extraction destination must be new'}
    $root=[IO.Path]::GetFullPath($destination)
    $prefix=$root+[IO.Path]::DirectorySeparatorChar
    $archive=[IO.Compression.ZipFile]::OpenRead($archivePath)
    $created=$false
    try {
        if($archive.Entries.Count -eq 0 -or $archive.Entries.Count -gt $maxEntries) {throw 'Portable archive exceeds entry limit or is empty'}
        $seen=New-Object 'Collections.Generic.Dictionary[string,bool]' ([StringComparer]::OrdinalIgnoreCase)
        $entries=New-Object 'Collections.Generic.List[object]'
        [long]$total=0
        foreach($entry in $archive.Entries) {
            $type=($entry.ExternalAttributes -shr 16) -band 0xf000
            if($type -notin @(0,0x8000,0x4000) -or ($entry.ExternalAttributes -band 0x400)) {
                throw 'Portable archive contains a symbolic link, reparse point or special file'
            }
            $directory=$entry.FullName.EndsWith('/') -or $entry.FullName.EndsWith('\') -or
                       $type -eq 0x4000 -or ($entry.ExternalAttributes -band 0x10)
            $relative=Get-AgentPairSafePortablePath $entry.FullName ([bool]$directory)
            if($relative -ieq '.agentpair-portable.json' -or $seen.ContainsKey($relative)) {throw 'Portable archive has a reserved or duplicate path'}
            $target=[IO.Path]::GetFullPath([IO.Path]::Combine($root,$relative.Replace('/',[IO.Path]::DirectorySeparatorChar)))
            if(-not $target.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) -or $target.Length -ge 260) {
                throw 'Portable archive path escapes destination or is too long'
            }
            if($entry.Length -lt 0 -or $entry.Length -gt $maxFileBytes -or ($directory -and $entry.Length -ne 0)) {
                throw 'Portable archive entry exceeds size limit'
            }
            $total+=$entry.Length
            if($total -gt $maxTotalBytes -or ($entry.Length -gt 0 -and
               ($entry.CompressedLength -le 0 -or $entry.Length/[double]$entry.CompressedLength -gt 200))) {
                throw 'Portable archive exceeds expanded size or compression ratio limit'
            }
            $seen.Add($relative,[bool]$directory)
            $entries.Add(@{entry=$entry;relative=$relative;target=$target;directory=[bool]$directory})
        }
        foreach($record in $entries) {
            $parts=$record.relative.Split('/')
            for($i=1;$i -lt $parts.Length;$i++) {
                $ancestor=($parts[0..($i-1)] -join '/')
                if($seen.ContainsKey($ancestor) -and -not $seen[$ancestor]) {throw 'Portable archive file/directory collision'}
            }
        }
        if(-not $seen.ContainsKey($entryPoint) -or $seen[$entryPoint]) {throw 'Portable EXE entry point not found in archive'}
        [IO.Directory]::CreateDirectory($root) | Out-Null
        $created=$true
        $buffer=New-Object byte[] 1048576
        [long]$written=0
        foreach($record in $entries) {
            Assert-AgentPairNoReparse $record.target
            if($record.directory) {[IO.Directory]::CreateDirectory($record.target) | Out-Null;continue}
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($record.target)) | Out-Null
            $entryStream=$record.entry.Open();$output=$null
            try {
                $output=[IO.File]::Open($record.target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
                [long]$copied=0
                while(($read=$entryStream.Read($buffer,0,$buffer.Length)) -gt 0) {
                    $copied+=$read;$written+=$read
                    if($copied -gt $record.entry.Length -or $copied -gt $maxFileBytes -or $written -gt $maxTotalBytes) {
                        throw 'Portable entry expanded beyond declared size limit'
                    }
                    $output.Write($buffer,0,$read)
                }
                if($copied -ne $record.entry.Length) {throw 'Portable archive entry is truncated'}
            } finally {if($output){$output.Dispose()};$entryStream.Dispose()}
        }
        $binary=Join-Path $root $entryPoint.Replace('/',[IO.Path]::DirectorySeparatorChar)
        if((Get-Item -LiteralPath $binary).Length -eq 0) {throw 'Portable EXE entry point is empty'}
        return (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
    } catch {
        if($created -and (Test-Path -LiteralPath $root)) {
            Assert-AgentPairNoReparse $root $true
            Remove-Item -LiteralPath $root -Recurse -Force
        }
        throw
    } finally {$archive.Dispose()}
}

function Invoke-AgentPairPortableSelfTest([string]$binary, [string]$expectedHash, [string]$logDirectory, $report) {
    Assert-AgentPairNoReparse $binary
    if(-not (Test-Path -LiteralPath $binary -PathType Leaf) -or
       (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw 'Portable binary verification failed before self-test'
    }
    $stdout=Join-Path $logDirectory 'self-test.stdout.log';$stderr=Join-Path $logDirectory 'self-test.stderr.log'
    $child=Start-Process -FilePath $binary -ArgumentList @('--self-test') -WorkingDirectory ([IO.Path]::GetDirectoryName($binary)) `
                         -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    try {
        # Cache the native handle before waiting: redirected Start-Process can
        # otherwise leave ExitCode null on Windows (PowerShell issue #5421).
        $processHandle=$child.Handle
        $started=Get-Date;$lastReport=$started
        while(-not $child.WaitForExit(1000)) {
            if(((Get-Date)-$lastReport).TotalSeconds -ge 10) {
                & $report @{state='running';summary='正在执行便携软件自测';stage='self-test'}
                $lastReport=Get-Date
            }
            if(((Get-Date)-$started).TotalSeconds -gt 120) {
                $child.Kill()
                if(-not $child.WaitForExit(5000)) {throw 'Portable self-test could not be stopped; manual reconciliation required'}
                throw 'Portable self-test timed out'
            }
        }
        $child.WaitForExit()
        if($child.ExitCode -isnot [int]) {throw 'Portable self-test exit code unavailable'}
        if($child.ExitCode -ne 0) {throw ('Portable self-test failed: '+$child.ExitCode)}
        if((Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
            throw 'Portable binary changed during self-test'
        }
        return $child.ExitCode
    } finally {
        if(-not $child.HasExited) {
            $child.Kill()
            if(-not $child.WaitForExit(5000)) {throw 'Portable self-test still running; manual reconciliation required'}
        }
        $child.Dispose()
    }
}

function Invoke-AgentPairPortableInstall($recipe, [string]$archivePath, [string]$hash, [string]$workDirectory, $report) {
    $entryPoint=Get-AgentPairSafePortablePath ([string]$recipe.entryPoint)
    if($entryPoint -cne $recipe.entryPoint -or -not $entryPoint.EndsWith('.exe',[StringComparison]::OrdinalIgnoreCase)) {
        throw 'Portable recipe requires a relative EXE entry point with / separators'
    }
    $root=Join-Path $env:LOCALAPPDATA 'AgentPair\Software'
    Assert-AgentPairNoReparse $root
    [IO.Directory]::CreateDirectory($root) | Out-Null
    $target=Join-Path $root $recipe.id
    $markerName='.agentpair-portable.json'
    $null=Test-AgentPairPortableTarget $target $recipe.id
    $nonce=[Guid]::NewGuid().ToString('N')
    $staging=Join-Path $root ('.'+$recipe.id+'.stage-'+$nonce)
    $backup=Join-Path $root ('.'+$recipe.id+'.backup-'+$nonce)
    $movedOld=$false;$published=$false;$complete=$false;$stagingCreated=$false
    try {
        & $report @{state='running';summary='下载哈希验证通过，正在安全解压';stage='extract';sha256=$hash}
        $binaryHash=Expand-AgentPairPortableZip $archivePath $staging $entryPoint
        $stagingCreated=$true
        $fingerprint=Get-AgentPairPortableFingerprint $recipe
        $manifest=@{managedBy='AgentPair';installer='portable_zip';softwareId=$recipe.id;version=$recipe.version;
                    versionSource='pinned_recipe';sha256=$hash;entryPoint=$entryPoint;recipeFingerprint=$fingerprint;binarySha256=$binaryHash}
        [IO.File]::WriteAllText((Join-Path $staging $markerName),($manifest | ConvertTo-Json),[Text.Encoding]::UTF8)
        if(Test-AgentPairPortableTarget $target $recipe.id) {[IO.Directory]::Move($target,$backup);$movedOld=$true}
        [IO.Directory]::Move($staging,$target);$published=$true
        $binary=Join-Path $target $entryPoint.Replace('/',[IO.Path]::DirectorySeparatorChar)
        & $report @{state='running';summary='解压完成，正在检查 EXE 并执行 --self-test';stage='self-test'}
        $exitCode=Invoke-AgentPairPortableSelfTest $binary $binaryHash $workDirectory $report
        $complete=$true
        # SessionLens's windowed EXE does not report an application version. This
        # is the recipe label bound to the pinned archive, not an EXE version claim.
        return @{state='completed';summary='便携软件归档哈希、EXE 和自测验证通过；版本依据为固定归档方案，GUI 启动尚未验证';evidence=@{
            softwareId=$recipe.id;verified=$true;installedVersion=$recipe.version;sha256=$hash;
            recipeFingerprint=$fingerprint;entryPoint=$entryPoint;installDirectory=$target;
            checkedBinary=$true;checkedBinaryPath=$binary;checkedBinarySha256=$binaryHash;
            selfTestArgument='--self-test';selfTestExitCode=$exitCode;
            versionSource='pinned_recipe';binaryVersionVerified=$false;launchVerified=$false}}
    } finally {
        if(-not $complete -and $published -and (Test-Path -LiteralPath $target)) {
            $null=Test-AgentPairPortableTarget $target $recipe.id
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        if(-not $complete -and $movedOld) {[IO.Directory]::Move($backup,$target)}
        if($stagingCreated -and (Test-Path -LiteralPath $staging)) {Assert-AgentPairNoReparse $staging $true;Remove-Item -LiteralPath $staging -Recurse -Force}
        if($complete -and $movedOld) {Assert-AgentPairNoReparse $backup $true;Remove-Item -LiteralPath $backup -Recurse -Force}
    }
}

function Invoke-SoftwareInstall($recipe, $report) {
    $ErrorActionPreference='Stop'
    if($recipe.platform -cne 'Windows' -or $recipe.installer -cnotin @('inno','msi','portable_zip') -or
       $recipe.id -cnotmatch '^[a-z][a-z0-9-]{2,60}$' -or $recipe.sha256 -cnotmatch '^[0-9a-f]{64}$' -or
       $recipe.name -isnot [string] -or $recipe.version -isnot [string] -or
       -not $recipe.name.Trim() -or -not $recipe.version.Trim() -or $recipe.name.Length -gt 120 -or $recipe.version.Length -gt 120 -or
       $recipe.name -match '[\x00-\x1f\x7f]' -or $recipe.version -match '[\x00-\x1f\x7f]') {throw 'Invalid installation recipe'}
    $url=[Uri]$recipe.url
    if($recipe.url -isnot [string] -or $recipe.url -match '[\x00-\x1f\x7f]' -or -not $url.IsAbsoluteUri -or
       $url.Scheme -ne 'https' -or -not $url.Host -or $url.UserInfo -or $url.Fragment) {throw 'Trusted HTTPS installer required'}
    $portable=$recipe.installer -ceq 'portable_zip'
    if($portable) {
        $null=Get-AgentPairSafePortablePath ([string]$recipe.id)
        $entry=Get-AgentPairSafePortablePath ([string]$recipe.entryPoint)
        if($entry -cne $recipe.entryPoint -or -not $entry.EndsWith('.exe',[StringComparison]::OrdinalIgnoreCase)) {throw 'Invalid portable entry point'}
    }
    $findInstalled={
        @(Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
            Where-Object {$_.DisplayName -eq $recipe.displayName -and $_.DisplayVersion -eq $recipe.version})
    }
    $found=if($portable){@()}else{& $findInstalled}
    if($found.Count -gt 0) {
        return @{state='completed';summary='指定软件版本已存在，未重复安装';evidence=@{
            softwareId=$recipe.id;verified=$true;installedVersion=$recipe.version;sha256=$recipe.sha256;reused=$true}}
    }
    $dir=Join-Path $env:LOCALAPPDATA ('AgentPair\software-'+[Guid]::NewGuid().ToString('N'))
    Assert-AgentPairNoReparse $dir
    [IO.Directory]::CreateDirectory($dir) | Out-Null
    $extension=if($portable){'.zip'}elseif($recipe.installer -eq 'msi'){'.msi'}else{'.exe'}
    $file=Join-Path $dir ('setup'+$extension);$log=Join-Path $dir 'install.log'
    try {
        & $report @{state='running';summary='正在下载安装包';stage='download'}
        $client=New-Object Net.WebClient
        try {
            $download=$client.DownloadFileTaskAsync($url,$file);$started=Get-Date
            while(-not $download.IsCompleted) {
                Start-Sleep -Seconds 10
                if($portable -and (Test-Path -LiteralPath $file) -and (Get-Item -LiteralPath $file).Length -gt 1073741824) {
                    $client.CancelAsync();throw 'Portable download exceeds size limit'
                }
                & $report @{state='running';summary='正在下载安装包';stage='download';downloadBytes=if(Test-Path $file){(Get-Item $file).Length}else{0}}
                if(((Get-Date)-$started).TotalSeconds -gt 600){$client.CancelAsync();throw 'Download timed out'}
            }
            try {$download.GetAwaiter().GetResult()} catch {
                if(-not $recipe.officialUrl){throw}
                $official=[Uri]$recipe.officialUrl
                if($recipe.officialUrl -isnot [string] -or $recipe.officialUrl -match '[\x00-\x1f\x7f]' -or -not $official.IsAbsoluteUri -or
                   $official.Scheme -ne 'https' -or -not $official.Host -or $official.UserInfo -or $official.Fragment){throw 'Invalid official fallback'}
                & $report @{state='running';summary='缓存不可达，回退官方来源';stage='download'}
                $fallback=$client.DownloadFileTaskAsync($official,$file)
                $started=Get-Date
                while(-not $fallback.IsCompleted) {
                    Start-Sleep -Seconds 10
                    if(((Get-Date)-$started).TotalSeconds -gt 600){$client.CancelAsync();throw 'Official download timed out'}
                    if($portable -and (Test-Path -LiteralPath $file) -and (Get-Item -LiteralPath $file).Length -gt 1073741824) {
                        $client.CancelAsync();throw 'Portable download exceeds size limit'
                    }
                    & $report @{state='running';summary='正在从官方来源下载安装包';stage='download'}
                }
                $fallback.GetAwaiter().GetResult()
            }
        } finally {$client.Dispose()}
        $hash=(Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
        if($hash -ne $recipe.sha256) {throw 'Installer SHA256 mismatch; not executed'}
        if($portable) {return Invoke-AgentPairPortableInstall $recipe $file $hash $dir $report}
        & $report @{state='running';summary='下载哈希验证通过，正在安装';stage='install';sha256=$hash}
        if($recipe.installer -eq 'msi') {
            $child=Start-Process msiexec.exe -ArgumentList @('/i',('"'+$file+'"'),'/qn','/norestart','/L*v',('"'+$log+'"')) -PassThru
        } else {
            $child=Start-Process $file -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="'+$log+'"')) -PassThru
        }
        $started=Get-Date
        while(-not $child.WaitForExit(10000)) {
            & $report @{state='running';summary='安装程序正在执行';stage='install';
                log=if(Test-Path $log){[string]((Get-Content $log -Tail 25)-join "`n")}else{''}}
            if(((Get-Date)-$started).TotalSeconds -gt 600) {throw 'Installer still running after deadline; manual reconciliation required'}
        }
        if($child.ExitCode -notin @(0,3010)) {throw ('Installer failed: '+$child.ExitCode)}
        $found=& $findInstalled
        if($found.Count -eq 0) {throw 'Installation returned success but exact installed version was not found'}
        return @{state='completed';summary='安装及版本验证通过；应用启动/账号登录仍需单独验证';evidence=@{
            softwareId=$recipe.id;verified=$true;installedVersion=$recipe.version;sha256=$hash;
            rebootRequired=($child.ExitCode -eq 3010);launchVerified=$false;logPath=$log}}
    } catch {
        return @{state='failed';summary=[string]$_.Exception.Message;evidence=@{softwareId=$recipe.id;logPath=$log}}
    } finally {
        if($portable -and (Test-Path -LiteralPath $dir)) {Assert-AgentPairNoReparse $dir $true;Remove-Item -LiteralPath $dir -Recurse -Force}
    }
}
