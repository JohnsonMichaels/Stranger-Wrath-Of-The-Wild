# betarun.ps1 - build the Cxbx fork, deploy it to the dev folder, relaunch the beta.
#
#   powershell -ExecutionPolicy Bypass -File tools\betarun.ps1            # build + deploy + launch
#   powershell -ExecutionPolicy Bypass -File tools\betarun.ps1 -NoBuild   # deploy + launch only
#   powershell -ExecutionPolicy Bypass -File tools\betarun.ps1 -KeepLog   # do not clear diagnostics.txt
#
# Why a script: this round trip has been typed out by hand dozens of times this
# project, and each time it is four commands that must happen in a fixed order (kill,
# copy, clear the log, launch) with the same three absolute paths. It is also the step
# most likely to be skipped under time pressure - "deploy" in particular, which has
# produced more than one confused half hour spent testing a DLL that was never copied.
#
# Build goes through cmake/MSBuild directly. Bash + cmd /c build.bat hangs and leaves
# detached processes (see memory: build-via-powershell); this is the working path.
param(
    [switch]$NoBuild,
    [switch]$KeepLog
)

$ErrorActionPreference = "Stop"

$cmake   = "C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
$build   = "C:\Users\<you>\SWBeta\src\Cxbx-Reloaded\build"
$dll     = Join-Path $build "bin\Release\cxbxr-emu.dll"
$dev     = "C:\Users\<you>\SWBeta\oddbeta"
$xbe     = "C:\Users\<you>\SWBeta\Game\default.xbe"

if (-not $NoBuild) {
    Write-Output "=== build ==="
    $out = & $cmake --build $build --config Release --target cxbxr-emu 2>&1
    $errors = $out | Select-String -Pattern " error "
    if ($errors) {
        $errors | Select-Object -First 20 | ForEach-Object { Write-Output $_.Line }
        throw "build failed"
    }
    $out | Select-String -Pattern "cxbxr-emu.vcxproj ->" | ForEach-Object { Write-Output $_.Line.Trim() }
}

Write-Output "=== deploy ==="
Get-Process cxbxr-ldr, cxbx -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 1000
Copy-Item $dll (Join-Path $dev "cxbxr-emu.dll") -Force
$stamp = (Get-Item (Join-Path $dev "cxbxr-emu.dll")).LastWriteTime
Write-Output "cxbxr-emu.dll deployed ($stamp)"

if (-not $KeepLog) {
    Remove-Item (Join-Path $dev "diagnostics.txt") -Force -ErrorAction SilentlyContinue
}

Write-Output "=== launch ==="
Start-Process -FilePath (Join-Path $dev "cxbxr-ldr.exe") -ArgumentList "/load", "`"$xbe`"" -WorkingDirectory $dev
Start-Sleep -Seconds 3
$p = Get-Process cxbxr-ldr -ErrorAction SilentlyContinue
if ($p) { Write-Output "running: pid $($p.Id)  '$($p.MainWindowTitle)'" } else { throw "cxbxr-ldr did not start" }
