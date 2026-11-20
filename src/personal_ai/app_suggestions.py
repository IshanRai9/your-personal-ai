"""Find launchable Windows apps and propose corrections locally."""

import os
import re
import winreg
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import pythoncom
from win32com.shell import shell, shellcon

from personal_ai.commands import Executor, normalize
from personal_ai.tabs import TAB_APPS, TabWindow, WindowsDesktop
from personal_ai.window_commands import app_key, close_windows
from personal_ai.windows import external_program

YES_ANSWERS = {"yes", "yeah", "yep", "yes please", "correct"}
NO_ANSWERS = {"no", "nope", "cancel", "no thanks", "never mind"}


@dataclass(frozen=True)
class App:
    name: str
    path: str = ""
    pidl: tuple[bytes, ...] = ()
    windows: tuple[TabWindow, ...] = ()

    def run(self, cancelled):
        if cancelled.is_set():
            raise RuntimeError("Command cancelled.")
        if self.windows:
            return close_windows(WindowsDesktop(), self.name, self.windows, cancelled)
        if self.pidl:
            pythoncom.CoInitialize()
            try:
                with external_program():
                    shell.ShellExecuteEx(
                        fMask=shellcon.SEE_MASK_IDLIST | shellcon.SEE_MASK_FLAG_DDEWAIT,
                        lpIDList=list(self.pidl),
                        nShow=1,
                    )
            finally:
                pythoncom.CoUninitialize()
        else:
            Executor._launch(self.path, [], None)
        return f"Asked Windows to open {self.name}."


def registered_executables():
    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            key = winreg.EnumKey(root, index)
                            value = winreg.QueryValue(root, key)
                            path = Path(os.path.expandvars(value.strip('"')))
                            if (
                                path.is_absolute()
                                and path.suffix.lower() == ".exe"
                                and path.is_file()
                            ):
                                yield App(Path(key).stem, str(path))
                        except OSError:
                            continue
            except OSError:
                continue


def installed_apps(shortcuts):
    apps = {c.name.casefold(): App(c.name, c.actions[0].program) for c in shortcuts}
    pythoncom.CoInitialize()
    try:
        desktop = shell.SHGetDesktopFolder()
        base = desktop.ParseDisplayName(0, None, "shell:AppsFolder")[1]
        folder = desktop.BindToObject(base, None, shell.IID_IShellFolder)
        for child in folder.EnumObjects(0, shellcon.SHCONTF_NONFOLDERS):
            try:
                name = folder.GetDisplayNameOf(child, 0)
            except pythoncom.com_error:
                continue
            if name:
                apps.setdefault(name.casefold(), App(name, pidl=tuple(base + child)))
    except pythoncom.com_error:
        pass  # Start Menu shortcuts and App Paths are available independently.
    finally:
        pythoncom.CoUninitialize()
    for app in registered_executables():
        apps.setdefault(app.name.casefold(), app)
    return tuple(apps.values())


def start_catalog(shortcuts) -> Future:
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="App catalog")
    try:
        return pool.submit(installed_apps, shortcuts)
    finally:
        pool.shutdown(wait=False)


def active_apps(windows):
    groups = {}
    for window in windows:
        groups.setdefault(window.executable, []).append(window)
    return [
        App(TAB_APPS[exe][0] if exe in TAB_APPS else Path(exe).stem, windows=tuple(group))
        for exe, group in groups.items()
    ]


def ranked_apps(query, apps):
    left = app_key(query)

    def similarity(name):
        right = app_key(name)
        if left == right:
            return 1.0
        if min(len(left), len(right)) < 3:
            return 0.0
        words = normalize(name).split()
        variants = [right, *(word for word in words if len(word) >= 3)]
        return max(SequenceMatcher(None, left, value).ratio() for value in variants)

    scores = []
    for app in apps:
        names = [app.name]
        if app.windows:
            names.append(app.windows[0].executable)
        if app.path and app.path.casefold().endswith(".exe"):
            names.append(Path(app.path).stem)
        scores.append((max(map(similarity, names)), app))
    scores.sort(key=lambda item: (-item[0], item[1].name.casefold()))
    if not scores:
        return []
    cutoff = max(0.70, scores[0][0] - 0.055)
    return [app for score, app in scores if score >= cutoff][:5]


@dataclass
class AppQuestion:
    verb: str
    query: str
    choices: list[App]

    def prompt(self):
        if not self.choices:
            if self.verb == "close":
                return "I couldn't find an open app window to close."
            return f"I couldn't find an installed app matching “{self.query}”."
        if len(self.choices) == 1:
            return f"Did you mean {self.verb} {self.choices[0].name}? Answer yes or no."
        options = "\n".join(f"{i}. {app.name}" for i, app in enumerate(self.choices, 1))
        return (
            f"Which app did you mean to {self.verb}? Choose a name or number, or cancel.\n{options}"
        )

    def selection(self, answer):
        exact_name = next(
            (app for app in self.choices if app.name.casefold() == answer.strip().casefold()), None
        )
        if exact_name:
            return exact_name
        answer = normalize(answer)
        if len(self.choices) == 1 and answer in YES_ANSWERS:
            return self.choices[0]
        for index, app in enumerate(self.choices, 1):
            numbers = {str(index), f"number {index}"}
            if index <= 10:
                word = "one two three four five six seven eight nine ten".split()[index - 1]
                numbers.update((word, f"number {word}"))
            if answer in numbers:
                return app
        names = [app for app in self.choices if answer == normalize(app.name)]
        return names[0] if len(names) == 1 else None


def close_question(name, windows):
    active = active_apps(windows)
    choices = ranked_apps(name, active) or sorted(active, key=lambda app: app.name.casefold())
    return AppQuestion("close", name, choices)


def app_request(text):
    return re.fullmatch(
        r"(?:(?:could|can|would) you )?(?:please )?(open|launch|start|close) "
        r"(?:(?:the )?(?:app|application) )?(.+?)(?:[, ]+please)?",
        " ".join(text.strip().rstrip(".!?").split()),
        re.IGNORECASE,
    )
