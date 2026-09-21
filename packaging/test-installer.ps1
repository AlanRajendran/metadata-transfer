<#
SPDX-License-Identifier: GPL-3.0-or-later
.SYNOPSIS
    Install, check, reinstall and uninstall Metadata Transfer from the built installer (per user).

.DESCRIPTION
    1. Silent install (/S). Checks the programs, the Start-menu and desktop shortcuts and the
       Settings > Apps registry entry.
    2. Runs the installed "MetadataTransfer-cli.exe --diagnostic" (writes and reads back an ND2
       and a VSI).
    3. Silent reinstall over the first install; the settings folder must be untouched.
    4. With -Uninstall: the standalone "Uninstall Metadata Transfer.exe" /S; checks that program
       files, shortcuts and registry entry are gone and the settings folder is kept.
    Versions 0.1 and 0.2 (other registry keys) are never touched. Prints a JSON summary.

.PARAMETER Setup      Path of "Metadata Transfer Setup <x.y>.exe"; the standalone uninstaller must be
                      next to it.
.PARAMETER Uninstall  Remove the installation at the end (default: keep it installed).
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Setup,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

$Setup = [IO.Path]::GetFullPath($Setup)
$standalone = Join-Path (Split-Path $Setup -Parent) "Uninstall Metadata Transfer.exe"
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\MetadataTransfer"
$installDir = Join-Path $env:LOCALAPPDATA "Programs\Metadata Transfer"
$desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "Metadata Transfer.lnk"
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Metadata Transfer\Metadata Transfer.lnk"
$dataDir = Join-Path $env:APPDATA "Metadata Transfer"
$report = [ordered]@{}

function Check([string]$Name, [bool]$Ok) {
    $report[$Name] = $Ok
    Write-Host ("  {0} {1}" -f ($(if ($Ok) { "OK  " } else { "FAIL" })), $Name)
    if (-not $Ok) { throw "Check failed: $Name" }
}

Write-Host "==> Installing (silent)"
$p = Start-Process -FilePath $Setup -ArgumentList "/S" -Wait -PassThru
Check "installer exit code 0" ($p.ExitCode -eq 0)
Check "GUI program installed" (Test-Path -LiteralPath (Join-Path $installDir "Metadata Transfer.exe"))
Check "command-line program installed" (Test-Path -LiteralPath (Join-Path $installDir "MetadataTransfer-cli.exe"))
Check "licence and notices installed" ((Test-Path (Join-Path $installDir "_internal\LICENSE")) -and (Test-Path (Join-Path $installDir "_internal\THIRD_PARTY_NOTICES.txt")))
Check "no Qt Virtual Keyboard" (-not (Get-ChildItem -LiteralPath $installDir -Recurse -Filter "*VirtualKeyboard*" -ErrorAction SilentlyContinue))
Check "desktop shortcut" (Test-Path -LiteralPath $desktop)
Check "Start-menu shortcut" (Test-Path -LiteralPath $startMenu)
$entry = Get-ItemProperty -Path $key
Check "Settings > Apps entry" ($entry.DisplayName -eq "Metadata Transfer")
$report["version"] = $entry.DisplayVersion

Write-Host "==> Self-test of the installed program"
$diag = Join-Path $env:TEMP "mt-diagnostic.json"
& (Join-Path $installDir "MetadataTransfer-cli.exe") --diagnostic $diag | Out-Null
Check "diagnostic self-test (ND2 -> VSI -> ND2)" ($LASTEXITCODE -eq 0)
$report["diagnostic"] = Get-Content $diag -Raw | ConvertFrom-Json

$sentinel = Join-Path $dataDir "installer-test-sentinel.txt"
New-Item -ItemType Directory -Force -Path $dataDir | Out-Null
Set-Content -LiteralPath $sentinel -Value "kept" -Encoding ASCII
Write-Host "==> Reinstalling over the installation"
$p = Start-Process -FilePath $Setup -ArgumentList "/S" -Wait -PassThru
Check "reinstall exit code 0" ($p.ExitCode -eq 0)
Check "settings kept by reinstall" (Test-Path -LiteralPath $sentinel)

if ($Uninstall) {
    Write-Host "==> Uninstalling with the standalone uninstaller"
    $p = Start-Process -FilePath $standalone -ArgumentList "/S" -Wait -PassThru
    Check "uninstaller exit code 0" ($p.ExitCode -eq 0)
    Check "program files removed" (-not (Test-Path -LiteralPath (Join-Path $installDir "Metadata Transfer.exe")))
    Check "shortcuts removed" (-not (Test-Path -LiteralPath $desktop) -and -not (Test-Path -LiteralPath $startMenu))
    Check "registry entry removed" (-not (Test-Path -LiteralPath $key))
    Check "settings kept by uninstall" (Test-Path -LiteralPath $sentinel)
}
Remove-Item -LiteralPath $sentinel -Force -ErrorAction SilentlyContinue
$report | ConvertTo-Json -Depth 5
