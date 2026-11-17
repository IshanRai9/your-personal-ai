import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from personal_ai.commands import builtin_commands
from personal_ai.config import SettingsStore
from personal_ai.editor import ActionDialog, CommandDialog
from personal_ai.models import Settings
from personal_ai.runtime import LocalRuntime
from personal_ai.settings_ui import SettingsDialog
from personal_ai.ui import MainWindow, apply_style


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        apply_style(cls.app)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.directory = Path(self.folder.name)
        self.environment = patch.dict(os.environ, {"YPA_DATA_DIR": str(self.directory)})
        self.environment.start()
        self.runtime = LocalRuntime(self.directory)

    def tearDown(self):
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

    def test_shutdown_dialog_cancellation_and_confirmed_executor(self):
        window = MainWindow(self.runtime)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            with patch("subprocess.run") as run:
                window.execute(builtin_commands()[0])
                run.assert_not_called()
                self.assertIn("cancelled", window.activity.toPlainText())
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
            with patch("subprocess.run") as run:
                run.return_value = subprocess.CompletedProcess([], 0)
                window.execute(builtin_commands()[1])
                self.wait_for_idle(window)
                self.assertEqual(run.call_args.args[0][1:], ["/r", "/t", "0"])
        window.close()

    def test_missing_microphone_does_not_leave_recording_active(self):
        window = MainWindow(self.runtime)
        with patch.object(window.microphone, "start", side_effect=RuntimeError("No microphone")):
            window.start_recording()
        self.assertFalse(window.recording)
        self.assertTrue(window.request.isEnabled())
        self.assertIn("No microphone", window.activity.toPlainText())
        window.close()
