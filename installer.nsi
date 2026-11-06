; 多站点视频下载器 NSIS 安装脚本
; 依赖：NSIS 3.x、dist\multi-site-video-downloader\、installer_header.bmp、installer_welcome.bmp

Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "StrFunc.nsh"

!define APP_NAME "多站点视频下载器"
!define APP_VERSION "1.1.8"
!define APP_PUBLISHER "UnknownAlibi"
!define APP_EXE "multi-site-video-downloader.exe"
!define APP_URL "https://github.com/UnknownAlibi/multi-site-video-downloader"
!define APP_ID "{1BA388AD-167E-4B64-9F96-BAE43C79D230}_is1"
!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_ID}"
!define SOURCE_DIR "dist\multi-site-video-downloader"

${StrStr}

Var STARTMENU_FOLDER

Name "${APP_NAME}"
Caption "${APP_NAME} 安装"
OutFile "dist\multi-site-video-downloader-${APP_VERSION}-setup.exe"
InstallDir "$LOCALAPPDATA\Programs\MultiSiteVideoDownloader"
InstallDirRegKey HKCU "${UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel user
BrandingText "${APP_NAME} ${APP_VERSION}"
Icon "icon.ico"
UninstallIcon "icon.ico"

VIProductVersion "${APP_VERSION}.0"
VIAddVersionKey "ProductName" "${APP_NAME}"
VIAddVersionKey "ProductVersion" "${APP_VERSION}"
VIAddVersionKey "FileVersion" "${APP_VERSION}.0"
VIAddVersionKey "CompanyName" "${APP_PUBLISHER}"
VIAddVersionKey "FileDescription" "${APP_NAME} 安装程序"
VIAddVersionKey "LegalCopyright" "GNU GPL v3.0"

SetCompressor /SOLID lzma
ManifestDPIAware true
XPStyle on
CRCCheck on
ShowInstDetails nevershow

!define MUI_ABORTWARNING
!define MUI_ICON "icon.ico"
!define MUI_UNICON "icon.ico"
!define MUI_HEADERIMAGE
!define MUI_HEADERIMAGE_RIGHT
!define MUI_HEADERIMAGE_BITMAP "installer_header.bmp"
!define MUI_WELCOMEFINISHPAGE_BITMAP "installer_welcome.bmp"

!define MUI_WELCOMEPAGE_TITLE "欢迎安装多站点视频下载器"
!define MUI_WELCOMEPAGE_TEXT "本程序会安装到当前用户目录，不需要管理员权限。$\r$\n$\r$\n支持 B站、抖音、YouTube、Vimeo、X 等站点，安装包已包含 ffmpeg。$\r$\n$\r$\n点击“下一步”继续。"

!define MUI_LICENSEPAGE_TEXT_TOP "继续安装前，请阅读 GNU GPL v3 许可协议。程序及其源代码按 GPL-3.0 发布。"
!define MUI_LICENSEPAGE_TEXT_BOTTOM "安装完成后，可在程序目录查看完整的 LICENSE 文件。"
!define MUI_LICENSEPAGE_BUTTON "同意并继续"

!define MUI_DIRECTORYPAGE_TEXT_TOP "选择安装位置。建议保留默认目录；升级时会自动沿用旧版本目录。"
!define MUI_STARTMENUPAGE_TEXT_TOP "选择开始菜单文件夹。"
!define MUI_STARTMENUPAGE_DEFAULTFOLDER "${APP_NAME}"
!define MUI_COMPONENTSPAGE_TEXT_TOP "可选择是否创建桌面快捷方式。"
!define MUI_FINISHPAGE_TITLE "多站点视频下载器安装完成"
!define MUI_FINISHPAGE_TEXT "程序已安装完成。设置、Cookie 和下载历史会保存在程序目录，升级和卸载不会删除这些数据。"
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_EXE}"
!define MUI_FINISHPAGE_RUN_TEXT "启动多站点视频下载器"
!define MUI_FINISHPAGE_NOREBOOTSUPPORT

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_STARTMENU Application $STARTMENU_FOLDER
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"

; 兼容 1.1.5 更新器传入的 Inno 风格 /SILENT 参数
Function .onInit
    ${StrStr} $R0 "$CMDLINE" "/SILENT"
    ${If} $R0 != ""
        SetSilent silent
    ${EndIf}
FunctionEnd

Section "-Application"
    SetShellVarContext current
    SetOutPath "$INSTDIR"

    ; 清理旧版运行时文件，但保留 settings.json / cookies / download / assets
    RMDir /r "$INSTDIR\_internal"
    RMDir /r "$INSTDIR\updates"

    File /r "${SOURCE_DIR}\*"
    File "LICENSE"
    File "THIRD_PARTY_NOTICES.md"

    ; 从 Inno Setup 版本升级时移除旧卸载器，避免出现两个卸载入口
    Delete "$INSTDIR\unins000.exe"
    Delete "$INSTDIR\unins000.dat"

    WriteUninstaller "$INSTDIR\uninstall.exe"

    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${APP_VERSION}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "Publisher" "${APP_PUBLISHER}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "InstallLocation" "$INSTDIR"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_EXE}"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" "$\"$INSTDIR\uninstall.exe$\""
    WriteRegStr HKCU "${UNINSTALL_KEY}" "QuietUninstallString" "$\"$INSTDIR\uninstall.exe$\" /S"
    WriteRegStr HKCU "${UNINSTALL_KEY}" "URLInfoAbout" "${APP_URL}"
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
    WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1

    !insertmacro MUI_STARTMENU_WRITE_BEGIN Application
        CreateDirectory "$SMPROGRAMS\$STARTMENU_FOLDER"
        CreateShortCut "$SMPROGRAMS\$STARTMENU_FOLDER\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
        CreateShortCut "$SMPROGRAMS\$STARTMENU_FOLDER\卸载 ${APP_NAME}.lnk" "$INSTDIR\uninstall.exe"
        WriteRegStr HKCU "${UNINSTALL_KEY}" "StartMenuFolder" "$STARTMENU_FOLDER"
    !insertmacro MUI_STARTMENU_WRITE_END
SectionEnd

Section /o "桌面快捷方式" SEC_DESKTOP
    SetShellVarContext current
    CreateShortCut "$DESKTOP\${APP_NAME}.lnk" "$INSTDIR\${APP_EXE}"
SectionEnd

Section "Uninstall"
    SetShellVarContext current

    ReadRegStr $R0 HKCU "${UNINSTALL_KEY}" "StartMenuFolder"
    ${If} $R0 != ""
        RMDir /r "$SMPROGRAMS\$R0"
    ${Else}
        RMDir /r "$SMPROGRAMS\${APP_NAME}"
    ${EndIf}
    Delete "$DESKTOP\${APP_NAME}.lnk"

    Delete "$INSTDIR\${APP_EXE}"
    Delete "$INSTDIR\uninstall.exe"
    Delete "$INSTDIR\LICENSE"
    Delete "$INSTDIR\THIRD_PARTY_NOTICES.md"
    Delete "$INSTDIR\unins000.exe"
    Delete "$INSTDIR\unins000.dat"
    RMDir /r "$INSTDIR\_internal"

    ; 保留 settings.json、cookies、download、assets 等用户数据
    RMDir "$INSTDIR"

    DeleteRegKey HKCU "${UNINSTALL_KEY}"
SectionEnd
