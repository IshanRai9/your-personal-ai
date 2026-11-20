import threading
import unittest
from unittest.mock import Mock, patch

from personal_ai.commands import Executor, Resolver, bind_request, builtin_commands
from personal_ai.tabs import TabWindow


def resolve(text):
    commands = bind_request(text, builtin_commands())
    resolution = Resolver(None).resolve(text, commands)
    return next(command for command in commands if command.id == resolution.command_id)


class DesktopCommandTests(unittest.TestCase):
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
