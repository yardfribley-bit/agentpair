$ErrorActionPreference='Stop'
$target=Join-Path $PSScriptRoot 'build\capture'
if(Test-Path (Join-Path $target 'mitmdump.exe')){return}
$archive=Join-Path $PSScriptRoot 'build\mitmproxy.zip'
Invoke-WebRequest -UseBasicParsing 'https://downloads.mitmproxy.org/12.2.3/mitmproxy-12.2.3-windows-x86_64.zip' -OutFile $archive
if((Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne '04a01ea95ae96df75058a893e774957d294e69012dab1f4e256ce2b0c6725483'){throw 'Capture runtime checksum mismatch'}
Expand-Archive $archive -DestinationPath (Join-Path $PSScriptRoot 'build\mitmproxy') -Force
$binary=Get-ChildItem (Join-Path $PSScriptRoot 'build\mitmproxy') -Filter mitmdump.exe -Recurse|Select-Object -First 1
if(!$binary){throw 'Official archive missing mitmdump'}
New-Item -ItemType Directory -Force $target|Out-Null
Copy-Item $binary.FullName (Join-Path $target 'mitmdump.exe')
Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/mitmproxy/mitmproxy/v12.2.3/LICENSE' -OutFile (Join-Path $target 'LICENSE.txt')
