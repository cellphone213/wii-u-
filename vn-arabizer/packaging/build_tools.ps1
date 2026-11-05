# Builds the third-party Windows tools bundled with VN Arabizer and copies
# them into vnarabizer/assets/tools. Run from the vn-arabizer folder on
# Windows with Visual Studio 2022 (MSBuild + C++ x86) and the .NET SDK.
#
#   VNTextPatch, VNTextProxy  - arcusmaximus/VNTranslationTools (MIT)
#   KirikiriUnencryptedArchive - arcusmaximus/KirikiriTools (MIT)
#   siglus-static-key-tool     - xmoezzz/siglus_static_key_tool (MPL-2.0), needs Rust (cargo)

$ErrorActionPreference = "Stop"
$VNTT_COMMIT = "36fbe909d02f2e9bb187443e03e2df595f56bfec"
$KRKR_COMMIT = "9590c3cce4733e1f048cd407bfb57e8ce71f1f95"
$SIGKEY_COMMIT = "d1bcd97862a24b511217e3ea930599ed99e0a053"
$SIGLUS_RS_COMMIT = "5f358ef1360b0439e92aed2b749e8f25d8721954"  # only for the MPL-2.0 licence text

$out = Join-Path (Get-Location) "vnarabizer\assets\tools"
$work = Join-Path $env:RUNNER_TEMP "tool-src"
if (-not $env:RUNNER_TEMP) { $work = Join-Path $env:TEMP "vna-tool-src" }
New-Item -ItemType Directory -Force -Path $work, $out | Out-Null

$msbuild = & "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe" -latest -requires Microsoft.Component.MSBuild -find "MSBuild\**\Bin\MSBuild.exe" | Select-Object -First 1
if (-not $msbuild) { throw "MSBuild not found" }
Write-Host "MSBuild: $msbuild"

function Clone($url, $commit, $dir) {
    if (-not (Test-Path $dir)) { git clone --quiet $url $dir }
    git -C $dir fetch --quiet --depth 1 origin $commit
    git -C $dir checkout --quiet $commit
    git -C $dir submodule update --init --recursive --quiet
}

# ---- VNTextPatch + VNTextProxy -------------------------------------------------
$vntt = Join-Path $work "VNTranslationTools"
Clone "https://github.com/arcusmaximus/VNTranslationTools" $VNTT_COMMIT $vntt
Push-Location $vntt
$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
dotnet restore VNTextPatch\VNTextPatch.csproj /p:RuntimeIdentifiers=win
if ($LASTEXITCODE -ne 0) { throw "restore failed" }
& $msbuild VNTextPatch\VNTextPatch.csproj /v:m /p:LangVersion=9 /p:AllowUnsafeBlocks=true /p:Platform=AnyCPU /p:Configuration=Release "/p:OutputPath=..\Build\VNTextPatch\"
if ($LASTEXITCODE -ne 0) { throw "VNTextPatch build failed" }
Remove-Item Build\VNTextPatch\*.pdb, Build\VNTextPatch\FreeMote*.xml -ErrorAction SilentlyContinue

New-Item -ItemType Directory -Force -Path Build\VNTextProxy | Out-Null
& $msbuild VNTextProxy\VNTextProxy.vcxproj /v:m /p:Platform=Win32 /p:Configuration=Release /p:PlatformToolset=v143 /p:TargetName=d2d1
if ($LASTEXITCODE -ne 0) { throw "VNTextProxy (d2d1) build failed" }
Copy-Item VNTextProxy\Release\d2d1.dll Build\VNTextProxy\
Remove-Item -Recurse -Force VNTextProxy\Release
foreach ($proxy in Get-ChildItem VNTextProxy\AlternateProxies -Directory) {
    Copy-Item -Force (Join-Path $proxy.FullName "*") VNTextProxy\
    & $msbuild VNTextProxy\VNTextProxy.vcxproj /v:m /p:Platform=Win32 /p:Configuration=Release /p:PlatformToolset=v143 "/p:TargetName=$($proxy.Name)"
    if ($LASTEXITCODE -ne 0) { throw "VNTextProxy ($($proxy.Name)) build failed" }
    Copy-Item "VNTextProxy\Release\$($proxy.Name).dll" Build\VNTextProxy\
    Remove-Item -Recurse -Force VNTextProxy\Release
}
Pop-Location

# Turn off VNTextPatch's own word wrapping: VN Arabizer wraps (and reorders) Arabic itself.
$config = Join-Path $vntt "Build\VNTextPatch\VNTextPatch.exe.config"
$xml = Get-Content $config -Raw
$xml = $xml -replace 'key="ProportionalLineWidth" value="\d+"', 'key="ProportionalLineWidth" value="1000000"'
$xml = $xml -replace 'key="SecondaryProportionalLineWidth" value="\d+"', 'key="SecondaryProportionalLineWidth" value="1000000"'
$xml = $xml -replace 'key="MonospaceCharactersPerLine" value="\d+"', 'key="MonospaceCharactersPerLine" value="100000"'
Set-Content -Path $config -Value $xml -Encoding utf8

# ---- KirikiriUnencryptedArchive -------------------------------------------------
$krkr = Join-Path $work "KirikiriTools"
Clone "https://github.com/arcusmaximus/KirikiriTools" $KRKR_COMMIT $krkr
Push-Location $krkr
# The C++17 compiler rejects the old 'register' keyword (it never had any effect): drop it.
Get-ChildItem -Recurse -Include *.h, *.cpp KirikiriUnencryptedArchive | ForEach-Object {
    $src = Get-Content $_.FullName -Raw
    $fixed = $src -replace '\bregister\s+', ''
    if ($fixed -ne $src) { Set-Content -Path $_.FullName -Value $fixed -NoNewline }
}
& $msbuild KirikiriUnencryptedArchive\KirikiriUnencryptedArchive.vcxproj /v:m /p:Platform=Win32 /p:Configuration=Release /p:PlatformToolset=v143 "/p:SolutionDir=$krkr\"
if ($LASTEXITCODE -ne 0) { throw "KirikiriUnencryptedArchive build failed" }
$versionDll = Get-ChildItem -Recurse -Filter version.dll | Select-Object -First 1
if (-not $versionDll) { throw "version.dll not produced" }
Pop-Location

# ---- siglus-static-key-tool -------------------------------------------------------
$sigkey = Join-Path $work "siglus_static_key_tool"
Clone "https://github.com/xmoezzz/siglus_static_key_tool" $SIGKEY_COMMIT $sigkey
Push-Location $sigkey
cargo build --release --locked
if ($LASTEXITCODE -ne 0) { throw "siglus-static-key-tool build failed" }
Pop-Location
$mpl = Join-Path $work "MPL-2.0.txt"
Invoke-WebRequest "https://raw.githubusercontent.com/xmoezzz/siglus_rs/$SIGLUS_RS_COMMIT/LICENSE-MPL-2.0" -OutFile $mpl

# ---- copy into the app -------------------------------------------------------------
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path "$out\VNTextPatch", "$out\VNTextProxy", "$out\KirikiriTools", "$out\SiglusKey" | Out-Null
Copy-Item -Recurse "$vntt\Build\VNTextPatch\*" "$out\VNTextPatch\"
Copy-Item "$vntt\Build\VNTextProxy\*.dll" "$out\VNTextProxy\"
Copy-Item "$vntt\LICENSE" "$out\VNTextPatch\LICENSE-VNTranslationTools.txt"
Copy-Item "$vntt\LICENSE" "$out\VNTextProxy\LICENSE-VNTranslationTools.txt"
Copy-Item "$vntt\external\FreeMote\LICENSE*" "$out\VNTextPatch\" -ErrorAction SilentlyContinue
Copy-Item $versionDll.FullName "$out\KirikiriTools\version.dll"
Copy-Item "$krkr\LICENSE" "$out\KirikiriTools\LICENSE-KirikiriTools.txt"
Copy-Item "$sigkey\target\release\siglus-static-key-tool.exe" "$out\SiglusKey\"
Copy-Item $mpl "$out\SiglusKey\LICENSE-MPL-2.0.txt"
"Source: https://github.com/xmoezzz/siglus_static_key_tool/tree/$SIGKEY_COMMIT (MPL-2.0)" | Set-Content "$out\SiglusKey\SOURCE.txt"
Get-ChildItem -Recurse $out | Select-Object FullName, Length | Format-Table -AutoSize
