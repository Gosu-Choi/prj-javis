#SingleInstance Force
SendMode "Input"
SetTitleMatchMode 2

action := A_Args.Length >= 1 ? A_Args[1] : ""
param := A_Args.Length >= 2 ? A_Args[2] : ""

if (action = "page_down") {
    Sleep 150
    Send "{PgDn}"
    ExitApp
}

if (action = "page_up") {
    Sleep 150
    Send "{PgUp}"
    ExitApp
}

if (action = "scroll_down") {
    Sleep 50
    Send "{WheelDown 6}"
    ExitApp
}

if (action = "scroll_up") {
    Sleep 50
    Send "{WheelUp 6}"
    ExitApp
}

if (action = "notepad_append") {
    note := param
    if !WinExist("ahk_exe notepad.exe") {
        Send "#r"
        WinWaitActive "ahk_class #32770", , 2
        if WinActive("ahk_class #32770") {
            SendText "notepad"
            Send "{Enter}"
        } else {
            Run "notepad.exe"
        }
        WinWaitActive "ahk_exe notepad.exe", , 3
    } else {
        WinActivate "ahk_exe notepad.exe"
        WinWaitActive "ahk_exe notepad.exe", , 1
    }
    Send "{End}"
    Send "{Enter}"
    if (note != "") {
        SendText note
        Send "{Enter}"
    }
    ExitApp
}

if (action = "find") {
    Send "^f"
    if (param != "") {
        Sleep 120
        SendText param
        Sleep 80
        Send "{Enter}"
    }
    ExitApp
}

if (action = "open_url") {
    Run 'chrome.exe "' . param . '"'
    ExitApp
}

if (action = "activate_window") {
    if (param != "") {
        if WinActive(param) {
            ExitApp
        }
        WinRestore param
        WinActivate param
        WinWaitActive param, , 1
        WinMaximize param
    }
    ExitApp
}

if (action = "activate_window_no_resize") {
    if (param != "") {
        if WinActive(param) {
            ExitApp
        }
        WinRestore param
        WinActivate param
        WinWaitActive param, , 1
    }
    ExitApp
}

if (action = "zotero_library") {
    Send "^1"
    ExitApp
}

if (action = "zotero_tab") {
    if (param != "") {
        Send "^" param
    }
    ExitApp
}

ExitApp
