; SPDX-License-Identifier: GPL-3.0-or-later
; Metadata Transfer — standalone uninstaller ("Uninstall Metadata Transfer.exe"), kept next to the
; installer so the program can be removed without the Start-menu shortcut. It finds the installation
; through the registry key installer.nsi writes and runs the installed Uninstall.exe.
;
;   /S                  silent
;   /REMOVESETTINGS=1   also remove %APPDATA%\Metadata Transfer (settings, presets, logs; shared
;                       with Metadata Transfer 0.1 and 0.2). Default: kept. Interactive runs ask.
;
; Exit codes: 0 = not installed or uninstalled; 1 = the installed uninstaller failed.

Unicode True

!include "LogicLib.nsh"
!include "FileFunc.nsh"

!define PRODUCT_NAME "Metadata Transfer"
!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "1.0.0"
!endif
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\MetadataTransfer"
!define DATA_DIR_NAME "Metadata Transfer"

!ifndef OUTFILE
  !define OUTFILE "..\dist\Uninstall Metadata Transfer.exe"
!endif

Name "${PRODUCT_NAME} Uninstaller"
OutFile "${OUTFILE}"
RequestExecutionLevel user
Icon "..\source\metadata_transfer\assets\icon.ico"
ShowInstDetails nevershow

VIProductVersion "${PRODUCT_VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT_NAME} Uninstaller"
VIAddVersionKey "FileDescription" "Standalone uninstaller for ${PRODUCT_NAME}"
VIAddVersionKey "FileVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "CompanyName" "BIOMIS Team, SATIE laboratory, ENS Paris-Saclay"
VIAddVersionKey "LegalCopyright" "Copyright (c) 2026 the Metadata Transfer contributors"

Function .onInit
  SetShellVarContext current
  ReadRegStr $0 HKCU "${UNINST_KEY}" "UninstallString"
  ReadRegStr $1 HKCU "${UNINST_KEY}" "InstallLocation"
  ${If} $0 == ""
  ${OrIf} $1 == ""
    ${IfNot} ${Silent}
      MessageBox MB_ICONINFORMATION|MB_OK "${PRODUCT_NAME} is not installed for this user.$\r$\n$\r$\nVersions 0.1 and 0.2 are removed in Windows Settings > Apps (Metadata Transfer 0.1 / 0.2)." /SD IDOK
    ${EndIf}
    SetErrorLevel 0
    Quit
  ${EndIf}

  ${IfNot} ${FileExists} "$1\Uninstall.exe"
    DeleteRegKey HKCU "${UNINST_KEY}"
    RMDir /r "$1"
    ${IfNot} ${Silent}
      MessageBox MB_ICONINFORMATION|MB_OK "The installation record of ${PRODUCT_NAME} was damaged (its uninstaller was missing). The leftover entry and folder were removed." /SD IDOK
    ${EndIf}
    SetErrorLevel 0
    Quit
  ${EndIf}

  ${GetParameters} $R0
  ${GetOptions} $R0 "/REMOVESETTINGS=" $R1
  StrCpy $2 "0" ; 0 = keep settings (default), 1 = remove
  ${If} $R1 == "1"
    StrCpy $2 "1"
  ${ElseIfNot} ${Silent}
    MessageBox MB_YESNO|MB_ICONQUESTION "Remove ${PRODUCT_NAME}?$\r$\n$\r$\nYour settings, channel-name presets and logs in $APPDATA\${DATA_DIR_NAME} are kept (they are shared with versions 0.1 and 0.2)." /SD IDYES IDYES go
    SetErrorLevel 0
    Quit
    go:
  ${EndIf}

  ${If} $2 == "1"
    ExecWait '$0 /S /REMOVESETTINGS=1 _?=$1' $3
  ${Else}
    ExecWait '$0 /S _?=$1' $3
  ${EndIf}
  Delete "$1\Uninstall.exe"
  RMDir "$1"

  ${If} $3 == 0
    ${IfNot} ${Silent}
      MessageBox MB_ICONINFORMATION|MB_OK "${PRODUCT_NAME} was uninstalled."
    ${EndIf}
    SetErrorLevel 0
  ${Else}
    ${IfNot} ${Silent}
      MessageBox MB_ICONEXCLAMATION|MB_OK "The uninstaller of ${PRODUCT_NAME} returned exit code $3."
    ${EndIf}
    SetErrorLevel 1
  ${EndIf}
  Quit
FunctionEnd

Section "Placeholder" SEC_PLACEHOLDER
  SectionIn RO
  SetOutPath "$TEMP"
SectionEnd
