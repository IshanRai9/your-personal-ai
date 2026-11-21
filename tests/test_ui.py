import gc
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtTest import QTest
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from personal_ai.app_suggestions import App
from personal_ai.commands import builtin_commands
from personal_ai.config import SettingsStore
from personal_ai.editor import ActionDialog, CommandDialog
from personal_ai.instance import InstanceServer, server_name
from personal_ai.models import Action, Command, Resolution, Settings
from personal_ai.runtime import LocalRuntime
from personal_ai.settings_ui import SettingsDialog
from personal_ai.tabs import TabWindow
from personal_ai.ui import MainWindow, apply_style


class DesktopTests(unittest.TestCase):
    def test_voice_clarification_finishes_playback_before_listening(self):
        window = MainWindow(self.runtime)
        window.settings.speak_replies = True
        window.speaker = Mock()
        window.speaker.state.return_value = QTextToSpeech.State.Ready
        options = [
            c for c in builtin_commands() if c.id in {"builtin.close_tab", "builtin.close_app"}
        ]
        result = Resolution(status="clarify", candidates=[c.id for c in options])
        window.voice_request = True
        with (
            patch.object(window, "start_recording") as listen,
            patch.object(window, "execute") as run,
        ):
            window.handle_resolution(result, options)
            QTest.qWait(20)  # WinRT can initially remain Ready after say().
            listen.assert_not_called()
            prompt = window.speaker.say.call_args.args[0]
            self.assertIn("1. Close active tab", prompt)
            self.assertIn("2. Close app", prompt)
            window.speaker_state_changed(QTextToSpeech.State.Speaking)
            window.speaker_state_changed(QTextToSpeech.State.Ready)
            QTest.qWait(20)
            listen.assert_called_once_with(automatic=True)
            window.handle_voice("Number one.", window.answer_question)
            run.assert_called_once_with(options[0])
        window.close()

    def test_spoken_missing_app_name_binds_only_the_answer(self):
        window = MainWindow(self.runtime)
        window.voice_request = True
        close = next(c for c in builtin_commands() if c.id == "builtin.close_app")
        with patch.object(window, "reply") as say:
            window.execute(close)
            say.assert_called_once_with("Which app do you want to close?", prompt=True)
            with patch.object(window, "execute") as run:
                window.handle_voice("Calculator.", window.answer_question)
                self.assertEqual(run.call_args.args[0].actions[0].arguments, ["Calculator"])
        window.close()

    def test_radio_consent_starts_on_ui_thread_and_result_reaches_worker(self):
        import threading

        window = MainWindow(self.runtime)
        ui_thread = threading.get_ident()
        access = object()

        def consent():
            self.assertEqual(threading.get_ident(), ui_thread)
            return access

        with (
            patch("personal_ai.ui.request_radio_access", side_effect=consent) as request,
            patch("personal_ai.commands.set_radio", return_value="Wi-Fi is off.") as radio,
        ):
            window.request.setText("turn wifi off")
            window.submit()
            self.wait_for_idle(window)
            request.assert_called_once()
            self.assertEqual(radio.call_args.args[:3], ("wifi", False, access))
            self.assertIn("Wi-Fi is off.", window.activity.toPlainText())
        window.close()

    def test_hidden_app_is_only_launched_after_explicit_confirmation(self):
        from concurrent.futures import Future

        hidden = Command(
            id="app.hidden",
            name="Example Studio",
            phrases=["open Example Studio"],
            actions=[Action(program="example.exe")],
        )
        catalog = Future()
        catalog.set_result([App(hidden.name, hidden.actions[0].program)])
        SettingsStore(self.directory).save(Settings(hidden_apps=[hidden.id]))
        with (
            patch("personal_ai.ui.discover_apps", return_value=[hidden]),
            patch("personal_ai.ui.start_catalog", return_value=catalog),
        ):
            window = MainWindow(self.runtime)
        self.assertNotIn(hidden.id, [c.id for c in window.available_commands()])
        with (
            patch.object(window.resolver, "classify", side_effect=AssertionError("unexpected LLM")),
            patch("personal_ai.app_suggestions.Executor._launch") as launch,
        ):
            for request, response in [
                ("open Example Studio", "no"),
                ("open Exampel Studio", "yes"),
            ]:
                window.resolve_text(request)
                self.wait_for_idle(window)
                self.assertIn("Did you mean open Example Studio?", window.activity.toPlainText())
                launch.assert_not_called()
                window.resolve_text(response)
                self.wait_for_idle(window)
            launch.assert_called_once_with("example.exe", [], None)
            window.resolve_text("yes")
            launch.assert_called_once()
        window.close()

    def test_spoken_close_correction_waits_for_a_second_recording(self):
        window = MainWindow(self.runtime)
        live = TabWindow(4, 9, "discord.exe")
        from unittest.mock import Mock

        desktop = Mock()
        desktop.visible_windows.return_value = [live]
        desktop.window.return_value = live
        with (
            patch("personal_ai.window_commands.WindowsDesktop", return_value=desktop),
            patch("personal_ai.app_suggestions.WindowsDesktop", return_value=desktop),
            patch.object(QApplication, "beep"),
            patch.object(window.microphone, "start"),
            patch.object(window.microphone, "stop", return_value=np.zeros(16000)),
            patch.object(window.transcriber, "transcribe", return_value="Yes."),
        ):
            window.handle_voice("Close Discrod.")
            self.wait_for_idle(window)
            QTest.qWait(25)
            self.assertTrue(window.automatic_recording)
            self.assertIn("Did you mean close discord?", window.activity.toPlainText())
            desktop.user.PostMessageW.assert_not_called()
            window.command_endpoint.last_speech = 1600
            window.stop_recording()
            self.wait_for_idle(window)
            desktop.user.PostMessageW.assert_called_once_with(4, 0x10, 0, 0)
        window.close()

    def test_tab_target_is_captured_before_understanding_and_forwarded_to_execution(self):
        window = MainWindow(self.runtime)
        original = TabWindow(111, 222, "chrome.exe")
        different = TabWindow(333, 444, "notepad.exe")
        with (
            patch.object(window.tab_tracker, "capture", return_value=original) as capture,
            patch.object(window.executor, "execute", return_value="Close requested") as execute,
        ):
            window.request.setText("close active tab")
            window.submit()
            capture.return_value = different
            self.wait_for_idle(window)
            execute.assert_called_once()
            self.assertEqual(execute.call_args.kwargs["tab_target"], original)
            self.assertEqual(execute.call_args.args[0].id, "builtin.close_tab")
        window.close()

    def test_close_hides_to_tray_and_quit_exits(self):
        window = MainWindow(self.runtime)
        window.install_tray()
        window.show()
        window.close()
        self.assertFalse(window.isVisible())
        self.assertFalse(window.closing)
        self.assertTrue(window.tray.isVisible())
        menu = window.tray.contextMenu()
        next(action for action in menu.actions() if action.text() == "Open assistant").trigger()
        self.assertTrue(window.isVisible())
        next(action for action in menu.actions() if action.text() == "Quit").trigger()
        self.assertTrue(window.closing)
        self.assertFalse(window.tray.isVisible())

    def test_second_launch_restores_window_after_complete_local_message(self):
        window = MainWindow(self.runtime)
        server = InstanceServer(self.directory, window)
        client = QLocalSocket()
        client.connectToServer(server_name(self.directory))
        self.assertTrue(client.waitForConnected(1000))
        QTest.qWait(20)
        client.write(b"sh")
        client.flush()
        QTest.qWait(20)
        self.assertFalse(window.isVisible())
        client.write(b"ow\n")
        client.flush()
        QTest.qWait(30)
        self.assertTrue(window.isVisible())
        client.abort()
        server.server.close()
        window.close()

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance()
        if cls.app is None:
            cls.app = QApplication([])
            cls.addClassCleanup(cls.app.shutdown)
        apply_style(cls.app)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.environment = patch.dict(os.environ, {"YPA_DATA_DIR": str(self.directory)})
        self.environment.start()
        self.runtime = LocalRuntime(self.directory)

    def tearDown(self):
        # unittest has no outer Qt event loop to deliver pending widget and worker deletions.
        self.app.processEvents()
        for widget in self.app.topLevelWidgets():
            if isinstance(widget, (MainWindow, SettingsDialog, CommandDialog, ActionDialog)):
                widget.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        self.runtime.stop()
        self.environment.stop()
        self.folder.cleanup()

    def wait_for_idle(self, window):
        deadline = time.monotonic() + 10
        while window.worker and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertIsNone(window.worker, "Desktop task did not finish")

    def test_editor_save_restart_and_typed_execution(self):
        target = self.directory / "ran.txt"
        step = ActionDialog()
        step.kind.setCurrentIndex(1)
        step.program.setText(sys.executable)
        step.arguments.setPlainText(
            "-c\nfrom pathlib import Path; import sys; Path(sys.argv[1]).write_text('done')\n"
            + str(target)
        )
        QTest.mouseClick(
            step.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save),
            Qt.MouseButton.LeftButton,
        )
        self.assertIsNotNone(step.result_action)
        command = CommandDialog()
        command.name.setText("Run my workflow")
        command.phrases.setPlainText("start my workday")
        command.actions.append(step.result_action)
        command.save()
        settings = SettingsDialog(Settings(), [], self.runtime, SettingsStore(self.directory))
        settings.settings.commands.append(command.result_command)
        settings.save()
        window = MainWindow(self.runtime)
        window.request.setText("start my workday")
        QTest.keyClick(window.request, Qt.Key.Key_Return)
        self.wait_for_idle(window)
        self.assertEqual(target.read_text(), "done")
        self.assertIn("completed", window.activity.toPlainText())
        window.close()

    def test_unconfigured_shutdown_and_confirmed_restart(self):
        window = MainWindow(self.runtime)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            with patch("subprocess.run") as run:
                window.execute(builtin_commands()[0])
                run.assert_not_called()
                self.assertIn("Set a spoken shutdown password", window.activity.toPlainText())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            with patch("subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0)
                window.execute(builtin_commands()[1])
                self.wait_for_idle(window)
                self.assertEqual(run.call_args.args[0][1:], ["/r", "/t", "0"])
        window.close()

    def test_shutdown_checks_a_separate_recording_without_logging_or_resolving_it(self):
        dialog = SettingsDialog(Settings(), [], self.runtime, SettingsStore(self.directory))
        dialog.shutdown_password.setText("“Sunflower!”")
        dialog.save()
        self.assertNotIn("sunflower", SettingsStore(self.directory).path.read_text().lower())
        window = MainWindow(self.runtime)
        with (
            patch.object(window.microphone, "start"),
            patch.object(window.microphone, "stop", return_value=np.zeros(16000)),
            patch.object(window.transcriber, "transcribe", return_value="Sunflower.") as hear,
            patch.object(QMessageBox, "question", side_effect=AssertionError("unexpected dialog")),
            patch.object(QApplication, "beep"),
            patch("subprocess.run") as run,
        ):
            run.return_value = subprocess.CompletedProcess([], 0)
            window.request.setText("shutdown")
            window.submit()
            self.wait_for_idle(window)
            self.assertTrue(window.recording)
            window.cancel()
            window.stop_recording()
            hear.assert_not_called()
            run.assert_not_called()
            window.execute(builtin_commands()[0])
            window.stop_recording()  # silence
            hear.assert_not_called()
            run.assert_not_called()
            window.execute(builtin_commands()[0])
            window.command_endpoint.last_speech = 1600
            window.stop_recording()
            self.wait_for_idle(window)
            self.assertEqual(run.call_args.args[0][1:], ["/s", "/t", "0"])
            self.assertNotIn("sunflower", window.activity.toPlainText().lower())
        window.close()

    def test_missing_microphone_does_not_leave_recording_active(self):
        window = MainWindow(self.runtime)
        with patch.object(window.microphone, "start", side_effect=RuntimeError("No microphone")):
            window.start_recording()
        self.assertFalse(window.recording)
        self.assertTrue(window.request.isEnabled())
        self.assertIn("No microphone", window.activity.toPlainText())
        window.close()

    def test_wake_recording_stops_after_silence_and_runs_the_command(self):
        target = self.directory / "wake-command.txt"
        command = Command(
            name="Save marker",
            actions=[
                Action(
                    kind="script",
                    program=sys.executable,
                    arguments=[
                        "-c",
                        "from pathlib import Path; import sys; "
                        "Path(sys.argv[1]).write_text('done')",
                        str(target),
                    ],
                )
            ],
        )
        SettingsStore(self.directory).save(Settings(wake_enabled=True, commands=[command]))
        with patch.object(MainWindow, "resume_wake"), patch.object(QApplication, "beep"):
            window = MainWindow(self.runtime)
            with (
                patch.object(window.microphone, "start"),
                patch.object(
                    window.microphone, "stop", return_value=np.zeros(16000, dtype=np.float32)
                ),
                patch.object(window.transcriber, "transcribe", return_value="Save marker"),
                patch(
                    "faster_whisper.vad.get_speech_timestamps",
                    side_effect=[[], [{"start": 0, "end": 1600}], [{"start": 0, "end": 1600}]],
                ),
            ):
                window.wake_detected()
                QTest.qWait(300)
                self.assertTrue(window.automatic_recording)
                window.audio_received(np.full(1600, 0.1, dtype=np.float32))
                window.audio_received(np.zeros(16000, dtype=np.float32))
                QTest.qWait(50)
                self.wait_for_idle(window)
                self.assertEqual(target.read_text(), "done")
                self.assertFalse(window.recording)
            window.close()

    def test_empty_wake_capture_returns_to_listening_without_transcribing(self):
        with patch.object(MainWindow, "resume_wake") as resume:
            window = MainWindow(self.runtime)
            with (
                patch.object(window.microphone, "start"),
                patch.object(window.microphone, "stop", return_value=np.zeros(48000)),
                patch.object(window.transcriber, "transcribe") as transcribe,
            ):
                window.start_recording(automatic=True)
                window.audio_received(np.zeros(48000, dtype=np.float32))
                QTest.qWait(30)
                self.assertFalse(window.recording)
                self.assertFalse(window.recording_timer.isActive())
                self.assertTrue(window.request.isEnabled())
                self.assertIsNone(window.command_endpoint)
                transcribe.assert_not_called()
                resume.assert_called()
            window.close()

    def test_disabling_wake_discards_automatic_capture_even_if_saving_fails(self):
        with patch.object(MainWindow, "resume_wake"):
            window = MainWindow(self.runtime)
            window.settings.wake_enabled = True
            with (
                patch.object(window.microphone, "start"),
                patch.object(
                    window.microphone, "stop", return_value=np.zeros(16000, dtype=np.float32)
                ),
                patch.object(window.transcriber, "transcribe") as transcribe,
                patch.object(window.store, "save", side_effect=OSError("disk full")),
            ):
                window.start_recording(automatic=True)
                window.toggle_wake(False)
                self.assertFalse(window.recording)
                self.assertFalse(window.settings.wake_enabled)
                self.assertFalse(window.recording_timer.isActive())
                self.assertIsNone(window.command_endpoint)
                transcribe.assert_not_called()
            window.close()

    def test_late_wake_detection_does_not_interrupt_an_active_command(self):
        window = MainWindow(self.runtime)
        window.settings.wake_enabled = True
        window.worker = object()
        window.wake_detected()
        self.assertFalse(window.wake_pending)
        self.assertFalse(window.recording)
        window.worker = None
        window.close()
