import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from personal_ai.commands import CancelledError, Executor, Resolver, builtin_commands, discover_apps
from personal_ai.config import SettingsStore
from personal_ai.models import Action, Command, Resolution, Settings


class CommandTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows administrator app launches")
    def test_apps_request_uac_only_when_windows_requires_elevation(self):
        with tempfile.TemporaryDirectory(prefix="Admin app ") as folder:
            for extension in ("exe", "lnk"):
                with self.subTest(extension=extension):
                    program = Path(folder) / ("App with spaces." + extension)
                    program.touch()
                    arguments = ["a path with spaces", "& $(literal)"]
                    command = Command(
                        name="Admin app",
                        actions=[
                            Action(
                                program=str(program), arguments=arguments, working_directory=folder
                            )
                        ],
                    )
                    required = OSError("The requested operation requires elevation")
                    required.winerror = 740
                    with patch("subprocess.Popen") as spawn, patch("os.startfile") as shell:
                        if extension == "exe":
                            spawn.side_effect = required
                        else:
                            shell.side_effect = [required, None]
                        self.assertIn("Launched", Executor().execute(command))
                        expected = call(
                            str(program), "runas", subprocess.list2cmdline(arguments), folder
                        )
                        self.assertEqual(shell.call_args, expected)
                        self.assertEqual(shell.call_count, 1 if extension == "exe" else 2)

    @unittest.skipUnless(os.name == "nt", "Windows administrator app launches")
    def test_declining_uac_cancels_the_workflow(self):
        required = OSError("Elevation required")
        required.winerror = 740
        declined = OSError("The operation was canceled by the user")
        declined.winerror = 1223
        for shortcut in (False, True):
            with self.subTest(shortcut=shortcut), tempfile.TemporaryDirectory() as folder:
                program = Path(folder) / ("App.lnk" if shortcut else "App.exe")
                program.touch()
                command = Command(name="Admin app", actions=[Action(program=str(program))] * 2)
                with patch("subprocess.Popen", side_effect=required) as spawn:
                    with patch("os.startfile", side_effect=declined) as shell:
                        with self.assertRaisesRegex(
                            CancelledError, "Administrator launch cancelled"
                        ):
                            Executor().execute(command)
                        self.assertEqual(spawn.call_count, 0 if shortcut else 1)
                        self.assertEqual(shell.call_count, 1)

    @unittest.skipUnless(os.name == "nt", "Windows administrator app launches")
    def test_regular_launches_and_unrelated_errors_do_not_request_uac(self):
        command = Command(name="Regular app", actions=[Action(program=sys.executable)])
        with patch("subprocess.Popen") as spawn, patch("os.startfile") as shell:
            self.assertIn("Launched", Executor().execute(command))
            shell.assert_not_called()
            denied = OSError("Access denied")
            denied.winerror = 5
            spawn.side_effect = denied
            with self.assertRaises(OSError) as raised:
                Executor().execute(command)
            self.assertIs(raised.exception, denied)
            shell.assert_not_called()

    def test_model_receives_named_app_and_custom_workflows_before_unrelated_apps(self):
        calculator = Command(
            id="app.calculator",
            name="Calculator",
            actions=[Action(kind="launch", program=sys.executable)],
        )
        other = calculator.model_copy(update={"id": "app.other", "name": "Unrelated program"})

        def classify(text, commands):
            self.assertEqual({command.id for command in commands}, {calculator.id, self.command.id})
            return Resolution(status="match", command_id=calculator.id)

        resolver = Resolver(classify)
        self.assertEqual(
            resolver.resolve(
                "Start the calculator please", [calculator, other, self.command]
            ).command_id,
            calculator.id,
        )

    def test_long_start_menu_shortcut_name_does_not_break_discovery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shortcuts = root / "Microsoft/Windows/Start Menu/Programs"
            shortcuts.mkdir(parents=True)
            name = "A" * 132
            (shortcuts / (name + ".lnk")).touch()
            with patch.dict("os.environ", {"APPDATA": folder, "PROGRAMDATA": folder}):
                self.assertTrue(any(command.name == name for command in discover_apps()))

    def test_discovery_excludes_auxiliary_shortcuts_and_preserves_real_apps(self):
        excluded = [
            "About Java",
            "Install Additional Tools for Node.js",
            "Uninstall Telegram",
            "Git Release Notes",
            "Python 3.12 Manuals (64-bit)",
            "PuTTY Web Site",
            "VLC media player - reset preferences and cache files",
            "Configure Java",
        ]
        included = ["Visual Studio Code", "BCUninstaller", "Epic Games Launcher", "PowerToys"]
        with tempfile.TemporaryDirectory() as folder:
            shortcuts = Path(folder) / "Microsoft/Windows/Start Menu/Programs"
            shortcuts.mkdir(parents=True)
            for name in [*excluded, *included]:
                (shortcuts / (name + ".lnk")).touch()
            with patch.dict("os.environ", {"APPDATA": folder, "PROGRAMDATA": folder}):
                names = {command.name for command in discover_apps()}
            self.assertTrue(names.isdisjoint(excluded))
            self.assertTrue(set(included).issubset(names))

    def setUp(self):
        self.command = Command(
            name="Start work",
            phrases=["begin my workday"],
            actions=[Action(kind="launch", program=sys.executable)],
        )

    def test_settings_survive_restart_and_invalid_save_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder))
            settings = Settings(commands=[self.command])
            store.save(settings)
            self.assertEqual(store.load().commands[0].name, "Start work")
            original = store.path.read_bytes()
            with patch("os.replace", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    store.save(Settings())
            self.assertEqual(store.path.read_bytes(), original)

    def test_duplicate_command_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            Settings(commands=[self.command, self.command])

    def test_exact_phrases_work_offline_and_disabled_commands_do_not_execute(self):
        resolver = Resolver(lambda *_: self.fail("Exact commands should resolve locally"))
        self.assertEqual(
            resolver.resolve("BEGIN MY WORKDAY!", [self.command]).command_id, self.command.id
        )
        disabled = self.command.model_copy(update={"enabled": False})
        resolver = Resolver(lambda *_: Resolution(status="unknown"))
        self.assertEqual(resolver.resolve("begin my workday", [disabled]).status, "unknown")

    def test_model_cannot_select_unregistered_command_or_supply_shell_code(self):
        resolver = Resolver(lambda *_: Resolution(status="match", command_id="invented"))
        with self.assertRaises(ValueError):
            resolver.resolve("do something", [self.command])
        with self.assertRaises(ValueError):
            Resolution.model_validate(
                {"status": "match", "command_id": self.command.id, "shell": "bad command"}
            )

    def test_ambiguous_aliases_require_clarification(self):
        other = Command(
            name="Other workflow", phrases=["begin my workday"], actions=self.command.actions
        )
        resolver = Resolver(lambda *_: self.fail("Duplicate aliases must be handled locally"))
        result = resolver.resolve("begin my workday", [self.command, other])
        self.assertEqual(result.status, "clarify")
        self.assertEqual(set(result.candidates), {self.command.id, other.id})

    def test_power_actions_require_confirmation_at_execution_boundary(self):
        executor = Executor()
        shutdown = builtin_commands()[0]
        with patch("subprocess.run") as run:
            with self.assertRaises(PermissionError):
                executor.execute(shutdown)
            run.assert_not_called()
            run.return_value = subprocess.CompletedProcess([], 0, "", "")
            result = executor.execute(shutdown, power_confirmed=True)
            self.assertIn("requested", result.lower())
            self.assertNotIn("/f", run.call_args.args[0])

    def test_script_receives_literal_arguments_and_failure_stops_sequence(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "result.json"
            command = Command(
                name="Save argument",
                actions=[
                    Action(
                        kind="script",
                        program=sys.executable,
                        arguments=[
                            "-c",
                            "import json,sys,pathlib; "
                            "pathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))",
                            str(target),
                            "spaces & symbols $(literal)",
                        ],
                    )
                ],
            )
            self.assertIn("completed", Executor().execute(command).lower())
            self.assertEqual(json.loads(target.read_text()), ["spaces & symbols $(literal)"])
            command.actions.insert(
                0,
                Action(
                    kind="script", program=sys.executable, arguments=["-c", "raise SystemExit(7)"]
                ),
            )
            target.unlink()
            with self.assertRaisesRegex(RuntimeError, "Step 1.*7"):
                Executor().execute(command)
            self.assertFalse(target.exists())

    def test_corrupt_settings_are_reported_without_overwriting(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder))
            store.path.write_text("broken json")
            with self.assertRaises(ValueError):
                store.load()
            self.assertEqual(store.path.read_text(), "broken json")


if __name__ == "__main__":
    unittest.main()
