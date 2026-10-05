Unicode true
ManifestDPIAware true

!include "MUI2.nsh"
!define APP_NAME "Alp 工具箱"
; 版本单源: 默认 0.1.5, 发布构建经 makensis /DAPP_VERSION=x.y.z 覆盖 (见 tools/build_release.cmd)
!ifndef APP_VERSION
!define APP_VERSION "0.1.5"
!endif
!define UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\Alp Toolbox"

Name "${APP_NAME} ${APP_VERSION}"
OutFile "..\dist\Alp工具箱-setup-v${APP_VERSION}.exe"
InstallDir "$PROGRAMFILES64\${APP_NAME}"
InstallDirRegKey HKLM "${UNINST_KEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
!define MUI_ICON "..\assets\app.ico"
!define MUI_UNICON "..\assets\app.ico"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Section "Install"
  ; 托盘常驻: ✕ 只是隐藏到托盘, 进程还在; 运行中的 exe 无法覆写
  DetailPrint "关闭正在运行的 ${APP_NAME}..."
  ExecWait 'taskkill /F /T /IM "Alp工具箱.exe"'
  Sleep 500
  SetOutPath "$INSTDIR"
  File "..\dist\Alp工具箱.exe"
  File "..\README.md"
  File "..\LICENSE"
  ; PawnIO (CPU 温度驱动): 服务已存在则跳过
  ClearErrors
  ReadRegStr $0 HKLM "SYSTEM\CurrentControlSet\Services\PawnIO" "DisplayName"
  ${If} ${Errors}
    SetOutPath "$PLUGINSDIR"
    File /oname=PawnIO_setup.exe "pawnio\PawnIO_setup.exe"
    DetailPrint "安装 PawnIO 驱动 (CPU 温度监控依赖)..."
    ExecWait '"$PLUGINSDIR\PawnIO_setup.exe" /SILENT /NORESTART' $R0
    ; 静默安装可能失败但返回 0 → 再读注册表验证服务确实出现
    ClearErrors
    ReadRegStr $1 HKLM "SYSTEM\CurrentControlSet\Services\PawnIO" "DisplayName"
    ${If} ${Errors}
      DetailPrint "PawnIO 安装失败, CPU 温度功能可能不可用"
    ${ElseIf} $R0 != 0
      DetailPrint "PawnIO 安装失败 (退出码 $R0), CPU 温度功能可能不可用"
    ${EndIf}
    Delete "$PLUGINSDIR\PawnIO_setup.exe"
  ${Else}
    DetailPrint "PawnIO 已安装, 跳过"
  ${EndIf}
  SetOutPath "$INSTDIR"
  CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\Alp工具箱.exe"
  CreateDirectory "$SMPROGRAMS\${APP_NAME}"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\${APP_NAME}.lnk" "$INSTDIR\Alp工具箱.exe"
  CreateShortCut "$SMPROGRAMS\${APP_NAME}\卸载 Alp 工具箱.lnk" "$INSTDIR\Uninstall.exe"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayVersion" "${APP_VERSION}"
  WriteRegStr HKLM "${UNINST_KEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${UNINST_KEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${UNINST_KEY}" "DisplayIcon" "$INSTDIR\Alp工具箱.exe"
  WriteRegStr HKLM "${UNINST_KEY}" "Publisher" "Alp"
  WriteRegDWORD HKLM "${UNINST_KEY}" "EstimatedSize" 54000
SectionEnd

Section "un.Install"
  ; 清理历史版本遗留的自启动项 (计划任务 + Run 键)
  ExecWait 'schtasks /Delete /F /TN "AlpToolbox"'
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "Brb02Toolbox"
  ; 运行中的 exe 删不掉, 先结束进程
  DetailPrint "关闭正在运行的 ${APP_NAME}..."
  ExecWait 'taskkill /F /T /IM "Alp工具箱.exe"'
  Sleep 500
  Delete "$DESKTOP\${APP_NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${APP_NAME}"
  Delete "$INSTDIR\Alp工具箱.exe"
  Delete "$INSTDIR\README.md"
  Delete "$INSTDIR\LICENSE"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
  DeleteRegKey HKLM "${UNINST_KEY}"
  ; PawnIO 为系统驱动, 卸载不删除 (其他程序可能依赖)
SectionEnd
