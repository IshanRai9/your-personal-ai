import ctypes
import os
import threading
import unittest
from unittest.mock import Mock, patch

from personal_ai.commands import Executor, Resolver, builtin_commands
from personal_ai.tabs import TabTracker, TabWindow, WindowsDesktop, close_active_tab


class Desktop:
    own_process_id = 10

    def __init__(self):
        self.windows = {
            1: TabWindow(1, 20, "chrome.exe", "Chrome_WidgetWin_1"),
            2: TabWindow(2, 30, "code.exe"),
            3: TabWindow(3, 10, "yourpersonalai.exe"),
            4: TabWindow(4, 40, "explorer.exe", "Shell_TrayWnd"),
            5: TabWindow(5, 50, "other.exe"),
            6: TabWindow(6, 20, "chrome.exe", "Chrome_WidgetWin_1"),
        }
        self.current = 1
        self.sent = []
        self.held = False
        self.allow_activation = True
        self.callback = None

    def foreground(self):
        return self.current

    def window(self, handle):
        return self.windows.get(handle)

    def watch(self, callback):
        self.callback = callback

    def stop(self):
        self.callback = None

    def activate(self, handle):
        if self.allow_activation:
            self.current = handle

    def modifiers_down(self):
        return self.held

    def send_close(self, key):
        self.sent.append((self.current, key))


class TabTests(unittest.TestCase):
    def setUp(self):
        self.desktop = Desktop()
        self.cancelled = threading.Event()

    def test_close_tab_aliases_resolve_without_a_model_or_power_confirmation(self):
        resolver = Resolver(lambda *_: self.fail("Exact tab commands should work offline"))
        for phrase in (
            "close active tab",
            "close this tab",
            "close current tab",
            "close browser tab",
        ):
            command = next(item for item in builtin_commands() if item.id == "builtin.close_tab")
            self.assertEqual(resolver.resolve(phrase, builtin_commands()).command_id, command.id)
            self.assertFalse(command.requires_confirmation)

    def test_builtin_execution_routes_to_the_captured_window(self):
        target = self.desktop.windows[1]
        self.desktop.current = 3
        command = next(item for item in builtin_commands() if item.id == "builtin.close_tab")
        with patch("personal_ai.tabs.WindowsDesktop", return_value=self.desktop):
            result = Executor().execute(command, tab_target=target)
        self.assertEqual(result, "Asked Chrome to close its active tab.")
        self.assertEqual(self.desktop.sent, [(1, 0x57)])

    def test_tracker_preserves_previous_app_across_assistant_and_tray_activation(self):
        tracker = TabTracker(self.desktop)
        tracker.start()
        self.desktop.current = 2
        self.desktop.callback(2)
        self.desktop.current = 4
        self.desktop.callback(4)
        self.desktop.current = 3
        self.assertEqual(tracker.capture(), self.desktop.windows[2])
        tracker.stop()
        self.assertIsNone(tracker.last)
        self.assertIsNone(self.desktop.callback)

    def test_each_supported_app_uses_its_close_tab_shortcut(self):
        for executable, window_class, key in (
            ("chrome.exe", "Chrome_WidgetWin_1", 0x57),
            ("code.exe", "", 0x73),
            ("notepad.exe", "", 0x57),
            ("notepad++.exe", "", 0x57),
            ("sublime_text.exe", "", 0x57),
            ("explorer.exe", "CabinetWClass", 0x57),
        ):
            with self.subTest(executable=executable):
                target = TabWindow(1, 20, executable, window_class)
                self.desktop.windows[1] = target
                self.desktop.current = 3
                self.desktop.sent.clear()
                self.assertIn("active tab", close_active_tab(target, self.cancelled, self.desktop))
                self.assertEqual(self.desktop.sent, [(1, key)])

    def test_switching_to_another_window_cancels_instead_of_closing_the_old_tab(self):
        target = self.desktop.windows[1]
        for current in (2, 5, 6):
            self.desktop.current = current
            with self.assertRaisesRegex(ValueError, "active window changed"):
                close_active_tab(target, self.cancelled, self.desktop)
            self.assertEqual(self.desktop.sent, [])

    def test_reused_handle_and_unsupported_apps_never_receive_input(self):
        target = self.desktop.windows[1]
        self.desktop.windows[1] = TabWindow(1, 99, "chrome.exe", "Chrome_WidgetWin_1")
        with self.assertRaisesRegex(ValueError, "no longer available"):
            close_active_tab(target, self.cancelled, self.desktop)
        tracker = TabTracker(self.desktop)
        self.desktop.current = 5
        self.assertEqual(tracker.capture(), self.desktop.windows[5])
        with self.assertRaisesRegex(ValueError, "not supported"):
            close_active_tab(tracker.last, self.cancelled, self.desktop)
        self.assertEqual(self.desktop.sent, [])

    @unittest.skipUnless(os.name == "nt", "Windows input ABI")
    def test_native_shortcut_sends_one_chord_and_releases_keys_after_partial_input(self):
        desktop = WindowsDesktop()
        captured = []

        def send(count, events, size):
            self.assertEqual(size, 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
            captured.append(
                [(event.value.keyboard.vk, event.value.keyboard.flags) for event in events]
            )
            return count

        with patch.object(desktop.user, "SendInput", Mock(side_effect=send)):
            desktop.send_close(0x57)
        self.assertEqual(captured, [[(0x11, 0), (0x57, 0), (0x57, 2), (0x11, 2)]])
        captured.clear()

        def partial(count, events, size):
            send(count, events, size)
            return 2

        with patch.object(desktop.user, "SendInput", Mock(side_effect=partial)):
            with self.assertRaisesRegex(OSError, "partly sent"):
                desktop.send_close(0x73)
        self.assertEqual(captured[-1], [(0x73, 2), (0x11, 2)])
        with patch.object(desktop.user, "SendInput", Mock(return_value=0)) as blocked:
            with self.assertRaisesRegex(OSError, "blocked"):
                desktop.send_close(0x57)
            self.assertEqual(blocked.call_count, 1)

    def test_missing_target_own_window_and_shell_window_never_receive_input(self):
        for target in (None, self.desktop.windows[3], self.desktop.windows[4]):
            with self.assertRaises(ValueError):
                close_active_tab(target, self.cancelled, self.desktop)
        self.assertEqual(self.desktop.sent, [])

    def test_held_modifiers_cancellation_and_failed_focus_do_not_send_input(self):
        target = self.desktop.windows[1]
        self.desktop.current = 3
        self.desktop.held = True
        with self.assertRaisesRegex(ValueError, "Release"):
            close_active_tab(target, self.cancelled, self.desktop)
        self.desktop.held = False
        self.cancelled.set()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            close_active_tab(target, self.cancelled, self.desktop)
        self.cancelled.clear()
        self.desktop.allow_activation = False
        with self.assertRaisesRegex(RuntimeError, "Could not activate"):
            close_active_tab(target, self.cancelled, self.desktop)
        self.assertEqual(self.desktop.sent, [])
