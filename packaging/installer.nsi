; SPDX-License-Identifier: GPL-3.0-or-later
; Metadata Transfer — per-user NSIS installer (pattern of the BIOMIS team's Timelapse Video Processing).
;
; One stable identity ("MetadataTransfer"): a newer version installs over an older one and keeps the
; settings. Versions 0.1 and 0.2 (Inno Setup, "Metadata Transfer 0.x") are separate programs and
; stay installed until removed in Settings > Apps.
;
; packaging\build.ps1 passes DIST_DIR, OUTFILE and PRODUCT_VERSION.

Unicode True

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "Sections.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"
!include "WinVer.nsh"

!define PRODUCT_NAME "Metadata Transfer"
!ifndef PRODUCT_VERSION
  !define PRODUCT_VERSION "1.0.0"
!endif
!define PRODUCT_PUBLISHER "BIOMIS Team, SATIE laboratory, ENS Paris-Saclay"
!define PRODUCT_COPYRIGHT "Copyright (c) 2026 the Metadata Transfer contributors"
!define PRODUCT_URL "https://github.com/AlanRajendran/metadata-transfer"
!define PRODUCT_EXE "Metadata Transfer.exe"
!define CLI_EXE "MetadataTransfer-cli.exe"
!define INSTALL_REGKEY "Software\Metadata Transfer"
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\MetadataTransfer"
!define DATA_DIR_NAME "Metadata Transfer"

!ifndef DIST_DIR
  !define DIST_DIR "..\dist\Metadata Transfer"
!endif
!ifndef OUTFILE
  !define OUTFILE "..\dist\Metadata Transfer Setup.exe"
!endif

Name "${PRODUCT_NAME}"
OutFile "${OUTFILE}"
InstallDir "$LOCALAPPDATA\Programs\Metadata Transfer"
InstallDirRegKey HKCU "${INSTALL_REGKEY}" "InstallDir"
RequestExecutionLevel user
SetCompressor /SOLID lzma
SetCompressorDictSize 64
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "${PRODUCT_VERSION}.0"
VIAddVersionKey "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey "FileDescription" "${PRODUCT_NAME} installer"
VIAddVersionKey "FileVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "ProductVersion" "${PRODUCT_VERSION}"
VIAddVersionKey "CompanyName" "${PRODUCT_PUBLISHER}"
VIAddVersionKey "LegalCopyright" "${PRODUCT_COPYRIGHT}"

!define MUI_ICON "..\source\metadata_transfer\assets\icon.ico"
!define MUI_UNICON "..\source\metadata_transfer\assets\icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TEXT "This installs ${PRODUCT_NAME} ${PRODUCT_VERSION} for the current Windows user. No administrator rights are needed.$\r$\n$\r$\nIt converts microscopy files between Leica LIF, Evident VSI and Nikon ND2, with their metadata.$\r$\n$\r$\nAn earlier ${PRODUCT_NAME} 1.x is replaced; your settings and channel-name presets are kept.$\r$\n$\r$\nFor research use only; not for diagnostic or clinical use."

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${PRODUCT_EXE}"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!define MUI_COMPONENTSPAGE_NODESC
!define MUI_COMPONENTSPAGE_TEXT_TOP "Your settings, channel-name presets and logs are kept unless you tick the box below. They are shared with Metadata Transfer 0.1 and 0.2."
!insertmacro MUI_UNPAGE_COMPONENTS
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${AtLeastWin10}
    MessageBox MB_ICONSTOP "${PRODUCT_NAME} requires Windows 10 or Windows 11." /SD IDOK
    Abort
  ${EndIf}
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "${PRODUCT_NAME} requires 64-bit Windows 10 or Windows 11." /SD IDOK
    Abort
  ${EndIf}
FunctionEnd

Section "${PRODUCT_NAME} (required)" SEC_APP
  SectionIn RO
  SetShellVarContext current

  ; a newer build replaces the program files of an older one (settings live in %APPDATA%)
  ${If} ${FileExists} "$INSTDIR\_internal\*.*"
    DetailPrint "Replacing the installed program files..."
    RMDir /r "$INSTDIR\_internal"
  ${EndIf}

  SetOutPath "$INSTDIR"
  File /r "${DIST_DIR}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" "$INSTDIR\${PRODUCT_EXE}"
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk" "$INSTDIR\Uninstall.exe"
  CreateShortcut "$DESKTOP\${PRODUCT_NAME}.lnk" "$INSTDIR\${PRODUCT_EXE}"

  WriteRegStr HKCU "${INSTALL_REGKEY}" "InstallDir" "$INSTDIR"
  ${GetSize} "$INSTDIR" "/S=0K" $2 $3 $4
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${UNINST_KEY}" "URLInfoAbout" "${PRODUCT_URL}"
  WriteRegStr HKCU "${UNINST_KEY}" "HelpLink" "${PRODUCT_URL}/issues"
  WriteRegStr HKCU "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\${PRODUCT_EXE}"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "${UNINST_KEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKCU "${UNINST_KEY}" "EstimatedSize" "$2"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1
SectionEnd

Section "un.Program files" SEC_UN_APP
  SectionIn RO
  SetShellVarContext current
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall ${PRODUCT_NAME}.lnk"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${PRODUCT_EXE}"
  Delete "$INSTDIR\${CLI_EXE}"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "${INSTALL_REGKEY}"
SectionEnd

Section /o "un.Also remove my settings, channel-name presets and logs" SEC_UN_REMOVE
  SetShellVarContext current
  ${If} $APPDATA != ""
    DetailPrint "Removing settings, presets and logs..."
    RMDir /r "$APPDATA\${DATA_DIR_NAME}"
  ${EndIf}
SectionEnd

Function un.onInit
  SetShellVarContext current
  ; silent uninstall keeps the settings unless /REMOVESETTINGS=1 is given
  ${un.GetParameters} $R0
  ${un.GetOptions} $R0 "/REMOVESETTINGS=" $R1
  ${If} $R1 == "1"
    !insertmacro SelectSection ${SEC_UN_REMOVE}
  ${EndIf}
FunctionEnd
