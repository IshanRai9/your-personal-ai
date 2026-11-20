import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from personal_ai.files import PERSONAL_FOLDERS, find_files, open_folder, personal_folders
from personal_ai.models import Action, Command, Resolution
from personal_ai.tabs import TabWindow, close_active_tab
from personal_ai.window_commands import control_window
from personal_ai.windows import external_process, external_program

_AUXILIARY_SHORTCUT = re.compile(
    r"^(?:about|install|uninstall|configure|help|documentation|read\s*me|"
    r"check for updates|report a problem|reload configuration|"
    r"sample (?:desktop|uwp) apps|tools for (?:desktop|uwp) apps)\b"
    r"|\b(?:uninstall(?:er)?|installer|documentation|read\s*me|release notes|"
    r"manuals?|module docs|web\s*site|reset preferences)\b"
)


def normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def builtin(kind, name, *phrases):
    return Command(
        id=f"builtin.{kind}", name=name, phrases=list(phrases), actions=[Action(kind=kind)]
    )


# Bind literal user input only. The classifier continues to return registered IDs.
PARAMETERS = {
    "switch_window": (r"switch to (.+)", "App name"),
    "open_folder": (
        r"open (?:(?:the |my )?(" + "|".join(PERSONAL_FOLDERS) + r")(?: folder)?|folder (.+))",
        "Folder name or local path",
    ),
    "find_file": (r"find (?:the file |file )?(.+)", "File name"),
}


def with_argument(command, value):
    action = command.actions[0].model_copy(update={"arguments": [value]})
    return command.model_copy(update={"actions": [action]})


def bind_request(text, commands):
    bound = []
    for command in commands:
        spec = PARAMETERS.get(command.actions[0].kind)
        match = re.fullmatch(spec[0], text.strip(), re.IGNORECASE) if spec else None
        if match:
            value = next(group for group in match.groups() if group).strip().rstrip(".!?").strip()
            if value:
                command = with_argument(command, value)
                command.phrases = [*command.phrases, text]
        bound.append(command)
    return bound


def builtin_commands() -> list[Command]:
    return [
        Command(
            id="builtin.shutdown",
            name="Shut down",
            description="Shut down this computer.",
            phrases=["shutdown", "shut down my computer", "turn off my pc"],
            actions=[Action(kind="shutdown")],
        ),
        Command(
            id="builtin.restart",
            name="Restart",
            description="Restart this computer.",
            phrases=["restart my computer", "reboot", "reboot my pc"],
            actions=[Action(kind="restart")],
        ),
        Command(
            id="builtin.close_tab",
            name="Close active tab",
            description="Close one active tab in the browser or tabbed app you were using.",
            phrases=[
                "close tab",
                "close this tab",
                "close current tab",
                "close the active tab",
                "close browser tab",
                "close the current tab",
                "close active browser tab",
            ],
            actions=[Action(kind="close_tab")],
        ),
        builtin("switch_window", "Switch to app"),
        builtin("minimize_window", "Minimize this window", "minimize this", "minimize window"),
        builtin("snap_left", "Snap this window left", "snap left", "snap window left"),
        builtin("snap_right", "Snap this window right", "snap right", "snap window right"),
        builtin("open_folder", "Open folder"),
        builtin("find_file", "Find file"),
    ]


def discover_apps() -> list[Command]:
    paths: dict[str, tuple[str, str]] = {}
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    for name, executable in [("Notepad", "notepad.exe"), ("Calculator", "calc.exe")]:
        path = system / executable
        if path.exists():
            paths[str(path).casefold()] = (name, str(path))
    for variable in ("APPDATA", "PROGRAMDATA"):
        root = os.environ.get(variable)
        if not root:
            continue
        folder = Path(root) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for path in folder.rglob("*.lnk"):
            if _AUXILIARY_SHORTCUT.search(normalize(path.stem)):
                continue
            paths[str(path).casefold()] = (path.stem, str(path))
    commands = []
    names = set()
    for name, path in paths.values():
        if name.casefold() in names:
            continue
        names.add(name.casefold())
        identifier = hashlib.sha256(path.casefold().encode()).hexdigest()[:12]
        commands.append(
            Command(
                id="app." + identifier,
                name=name,
                description=f"Open {name}.",
                phrases=[f"open {name}", f"launch {name}", f"start {name}"],
                actions=[Action(kind="launch", program=path)],
            )
        )
    return sorted(commands, key=lambda command: command.name.casefold())


class Resolver:
    def __init__(self, classify: Callable[[str, list[Command]], Resolution]):
        self.classify = classify

    def resolve(self, text: str, commands: list[Command]) -> Resolution:
        available = [command for command in commands if command.enabled]
        request = normalize(text)
        if not request:
            return Resolution(status="unknown")
        exact = [
            command.id
            for command in available
            if request in {normalize(phrase) for phrase in [command.name, *command.phrases]}
        ]
        if len(exact) == 1:
            return Resolution(status="match", command_id=exact[0])
        if len(exact) > 1:
            return Resolution(status="clarify", candidates=exact)
        if not available:
            return Resolution(status="unknown")
        common_words = {
            "the",
            "and",
            "for",
            "app",
            "apps",
            "windows",
            "microsoft",
            "open",
            "start",
            "run",
        }
        words = set(request.split())
        named_apps = {
            command.id
            for command in available
            if command.id.startswith("app.")
            and (set(normalize(command.name).split()) - common_words) & words
        }
        offered = [
            command
            for command in available
            if not command.id.startswith("app.") or command.id in named_apps
        ] or available
        result = self.classify(text, offered)
        if result.status == "unknown" and not named_apps and len(offered) < len(available):
            offered = available
            result = self.classify(text, offered)
        known = {command.id for command in offered}
        if result.command_id and result.command_id not in known:
            raise ValueError(
                "The model selected an unavailable command. Please rephrase your request."
            )
        if not set(result.candidates).issubset(known):
            raise ValueError(
                "The model returned unavailable choices. Please rephrase your request."
            )
        if result.status == "clarify" and not result.candidates:
            return Resolution(status="unknown")
        return result


class CancelledError(Exception):
    pass


class Executor:
    def execute(
        self,
        command: Command,
        power_confirmed: bool = False,
        progress: Callable[[str], None] = lambda _: None,
        cancelled: threading.Event | None = None,
        tab_target: TabWindow | None = None,
    ) -> str:
        if not command.enabled:
            raise ValueError("This command is disabled.")
        if command.requires_confirmation and not power_confirmed:
            raise PermissionError("Confirm this power action before running it.")
        cancelled = cancelled or threading.Event()
        results = []
        for index, action in enumerate(command.actions, start=1):
            if cancelled.is_set():
                raise CancelledError("Command cancelled. Completed steps have already run.")
            progress(f"Running step {index} of {len(command.actions)}")
            if action.kind == "open_folder":
                results.append(open_folder(action.arguments[0]))
                continue
            if action.kind == "find_file":
                results.append(
                    find_files(action.arguments[0], personal_folders().values(), cancelled)
                )
                continue
            if action.kind in {"switch_window", "minimize_window", "snap_left", "snap_right"}:
                name = action.arguments[0] if action.arguments else ""
                results.append(control_window(action.kind, name, tab_target, cancelled))
                continue
            if action.kind == "close_tab":
                results.append(close_active_tab(tab_target, cancelled))
                continue
            if action.kind in {"shutdown", "restart"}:
                flag = "/s" if action.kind == "shutdown" else "/r"
                executable = (
                    Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "shutdown.exe"
                )
                # /t 0 avoids Windows implicitly forcing apps closed with a nonzero timeout.
                result = subprocess.run(
                    [str(executable), flag, "/t", "0"],
                    capture_output=True,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if result.returncode:
                    raise RuntimeError("Windows could not complete the power action.")
                results.append(f"{command.name} requested.")
                continue
            program = os.path.expandvars(os.path.expanduser(action.program))
            resolved = shutil.which(program) or (program if Path(program).is_file() else None)
            if not resolved:
                raise FileNotFoundError(f"Step {index} could not find {action.program}.")
            working_directory = os.path.expandvars(action.working_directory) or None
            if working_directory and not Path(working_directory).is_dir():
                raise FileNotFoundError(f"Step {index} has a missing working directory.")
            if action.kind == "launch":
                self._launch(resolved, action.arguments, working_directory)
                results.append(f"Launched {Path(resolved).stem}.")
            else:
                try:
                    output = self._run_script(
                        resolved, action.arguments, working_directory, cancelled
                    )
                except RuntimeError as error:
                    raise RuntimeError(f"Step {index} failed. {error}") from error
                results.append(f"Step {index} completed." + (f"\n{output}" if output else ""))
        return "\n".join(results)

    @staticmethod
    def _launch(program: str, arguments: list[str], directory: str | None) -> None:
        try:
            if Path(program).suffix.casefold() == ".lnk":
                Executor._shell_launch(program, arguments, directory, "open")
            else:
                external_process([program, *arguments], cwd=directory, stdin=subprocess.DEVNULL)
        except OSError as error:
            if os.name != "nt" or getattr(error, "winerror", None) != 740:
                raise
            Executor._shell_launch(program, arguments, directory, "runas")

    @staticmethod
    def _shell_launch(program: str, arguments: list[str], directory: str | None, verb: str) -> None:
        if os.name != "nt":
            raise OSError("Windows app activation requires Windows.")
        try:
            with external_program():
                os.startfile(program, verb, subprocess.list2cmdline(arguments), directory or "")
        except OSError as error:
            if getattr(error, "winerror", None) == 1223:
                raise CancelledError("Administrator launch cancelled.") from error
            raise

    @staticmethod
    def _run_script(
        program: str, arguments: list[str], directory: str | None, cancelled: threading.Event
    ) -> str:
        with tempfile.TemporaryFile() as output:
            with external_process(
                [program, *arguments],
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ) as process:
                while process.poll() is None:
                    if cancelled.wait(0.1):
                        if os.name == "nt":
                            subprocess.run(
                                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                capture_output=True,
                                check=False,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                            )
                        else:
                            process.terminate()
                        process.wait()
                        raise CancelledError("Script stopped. Completed steps have already run.")
                output.seek(0)
                text = output.read(65536).decode("utf-8", errors="replace").strip()
                if process.returncode:
                    raise RuntimeError(f"Script exited with code {process.returncode}.\n{text}")
                return text
