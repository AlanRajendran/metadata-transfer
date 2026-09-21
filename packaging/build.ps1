<#
SPDX-License-Identifier: GPL-3.0-or-later
.SYNOPSIS
    Build Metadata Transfer: tests, notices, PyInstaller freeze, NSIS installer and standalone
    uninstaller, SHA-256 files. (Pattern of the BIOMIS team's Timelapse Video Processing.)

.DESCRIPTION
    1. Find (or with -Install create) a 64-bit Python 3.12+ build environment.
    2. pytest tests (offscreen Qt) unless -SkipTests. A failing test stops the build.
    3. packaging\prepare_assets.py: THIRD_PARTY_NOTICES.txt and version_info.txt.
    4. PyInstaller one-folder build in a short staging folder (%LOCALAPPDATA%\MetadataTransfer-build),
       because long paths inside the bundle would exceed Windows' 260-character limit.
    5. makensis: installer.nsi and uninstaller.nsi. The two programs and their .sha256 files are
       copied to the root of this folder:
         "Metadata Transfer Setup <x.y>.exe", "Uninstall Metadata Transfer.exe"

.PARAMETER Python       Python of the build environment. Default: $env:METADATA_TRANSFER_PYTHON, then
                        ..\..\tools\build-env-<x.y>\Scripts\python.exe (workspace layout), then
                        %LOCALAPPDATA%\MetadataTransfer-dev\<x.y>\.venv\Scripts\python.exe
.PARAMETER Install      Create the build environment if needed and install source\requirements.lock.txt.
.PARAMETER SkipTests    Skip pytest.
.PARAMETER SkipInstaller Stop after the PyInstaller build.
#>
[CmdletBinding()]
param(
    [string]$Python,
    [switch]$Install,
    [switch]$SkipTests,
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$packagingDir = $PSScriptRoot
$appRoot = Split-Path $packagingDir -Parent
$init = Get-Content (Join-Path $appRoot "source\metadata_transfer\__init__.py") -Raw
$Version = [regex]::Match($init, '__version__ = "([^"]+)"').Groups[1].Value
$Short = ($Version -split '\.')[0..1] -join '.'
$devRoot = if ($env:METADATA_TRANSFER_DEV_DIR) { $env:METADATA_TRANSFER_DEV_DIR } else { Join-Path $env:LOCALAPPDATA "MetadataTransfer-dev\$Short" }
Write-Host "== Metadata Transfer $Version" -ForegroundColor Cyan

function Invoke-Checked {
    param([Parameter(Mandatory)][string]$Description, [Parameter(Mandatory)][scriptblock]$Action)
    Write-Host "==> $Description"
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Description failed (exit code $LASTEXITCODE)." }
}

if (-not $Python) {
    $candidates = @()
    if ($env:METADATA_TRANSFER_PYTHON) { $candidates += $env:METADATA_TRANSFER_PYTHON }
    $candidates += (Join-Path (Split-Path $appRoot -Parent) "tools\build-env-$Short\Scripts\python.exe")
    $candidates += (Join-Path $devRoot ".venv\Scripts\python.exe")
    $Python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $Python) { $Python = Join-Path $devRoot ".venv\Scripts\python.exe" }
}
$Python = [IO.Path]::GetFullPath($Python)

if (-not (Test-Path -LiteralPath $Python)) {
    if (-not $Install) { throw "No build environment at '$Python'. Run packaging\build.ps1 -Install once." }
    $base = (Get-Command py -ErrorAction SilentlyContinue)
    $venv = Split-Path (Split-Path $Python -Parent) -Parent
    if ($base) { & py -3 -m venv $venv } else { & python -m venv $venv }
    if ($LASTEXITCODE) { throw "Creating the build environment at $venv failed." }
}
& $Python -c "import sys, struct; sys.exit(0 if sys.version_info[:2] >= (3, 12) and struct.calcsize('P') * 8 == 64 else 1)"
if ($LASTEXITCODE -ne 0) { throw "'$Python' must be a 64-bit Python 3.12 or later." }
& $Python --version

Push-Location $appRoot
try {
    if ($Install) {
        Invoke-Checked "Installing source\requirements.lock.txt" {
            & $Python -m pip install --upgrade pip
            & $Python -m pip install -r "source\requirements.lock.txt" --extra-index-url https://pypi.laboratory-imaging.com/simple
        }
    }
    if (-not $SkipTests) {
        $env:QT_QPA_PLATFORM = "offscreen"
        $pytestBasetemp = Join-Path $devRoot "build\pytest"
        # pytest's TempPathFactory.mkdir()s a given --basetemp directly (no parents=True), so the
        # parent must already exist on a fresh dev root.
        New-Item -ItemType Directory -Force -Path (Split-Path $pytestBasetemp -Parent) | Out-Null
        Invoke-Checked "Running the tests" { & $Python -m pytest "tests" -q --basetemp $pytestBasetemp }
        Remove-Item Env:\QT_QPA_PLATFORM
    }
    Invoke-Checked "Writing THIRD_PARTY_NOTICES.txt and version_info.txt" { & $Python "packaging\prepare_assets.py" }

    $stageRoot = Join-Path $env:LOCALAPPDATA "MetadataTransfer-build"
    $distPath = Join-Path $stageRoot "dist"
    $workPath = Join-Path $stageRoot "pyinstaller"
    Invoke-Checked "Freezing with PyInstaller" {
        & $Python -m PyInstaller --noconfirm --clean --log-level WARN --distpath $distPath --workpath $workPath "packaging\app.spec"
    }
    $payload = Join-Path $distPath "Metadata Transfer"
    foreach ($exe in @("Metadata Transfer.exe", "MetadataTransfer-cli.exe")) {
        if (-not (Test-Path -LiteralPath (Join-Path $payload $exe))) { throw "PyInstaller did not produce $exe." }
    }
    Invoke-Checked "Self-test of the frozen program" {
        & (Join-Path $payload "MetadataTransfer-cli.exe") --diagnostic (Join-Path $stageRoot "diagnostic.json")
    }
    $sizeMB = ((Get-ChildItem -LiteralPath $payload -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)
    Write-Host ("Frozen app: {0} ({1:N0} MB)" -f $payload, $sizeMB)
    if ($SkipInstaller) { Write-Host "==> Done (frozen app only)."; return }

    $makensis = @(
        (Join-Path $devRoot "build\tools\nsis-3.11\makensis.exe"),
        (Join-Path $env:LOCALAPPDATA "EtalumaVP-dev\v1.0\build\tools\nsis-3.11\makensis.exe"),
        "${env:ProgramFiles(x86)}\NSIS\makensis.exe", "$env:ProgramFiles\NSIS\makensis.exe"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
    if (-not $makensis) {
        & (Join-Path $packagingDir "fetch-tools.ps1")
        $makensis = Join-Path $devRoot "build\tools\nsis-3.11\makensis.exe"
    }
    $builtSetup = Join-Path $stageRoot "Metadata Transfer Setup $Short.exe"
    $builtUninstall = Join-Path $stageRoot "Uninstall Metadata Transfer.exe"
    Invoke-Checked "Building the installer" {
        & $makensis /V2 "/DDIST_DIR=$payload" "/DOUTFILE=$builtSetup" "/DPRODUCT_VERSION=$Version" (Join-Path $packagingDir "installer.nsi")
    }
    Invoke-Checked "Building the standalone uninstaller" {
        & $makensis /V2 "/DOUTFILE=$builtUninstall" "/DPRODUCT_VERSION=$Version" (Join-Path $packagingDir "uninstaller.nsi")
    }
    foreach ($built in @($builtSetup, $builtUninstall)) {
        $final = Join-Path $appRoot (Split-Path $built -Leaf)
        Copy-Item -LiteralPath $built -Destination $final -Force
        $hash = (Get-FileHash -LiteralPath $final -Algorithm SHA256).Hash.ToLowerInvariant()
        Set-Content -LiteralPath "$final.sha256" -Value "$hash  $(Split-Path $final -Leaf)" -Encoding ASCII -NoNewline
        Write-Host ("{0}: {1:N1} MB, SHA-256 {2}" -f (Split-Path $final -Leaf), ((Get-Item -LiteralPath $final).Length / 1MB), $hash)
    }
    Write-Host "==> Build complete." -ForegroundColor Green
} finally {
    Pop-Location
}
