import threading
import unittest
from unittest.mock import Mock, patch

from personal_ai.app_suggestions import App, AppQuestion, app_request, close_question, ranked_apps
from personal_ai.tabs import TabWindow


class AppSuggestionTests(unittest.TestCase):
    def test_ranking_handles_transposed_letters_and_rejects_weak_matches(self):
        apps = [App("Discord"), App("Google Chrome"), App("Calculator")]
        self.assertEqual(ranked_apps("discrod", apps), [apps[0]])
        self.assertEqual(ranked_apps("crome", apps), [apps[1]])
        self.assertEqual(ranked_apps("unrelated zebras", apps), [])
        self.assertEqual(app_request("Could you open the app Discord, please?")[2], "Discord")
        self.assertIsNone(app_request("don't open Discord"))

    def test_close_lists_distinct_active_apps_and_preserves_the_window_snapshot(self):
        windows = [
            TabWindow(1, 2, "discord.exe"),
            TabWindow(3, 2, "discord.exe"),
            TabWindow(4, 5, "chrome.exe"),
        ]
        question = close_question("unrelated zebras", windows)
        self.assertEqual([app.name for app in question.choices], ["Chrome", "discord"])
        self.assertIsNone(question.selection("yes"))
        choice = question.selection("number two")
        self.assertEqual(choice.windows, tuple(windows[:2]))
        desktop = Mock()
        desktop.window.side_effect = [TabWindow(1, 99, "discord.exe"), windows[1]]
        with patch("personal_ai.app_suggestions.WindowsDesktop", return_value=desktop):
            choice.run(threading.Event())
        desktop.user.PostMessageW.assert_called_once_with(3, 0x10, 0, 0)
        desktop.visible_windows.assert_not_called()
        self.assertFalse(close_question("Discord", []).choices)

    def test_similar_names_require_an_explicit_selection(self):
        question = AppQuestion("open", "notpad", [App("Notepad"), App("Notepad++")])
        self.assertIsNone(question.selection("yes"))
        self.assertEqual(question.selection("Notepad++"), question.choices[1])
        self.assertEqual(question.selection("one"), question.choices[0])

    def test_shell_entry_launches_only_the_catalogued_identifier(self):
        app = App("Store app", pidl=(b"folder", b"child"))
        with patch("personal_ai.app_suggestions.shell.ShellExecuteEx") as launch:
            app.run(threading.Event())
            self.assertEqual(launch.call_args.kwargs["lpIDList"], list(app.pidl))
