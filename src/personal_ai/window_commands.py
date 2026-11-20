import re
import time
from threading import Event

from personal_ai.tabs import TAB_APPS, TabWindow, WindowsDesktop


def app_key(name: str) -> str:
    key = re.sub(r"[\s_-]+", "", name.casefold().removesuffix(".exe"))
    aliases = {"googlechrome": "chrome", "microsoftedge": "msedge", "visualstudiocode": "code"}
    for exe, (display, _) in TAB_APPS.items():
        aliases[re.sub(r"[\s_-]+", "", display.casefold())] = re.sub(
            r"[\s_-]+", "", exe.removesuffix(".exe")
        )
    return aliases.get(key, key)


class NoOpenApp(ValueError):
    def __init__(self, name, windows):
        super().__init__(f"No open window found for {name}.")
        self.name = name
        self.windows = windows


def named_windows(desktop, name):
    active = desktop.visible_windows()
    matches = [w for w in active if app_key(w.executable) == app_key(name)]
    if not matches:
        raise NoOpenApp(name, active)
    return matches


def check_window(desktop, target):
    if not target or target.process_id == desktop.own_process_id:
        raise ValueError("First select the window you want to use.")
    if desktop.window(target.handle) != target:
        raise ValueError("The selected window is no longer available.")


def close_app(name, cancelled):
    desktop = WindowsDesktop()
    windows = named_windows(desktop, name)
    return close_windows(desktop, name, windows, cancelled)


def close_windows(desktop, name, windows, cancelled):
    sent = 0
    for window in windows:
        if cancelled.is_set():
            break
        if desktop.window(window.handle) == window:
            if desktop.user.PostMessageW(window.handle, 0x0010, 0, 0):  # WM_CLOSE
                sent += 1
    if not sent:
        raise RuntimeError(f"Could not deliver a close request to {name}.")
    return f"Asked {name} to close {sent} of {len(windows)} open windows."


def activate(desktop, target, cancelled):
    check_window(desktop, target)
    desktop.activate(target.handle)
    deadline = time.monotonic() + 0.5
    while desktop.foreground() != target.handle:
        if cancelled.wait(0.01) or time.monotonic() >= deadline:
            raise RuntimeError("Windows could not bring the requested window forward.")


def control_window(kind: str, name: str, target: TabWindow | None, cancelled: Event) -> str:
    desktop = WindowsDesktop()
    if kind == "switch_window":
        target = named_windows(desktop, name)[0]
        activate(desktop, target, cancelled)
        return f"Switched to {name}."
    check_window(desktop, target)
    if kind == "minimize_window":
        desktop.user.ShowWindow(target.handle, 6)  # SW_MINIMIZE
        if not desktop.user.IsIconic(target.handle):
            raise RuntimeError("Windows could not minimize this window.")
        return "Minimized the window."
    activate(desktop, target, cancelled)
    check_window(desktop, target)
    if cancelled.is_set() or desktop.foreground() != target.handle or desktop.modifiers_down():
        raise ValueError("The active window or keyboard state changed. Try again.")
    left = kind == "snap_left"
    desktop.send_shortcut(0x5B, 0x25 if left else 0x27)
    return f"Asked Windows to snap the window {'left' if left else 'right'}."
