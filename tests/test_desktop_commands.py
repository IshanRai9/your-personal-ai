import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from personal_ai.commands import Executor, Resolver, bind_request, builtin_commands
from personal_ai.tabs import TabWindow


def resolve(text):
    commands = bind_request(text, builtin_commands())
    resolution = Resolver(None).resolve(text, commands)
    return next(command for command in commands if command.id == resolution.command_id)


class DesktopCommandTests(unittest.TestCase):
    def test_capture_writes_one_window_to_a_unique_local_png(self):
        from PIL import Image

        target = TabWindow(22, 30, "example.exe")
        desktop = Mock(own_process_id=20)
        desktop.window.return_value = target
        desktop.user.IsIconic.return_value = False
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("personal_ai.screenshots.WindowsDesktop", return_value=desktop),
            patch(
                "personal_ai.screenshots.ImageGrab.grab",
                side_effect=lambda **_: Image.new("RGB", (23, 17)),
            ) as grab,
        ):
            for _ in range(2):
                Executor(Path(folder)).execute(resolve("capture this window"), tab_target=target)
            grab.assert_called_with(window=22)
            files = list((Path(folder) / "Screenshots").glob("*.png"))
            self.assertEqual(len(files), 2)
            with Image.open(files[0]) as image:
                self.assertEqual(image.size, (23, 17))

    def test_file_lookup_preserves_extension_and_reports_both_locations(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Archive").mkdir()
            for base in (root, root / "Archive"):
                (base / "budget.xlsx").touch()
            with patch("personal_ai.commands.personal_folders", return_value={"downloads": root}):
                result = Executor().execute(resolve("find budget dot xlsx"))
                self.assertEqual(
                    set(result.splitlines()),
                    {str(root / "budget.xlsx"), str(root / "Archive" / "budget.xlsx")},
                )
        self.assertEqual(resolve("Open Downloads").actions[0].arguments, ["Downloads"])

    def test_switch_matches_open_app_and_snap_targets_captured_window(self):
        target = TabWindow(12, 30, "chrome.exe")
        desktop = Mock(own_process_id=20)
        desktop.visible_windows.return_value = [target]
        desktop.window.return_value = target
        desktop.foreground.return_value = target.handle
        desktop.modifiers_down.return_value = False
        with patch("personal_ai.window_commands.WindowsDesktop", return_value=desktop):
            executor = Executor()
            executor.execute(resolve("Switch to Google Chrome."))
            desktop.activate.assert_called_once_with(12)
            executor.execute(resolve("snap this window left"), tab_target=target)
            desktop.send_shortcut.assert_called_once_with(0x5B, 0x25)
            executor.execute(resolve("minimize this"), tab_target=target)
            desktop.user.ShowWindow.assert_called_once_with(12, 6)
            with self.assertRaisesRegex(ValueError, "No open window"):
                executor.execute(resolve("switch to missing app"))
            desktop.window.return_value = None
            with self.assertRaisesRegex(ValueError, "no longer available"):
                executor.execute(
                    resolve("snap right"), cancelled=threading.Event(), tab_target=target
                )
