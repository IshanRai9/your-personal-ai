"""Remember the user's window and request one tab close through Windows input."""

import ctypes
import os
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass

TAB_APPS = {
    "chrome.exe": ("Chrome", 0x57),
    "msedge.exe": ("Edge", 0x57),
    "firefox.exe": ("Firefox", 0x57),
    "brave.exe": ("Brave", 0x57),
    "opera.exe": ("Opera", 0x57),
    "vivaldi.exe": ("Vivaldi", 0x57),
    "arc.exe": ("Arc", 0x57),
    "zen.exe": ("Zen", 0x57),
    "code.exe": ("VS Code", 0x73),
    "code - insiders.exe": ("VS Code Insiders", 0x73),
    "notepad.exe": ("Notepad", 0x57),
    "notepad++.exe": ("Notepad++", 0x57),
    "sublime_text.exe": ("Sublime Text", 0x57),
    "explorer.exe": ("File Explorer", 0x57),
}
_SHELL_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW"}
_SHELL_APPS = {"startmenuexperiencehost.exe", "searchhost.exe", "shellexperiencehost.exe"}


@dataclass(frozen=True)
class TabWindow:
    handle: int
    process_id: int
    executable: str
    window_class: str = ""


class WindowsDesktop:
    def __init__(self):
        if os.name != "nt":
            raise OSError("Tab commands require Windows.")
        self.own_process_id = os.getpid()
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, arguments, result in [
            ("GetForegroundWindow", [], wintypes.HWND),
            (
                "GetWindowThreadProcessId",
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                wintypes.DWORD,
            ),
            ("GetClassNameW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
            ("IsIconic", [wintypes.HWND], wintypes.BOOL),
            ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL),
            ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
            ("GetAsyncKeyState", [ctypes.c_int], ctypes.c_short),
            ("UnhookWinEvent", [wintypes.HANDLE], wintypes.BOOL),
        ]:
            function = getattr(self.user, name)
            function.argtypes, function.restype = arguments, result
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.hook = None
        self.callback = None

    def foreground(self) -> int:
        return int(self.user.GetForegroundWindow() or 0)

    def window(self, handle: int) -> TabWindow | None:
        process_id = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
        if not process_id.value:
            return None
        name = ctypes.create_unicode_buffer(256)
        self.user.GetClassNameW(handle, name, len(name))
        executable = ""
        process = self.kernel.OpenProcess(0x1000, False, process_id.value)
        if process:
            try:
                path = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(path))
                if self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                    executable = os.path.basename(path.value).casefold()
            finally:
                self.kernel.CloseHandle(process)
        return TabWindow(handle, process_id.value, executable, name.value)

    def watch(self, remember):
        callback_type = ctypes.WINFUNCTYPE(
            None,
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.HWND,
            wintypes.LONG,
            wintypes.LONG,
            wintypes.DWORD,
            wintypes.DWORD,
        )
        self.callback = callback_type(lambda _hook, _event, window, *_: remember(int(window or 0)))
        self.user.SetWinEventHook.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HMODULE,
            callback_type,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.user.SetWinEventHook.restype = wintypes.HANDLE
        # Foreground changes only; skip the assistant's own windows. Qt pumps the events.
        self.hook = self.user.SetWinEventHook(3, 3, None, self.callback, 0, 0, 2)
        if not self.hook:
            self.callback = None
            raise OSError("Windows could not track the active window for tab commands.")

    def stop(self):
        if self.hook:
            self.user.UnhookWinEvent(self.hook)
            self.hook = None
            self.callback = None

    def activate(self, handle: int):
        if self.user.IsIconic(handle):
            self.user.ShowWindow(handle, 9)
        self.user.SetForegroundWindow(handle)

    def visible_windows(self) -> list[TabWindow]:
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user.GetWindow.restype = wintypes.HWND
        windows = []

        @callback_type
        def collect(handle, _):
            if self.user.IsWindowVisible(handle) and not self.user.GetWindow(handle, 4):
                window = self.window(handle)
                if (
                    window
                    and window.executable
                    and window.process_id != self.own_process_id
                    and window.window_class not in _SHELL_CLASSES
                    and window.executable not in _SHELL_APPS
                ):
                    windows.append(window)
            return True

        if not self.user.EnumWindows(collect, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return windows

    def modifiers_down(self) -> bool:
        return any(
            self.user.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5B, 0x5C)
        )

    def send_close(self, key: int):
        self.send_shortcut(0x11, key)

    def send_shortcut(self, modifier: int, key: int):
        class Keyboard(ctypes.Structure):
            _fields_ = [
                ("vk", wintypes.WORD),
                ("scan", wintypes.WORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Mouse(ctypes.Structure):
            _fields_ = [
                ("x", wintypes.LONG),
                ("y", wintypes.LONG),
                ("data", wintypes.DWORD),
                ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("extra", ctypes.c_size_t),
            ]

        class Value(ctypes.Union):
            _fields_ = [("keyboard", Keyboard), ("mouse", Mouse)]

        class Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("value", Value)]

        def event(vk, flags=0):
            return Input(1, Value(keyboard=Keyboard(vk, 0, flags, 0, 0)))

        send = self.user.SendInput
        send.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
        send.restype = wintypes.UINT
        extended = 1 if key in (0x25, 0x27) else 0
        mod_flags = 1 if modifier == 0x5B else 0
        events = (Input * 4)(
            event(modifier, mod_flags),
            event(key, extended),
            event(key, extended | 2),
            event(modifier, mod_flags | 2),
        )
        sent = send(len(events), events, ctypes.sizeof(Input))
        if sent != len(events):
            if sent:
                releases = (Input * 2)(event(key, extended | 2), event(modifier, mod_flags | 2))
                send(len(releases), releases, ctypes.sizeof(Input))
                raise OSError(
                    "Windows only partly sent the shortcut. Check the app before retrying."
                )
            raise OSError(
                "Windows blocked the shortcut. Check the app's administrator permissions."
            )


class TabTracker:
    def __init__(self, desktop=None):
        self.desktop = desktop or WindowsDesktop()
        self.last: TabWindow | None = None

    def start(self):
        self.desktop.watch(self.remember)
        self.remember()

    def remember(self, handle: int | None = None):
        window = self.desktop.window(self.desktop.foreground() if handle is None else handle)
        if window and (
            window.process_id == self.desktop.own_process_id
            or window.window_class in _SHELL_CLASSES
            or window.executable in _SHELL_APPS
        ):
            return
        self.last = window

    def capture(self) -> TabWindow | None:
        self.remember()
        return self.last

    def stop(self):
        self.desktop.stop()
        self.last = None


def close_active_tab(target: TabWindow | None, cancelled: threading.Event, desktop=None) -> str:
    desktop = desktop or WindowsDesktop()
    if not target or target.process_id == desktop.own_process_id:
        raise ValueError("Switch to the tab you want to close, then ask again.")
    app = TAB_APPS.get(target.executable)
    if not app or (target.executable == "explorer.exe" and target.window_class != "CabinetWClass"):
        raise ValueError(
            "Tab closing is not supported in this app. Switch to a browser or supported editor."
        )
    if desktop.window(target.handle) != target:
        raise ValueError(
            "The original window is no longer available. Ask again from the tab you want."
        )
    foreground = desktop.window(desktop.foreground())
    if (
        not foreground
        or foreground.process_id not in {desktop.own_process_id, target.process_id}
        or (foreground.process_id == target.process_id and foreground.handle != target.handle)
    ):
        raise ValueError("The active window changed. Ask again from the tab you want to close.")
    if desktop.modifiers_down():
        raise ValueError("Release Ctrl, Shift, Alt and Windows keys, then try again.")
    if cancelled.is_set():
        raise RuntimeError("Tab command cancelled.")
    desktop.activate(target.handle)
    deadline = time.monotonic() + 0.5
    while desktop.foreground() != target.handle:
        if cancelled.wait(0.01) or time.monotonic() >= deadline:
            raise RuntimeError(
                "Could not activate the original tab window. No close shortcut was sent."
            )
    if cancelled.is_set() or desktop.modifiers_down() or desktop.window(target.handle) != target:
        raise RuntimeError("Tab command cancelled because the window or keyboard state changed.")
    if desktop.foreground() != target.handle:
        raise RuntimeError("The active window changed. No close shortcut was sent.")
    desktop.send_close(app[1])
    return f"Asked {app[0]} to close its active tab."
