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
  ; ⚠️ 2026-10-27 用户实测: taskkill 之后仍报「无法打开要写入的文件 ...\Alp工具箱.exe」。
  ; 本机验证过「运行中的 exe 可以重命名」, 所以失败另有原因:
  ;   1) 上次安装残留了 Alp工具箱.old.exe, 重命名撞名 → 改用唯一名字;
  ;   2) 杀软/反作弊(如三角洲的 ACE)在游戏运行时会锁文件 → 只能提示用户先退游戏。
  ; 策略: 删除 → 重命名(唯一名) → 每次间隔 400ms 重试 8 次, 全失败才报错。
  StrCpy $1 0
  ${Do}
    ClearErrors
    Delete "$INSTDIR\Alp工具箱.exe"
    ${IfNot} ${Errors}
      ${ExitDo}
    ${EndIf}
    ClearErrors
    Rename "$INSTDIR\Alp工具箱.exe" "$INSTDIR\Alp工具箱.old$1.exe"
    ${IfNot} ${Errors}
      Delete /REBOOTOK "$INSTDIR\Alp工具箱.old$1.exe"
      ${ExitDo}
    ${EndIf}
    Sleep 400
    IntOp $1 $1 + 1
  ${LoopUntil} $1 >= 8

  ${If} ${FileExists} "$INSTDIR\Alp工具箱.exe"
    MessageBox MB_ICONSTOP|MB_OK "无法替换 Alp工具箱.exe。$\r$\n$\r$\n常见原因:$\r$\n  · 程序还在运行 —— 请在托盘图标上右键「退出」$\r$\n  · 正在玩带反作弊的游戏 (如三角洲 ACE) —— 它会锁住可执行文件, 请先退出游戏$\r$\n  · 杀毒软件正在扫描 —— 稍等片刻重试$\r$\n$\r$\n处理完后点「重试」即可继续安装。"
    Abort
  ${EndIf}
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
    ; PawnIO 安装器只认短横线参数 (-install/-silent), 斜杠 /SILENT 会弹 "Unknown argument"
    ExecWait '"$PLUGINSDIR\PawnIO_setup.exe" -install -silent' $R0
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
