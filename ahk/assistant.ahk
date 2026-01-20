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
