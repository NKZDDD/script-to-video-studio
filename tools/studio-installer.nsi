Unicode True
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"
Var UpdateMode
Var UpdatePid
Name "Respect Studio"
OutFile "${OUTPUT}"
InstallDir "$LOCALAPPDATA\Programs\Respect-Studio"
InstallDirRegKey HKCU "Software\Respect\Studio" "InstallDir"
RequestExecutionLevel user
CRCCheck force
SetCompressor /SOLID lzma
VIProductVersion "${VERSION}"
VIAddVersionKey "ProductName" "Respect Studio"
VIAddVersionKey "FileDescription" "Respect Studio 安装程序"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "LegalCopyright" "Respect Studio"
BrandingText "Respect Studio"
!define MUI_ABORTWARNING
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_FINISHPAGE_RUN "$INSTDIR\Respect-Studio.exe"
!define MUI_FINISHPAGE_RUN_TEXT "启动 Respect Studio"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

!macro CheckRunning
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\Respect.Studio.Desktop") p.r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    MessageBox MB_OK|MB_ICONEXCLAMATION "Studio 正在运行。请先结束生产任务并关闭窗口，然后重新运行安装器。"
    SetErrorLevel 2
    Abort
  ${EndIf}
!macroend

Function .onInit
  SetShellVarContext current
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "此安装包需要 64 位 Windows 10 或 Windows 11。" /SD IDOK
    Abort
  ${EndIf}
  ${GetParameters} $0
  ClearErrors
  ${GetOptions} $0 "/STUDIOUPDATE=" $UpdatePid
  ${IfNot} ${Errors}
    StrCpy $UpdateMode 1
    SetSilent silent
    ${If} $UpdatePid <= 0
      StrCpy $9 "更新进程参数无效，请从 Studio 重新开始更新。"
      Call UpdateFailed
    ${EndIf}
    ; Wait for the actual process, not just the window or named mutex.
    System::Call 'kernel32::OpenProcess(i 0x100000, i 0, i $UpdatePid) p.r1 ?e'
    Pop $2
    ${If} $1 != 0
      System::Call 'kernel32::WaitForSingleObject(p r1, i 60000) i.r2'
      System::Call 'kernel32::CloseHandle(p r1)'
      ${If} $2 != 0
        StrCpy $9 "Studio 尚未完全退出，更新未开始。请关闭 Studio 后重新运行安装包。"
        Call UpdateFailed
      ${EndIf}
    ${Else}
      ${If} $2 != 87
        StrCpy $9 "无法确认 Studio 已退出，更新未开始。请关闭 Studio 后重新运行安装包。"
        Call UpdateFailed
      ${EndIf}
    ${EndIf}
    IfFileExists "$INSTDIR\Respect-Studio.exe" +3 0
      StrCpy $9 "原安装目录不存在，请手动运行安装包选择安装目录。"
      Call UpdateFailed
  ${EndIf}
  !insertmacro CheckRunning
FunctionEnd

Function UpdateFailed
  SetErrorLevel 2
  MessageBox MB_OK|MB_ICONSTOP "$9"
  Quit
FunctionEnd

Function .onInstSuccess
  ${If} $UpdateMode == 1
    ClearErrors
    Exec '"$INSTDIR\Respect-Studio.exe"'
    ${If} ${Errors}
      StrCpy $9 "更新已完成，但未能重新打开 Studio。请从桌面快捷方式启动。"
      Call UpdateFailed
    ${EndIf}
  ${EndIf}
FunctionEnd

Function .onInstFailed
  ${If} $UpdateMode == 1
    StrCpy $9 "更新未完成，请关闭 Studio 后手动运行已下载的安装包修复。配置和项目保留。"
    Call UpdateFailed
  ${EndIf}
FunctionEnd

Function un.onInit
  SetShellVarContext current
  !insertmacro CheckRunning
FunctionEnd

Section "Studio"
  !insertmacro CheckRunning
  ReadRegStr $0 HKCU "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $0 == ""
    ReadRegStr $0 HKLM "Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${EndIf}
  ${If} $0 == ""
  ${OrIf} $0 == "0.0.0.0"
    InitPluginsDir
    SetOutPath "$PLUGINSDIR"
    File "${BOOTSTRAPPER}"
    DetailPrint "正在安装 Microsoft WebView2（需要联网）..."
    ExecWait '"$PLUGINSDIR\MicrosoftEdgeWebview2Setup.exe" /silent /install' $0
    ${If} $0 != 0
      MessageBox MB_OK|MB_ICONEXCLAMATION "WebView2 未安装成功。请联网后重试，或从微软官网安装 WebView2 Runtime。" /SD IDOK
      Abort
    ${EndIf}
  ${EndIf}
  ClearErrors
  SetOutPath "$INSTDIR"
  ${If} ${Errors}
    Abort
  ${EndIf}
  File /r "${PAYLOAD}\*"
  ${If} ${Errors}
    Abort
  ${EndIf}
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\Respect Studio"
  CreateShortcut "$SMPROGRAMS\Respect Studio\Respect Studio.lnk" "$INSTDIR\Respect-Studio.exe"
  CreateShortcut "$DESKTOP\Respect Studio.lnk" "$INSTDIR\Respect-Studio.exe"
  WriteRegStr HKCU "Software\Respect\Studio" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RespectStudio" "DisplayName" "Respect Studio"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RespectStudio" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RespectStudio" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RespectStudio" "DisplayIcon" "$INSTDIR\Respect-Studio.exe"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RespectStudio" "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RespectStudio" "NoRepair" 1
SectionEnd

Section "Uninstall"
  ; Only remove files shipped by this release. Never recursively delete a data tree.
  !include "${REMOVE_LIST}"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$DESKTOP\Respect Studio.lnk"
  Delete "$SMPROGRAMS\Respect Studio\Respect Studio.lnk"
  RMDir "$SMPROGRAMS\Respect Studio"
  RMDir "$INSTDIR"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\RespectStudio"
  DeleteRegKey HKCU "Software\Respect\Studio"
SectionEnd
