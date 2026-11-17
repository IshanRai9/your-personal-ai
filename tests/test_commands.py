import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_ai.commands import Executor, Resolver, builtin_commands, discover_apps
from personal_ai.config import SettingsStore
from personal_ai.models import Action, Command, Resolution, Settings


class CommandTests(unittest.TestCase):
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
