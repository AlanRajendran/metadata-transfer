<#
SPDX-License-Identifier: GPL-3.0-or-later
.SYNOPSIS
    Fetch the pinned NSIS 3.11 build tool (same mirror and SHA-256 as the BIOMIS team's
    Timelapse Video Processing) into %LOCALAPPDATA%\MetadataTransfer-dev\<x.y>\build\tools.

.DESCRIPTION
    build.ps1 calls this only when no makensis.exe is found. Idempotent: an archive with the right
    hash is not downloaded again, an extracted NSIS is not extracted again. A download with the
    wrong hash is deleted and the script stops.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$appRoot = Split-Path $PSScriptRoot -Parent
$init = Get-Content (Join-Path $appRoot "source\metadata_transfer\__init__.py") -Raw
$Short = ([regex]::Match($init, '__version__ = "([^"]+)"').Groups[1].Value -split '\.')[0..1] -join '.'
$devRoot = if ($env:METADATA_TRANSFER_DEV_DIR) { $env:METADATA_TRANSFER_DEV_DIR } else { Join-Path $env:LOCALAPPDATA "MetadataTransfer-dev\$Short" }
$toolsDir = Join-Path $devRoot "build\tools"
New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null

$nsisUrl = "https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.11/nsis-3.11.zip"
$nsisSha256 = "C7D27F780DDB6CFFB4730138CD1591E841F4B7EDB155856901CDF5F214394FA1"
$archive = Join-Path $toolsDir "nsis-3.11.zip"
$makensis = Join-Path $toolsDir "nsis-3.11\makensis.exe"

function Test-Hash([string]$Path) {
    (Test-Path -LiteralPath $Path) -and ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $nsisSha256)
}

if (-not (Test-Hash $archive)) {
    Write-Host "Downloading NSIS 3.11 from $nsisUrl ..."
    Invoke-WebRequest -Uri $nsisUrl -OutFile $archive
    if (-not (Test-Hash $archive)) {
        Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
        throw "The NSIS archive does not match the pinned SHA-256 ($nsisSha256); it was deleted."
    }
}
if (-not (Test-Path -LiteralPath $makensis)) {
    Expand-Archive -LiteralPath $archive -DestinationPath $toolsDir -Force
    if (-not (Test-Path -LiteralPath $makensis)) { throw "makensis.exe missing after extracting $archive." }
}
Write-Host "NSIS ready at $makensis"
