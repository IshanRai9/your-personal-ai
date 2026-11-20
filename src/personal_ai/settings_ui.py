from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from personal_ai.config import SettingsStore
from personal_ai.editor import CommandDialog
from personal_ai.models import Command, Settings
from personal_ai.passphrase import enroll
from personal_ai.runtime import MODEL, LocalRuntime
from personal_ai.windows import Shortcuts
from personal_ai.workers import Worker


class SettingsDialog(QDialog):
    def __init__(
        self,
        settings: Settings,
        apps: list[Command],
        runtime: LocalRuntime,
        store: SettingsStore,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(720, 570)
        self.setMinimumSize(580, 460)
        self.settings = settings.model_copy(deep=True)
        self.runtime = runtime
        self.store = store
        self.shortcuts = Shortcuts(store.directory)
        self.startup_was_enabled = self.shortcuts.startup.exists()
        self.worker: Worker | None = None
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        general = QWidget()
        general_layout = QVBoxLayout(general)
        self.start_with_windows = QCheckBox("Start quietly with Windows")
        self.start_with_windows.setChecked(self.startup_was_enabled)
        general_layout.addWidget(self.start_with_windows)
        self.speak = QCheckBox("Speak responses using an installed Windows voice")
        self.speak.setChecked(settings.speak_replies)
        general_layout.addWidget(self.speak)
        self.wake_enabled = QCheckBox("Listen locally for a wake phrase")
        self.wake_enabled.setChecked(settings.wake_enabled)
        general_layout.addWidget(self.wake_enabled)
        general_layout.addWidget(QLabel("Wake phrase"))
        self.wake_phrase = QLineEdit(settings.wake_phrase)
        self.wake_phrase.setAccessibleName("Wake phrase")
        self.wake_phrase.setMaxLength(80)
        self.wake_phrase.setPlaceholderText("Wake phrase, for example Hey Assistant")
        general_layout.addWidget(self.wake_phrase)
        general_layout.addWidget(QLabel("Spoken shutdown password"))
        self.shutdown_password = QLineEdit()
        self.shutdown_password.setAccessibleName("Spoken shutdown password")
        self.shutdown_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.shutdown_password.setMaxLength(120)
        self.shutdown_password.setPlaceholderText(
            "Password set. Enter a new phrase to change it."
            if settings.shutdown_verifier
            else "Choose 2 to 12 English words"
        )
        general_layout.addWidget(self.shutdown_password)
        details = QLabel(
            f"Command model · {MODEL}\nSpeech recognition · English\n\n"
            "Setup downloads the runtime and model files. Once setup completes, "
            "assistant processing works offline."
        )
        details.setWordWrap(True)
        general_layout.addWidget(details)
        self.setup_button = QPushButton("Download / repair local models")
        self.setup_button.clicked.connect(self.setup)
        general_layout.addWidget(self.setup_button)
        self.setup_status = QLabel(self.model_status())
        self.setup_status.setWordWrap(True)
        self.setup_status.setTextFormat(Qt.TextFormat.PlainText)
        general_layout.addWidget(self.setup_status)
        location = QLabel(f"Local settings and models\n{store.directory}")
        location.setWordWrap(True)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        general_layout.addStretch()
        general_layout.addWidget(location)
        tabs.addTab(general, "General")

        custom = QWidget()
        custom_layout = QVBoxLayout(custom)
        self.commands = QListWidget()
        self.commands.itemDoubleClicked.connect(self.edit_command)
        custom_layout.addWidget(self.commands)
        row = QHBoxLayout()
        for title, slot in [
            ("New command", self.add_command),
            ("Edit", self.edit_command),
            ("Delete", self.delete_command),
        ]:
            button = QPushButton(title)
            button.clicked.connect(slot)
            row.addWidget(button)
        custom_layout.addLayout(row)
        tabs.addTab(custom, "Custom commands")
        self.refresh_commands()

        app_tab = QWidget()
        apps_layout = QVBoxLayout(app_tab)
        self.app_search = QLineEdit()
        self.app_search.setPlaceholderText("Search installed apps")
        self.app_search.textChanged.connect(self.filter_apps)
        apps_layout.addWidget(self.app_search)
        self.apps = QListWidget()
        hidden = set(settings.hidden_apps)
        for command in apps:
            item = QListWidgetItem(command.name)
            item.setToolTip(command.actions[0].program)
            item.setData(Qt.ItemDataRole.UserRole, command.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Unchecked if command.id in hidden else Qt.CheckState.Checked
            )
            self.apps.addItem(item)
        apps_layout.addWidget(self.apps)
        help_text = QLabel(
            "Checked apps are available as commands. Add other apps as custom commands."
        )
        help_text.setWordWrap(True)
        apps_layout.addWidget(help_text)
        tabs.addTab(app_tab, "Apps")

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self.save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def model_status(self):
        runtime = "Installed" if self.runtime.binary() else "Download needed"
        speech = (
            "Installed"
            if (self.runtime.speech_directory / "model.bin").is_file()
            else "Download needed"
        )
        manifest = (
            self.store.directory
            / "models/ollama/manifests/registry.ollama.ai/library/qwen3/4b-instruct"
        )
        model = "Installed" if manifest.is_file() else "Download needed"
        wake = (
            "Installed"
            if (self.runtime.wake_directory / "am/final.mdl").is_file()
            else "Download needed"
        )
        return (
            f"Runtime · {runtime}\nCommand model · {model}\nSpeech model · {speech}\n"
            f"Wake model · {wake}"
        )

    def refresh_commands(self, selected=0):
        self.commands.clear()
        for command in self.settings.commands:
            suffix = "" if command.enabled else " · Disabled"
            self.commands.addItem(command.name + suffix)
        self.commands.setCurrentRow(selected)

    def add_command(self):
        dialog = CommandDialog(parent=self)
        if dialog.exec() and dialog.result_command:
            self.settings.commands.append(dialog.result_command)
            self.refresh_commands(len(self.settings.commands) - 1)
        dialog.deleteLater()

    def edit_command(self):
        index = self.commands.currentRow()
        if index >= 0:
            dialog = CommandDialog(self.settings.commands[index], self)
            if dialog.exec() and dialog.result_command:
                self.settings.commands[index] = dialog.result_command
                self.refresh_commands(index)
            dialog.deleteLater()

    def delete_command(self):
        index = self.commands.currentRow()
        if index >= 0:
            self.settings.commands.pop(index)
            self.refresh_commands(min(index, len(self.settings.commands) - 1))

    def filter_apps(self, text):
        for index in range(self.apps.count()):
            item = self.apps.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def setup(self):
        if self.worker:
            return
        self.setup_button.setEnabled(False)
        self.buttons.setEnabled(False)

        def download(progress, cancelled):
            def report(message):
                if cancelled.is_set():
                    raise RuntimeError("Setup cancelled.")
                progress(message)

            return self.runtime.setup(report)

        self.worker = Worker(download, self)
        self.worker.progress.connect(self.setup_status.setText)
        self.worker.finished.connect(self.setup_finished)
        self.worker.start()

    def setup_finished(self):
        worker = self.worker
        self.worker = None
        self.setup_status.setText(str(worker.error) if worker.error else str(worker.result))
        self.setup_button.setEnabled(True)
        self.buttons.setEnabled(True)
        worker.deleteLater()

    def save(self):
        try:
            if self.shutdown_password.text():
                self.settings.shutdown_verifier = enroll(self.shutdown_password.text())
            self.settings.speak_replies = self.speak.isChecked()
            self.settings.wake_phrase = self.wake_phrase.text()
            self.settings.wake_enabled = self.wake_enabled.isChecked()
            self.settings.hidden_apps = [
                self.apps.item(index).data(Qt.ItemDataRole.UserRole)
                for index in range(self.apps.count())
                if self.apps.item(index).checkState() == Qt.CheckState.Unchecked
            ]
            if self.start_with_windows.isChecked() != self.startup_was_enabled:
                self.shortcuts.set_startup(self.start_with_windows.isChecked())
            self.store.save(self.settings)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Settings could not be saved", str(error))
            return
        self.accept()

    def reject(self):
        if self.worker:
            QMessageBox.information(self, "Setup in progress", "Wait for model setup to finish.")
            return
        super().reject()

    def closeEvent(self, event):
        if self.worker:
            event.ignore()
            return
        super().closeEvent(event)
