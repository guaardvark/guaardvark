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

; Keep-alive and the memory limit (the app's settings) write lines to the
; person's %USERPROFILE%\.wslconfig and record exactly what they changed in
; keep-alive.json and memory.json. The app itself undoes them
; (Guaardvark.exe --undo-wsl-settings <file> does that and exits without a
; window): an untouched file is put back as it was, an edited one only loses
; lines that still hold Guaardvark's values. customUnInstall runs before the
; template removes the program files, so the executable is still there.
!macro gvUndoWslSettings
  ${If} ${FileExists} "${GV_DATA}\keep-alive.json"
  ${OrIf} ${FileExists} "${GV_DATA}\memory.json"
    DetailPrint "Undoing Guaardvark's changes to .wslconfig"
    InitPluginsDir
    StrCpy $R2 "$PLUGINSDIR\gv-wsl-settings.txt"
    ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --undo-wsl-settings "$R2"' $R0
    StrCpy $R1 ""
    ClearErrors
    FileOpen $R3 "$R2" r
    ${IfNot} ${Errors}
      FileReadUTF16LE $R3 $R1
      FileClose $R3
    ${EndIf}
    ${If} $R0 == "3"
      MessageBox MB_OK|MB_ICONINFORMATION "$R1" /SD IDOK
    ${ElseIf} $R0 != "0"
      MessageBox MB_OK|MB_ICONEXCLAMATION "Guaardvark could not undo its changes to $PROFILE\.wslconfig (code $R0).$\r$\n$\r$\nOpen that file and remove the lines instanceIdleTimeout=-1, vmIdleTimeout=-1 and memory= that you did not add yourself.$\r$\n$\r$\n$R1" /SD IDOK
    ${EndIf}
  ${EndIf}
!macroend

!macro customUnInstall
  ; The restart-and-resume entry from setup, if a restart never came.
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\RunOnce" "GuaardvarkSetup"

  ${IfNot} ${isUpdated}
    !insertmacro gvUndoWslSettings
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
