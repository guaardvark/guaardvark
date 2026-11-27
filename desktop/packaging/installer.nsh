; Hooks into electron-builder's NSIS template (options in electron-builder.yml).
;
; Guaardvark itself runs in a WSL distribution named "Guaardvark" whose disk
; lives in %LOCALAPPDATA%\Guaardvark\wsl (platform/windows.js). Uninstalling
; the app keeps it unless the person says otherwise, so a reinstall finds
; everything where it was. An update runs the old uninstaller too
; (${isUpdated}); that never asks and never removes anything. A silent
; uninstall keeps it (/SD IDNO).

!include "LogicLib.nsh"

!define GV_DATA "$LOCALAPPDATA\Guaardvark"
!define GV_DISTRO "Guaardvark"

; The installer is a 32-bit program: $SYSDIR would be redirected to SysWOW64,
; which has no wsl.exe. Sysnative reaches the real System32.
!macro gvFindWsl _out
  StrCpy ${_out} "$WINDIR\Sysnative\wsl.exe"
  ${IfNot} ${FileExists} "${_out}"
    StrCpy ${_out} "$WINDIR\System32\wsl.exe"
  ${EndIf}
!macroend

!macro customUnInstall
  ; The restart-and-resume entry from setup, if a restart never came.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\RunOnce" "GuaardvarkSetup"

  ${IfNot} ${isUpdated}
    ${If} ${FileExists} "${GV_DATA}\wsl\ext4.vhdx"
      MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also remove Guaardvark's Linux system?$\r$\n$\r$\nIt holds Guaardvark and everything made or downloaded in it: chats, documents, pictures, videos, models and settings.$\r$\n$\r$\n${GV_DATA}\wsl$\r$\n$\r$\nChoose No to keep it. Installing Guaardvark again picks it up as it was." /SD IDNO IDNO gv_keep_distro
        !insertmacro gvFindWsl $R0
        DetailPrint "Stopping Guaardvark's Linux system"
        nsExec::Exec '"$R0" --terminate ${GV_DISTRO}'
        Pop $R1
        DetailPrint "Removing Guaardvark's Linux system"
        nsExec::Exec '"$R0" --unregister ${GV_DISTRO}'
        Pop $R1
        ${If} $R1 == "0"
          RMDir /r "${GV_DATA}"
        ${Else}
          MessageBox MB_OK|MB_ICONEXCLAMATION "Guaardvark's Linux system could not be removed (code $R1).$\r$\n$\r$\nTo remove it yourself, open PowerShell and run:$\r$\nwsl --unregister ${GV_DISTRO}" /SD IDOK
        ${EndIf}
      gv_keep_distro:
    ${EndIf}
  ${EndIf}
!macroend
