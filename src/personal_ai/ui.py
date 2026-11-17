from datetime import datetime

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFont, QPalette, QTextCharFormat, QTextCursor
from PySide6.QtTextToSpeech import QTextToSpeech
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from personal_ai.commands import CancelledError, Executor, Resolver, builtin_commands, discover_apps
from personal_ai.config import SettingsStore
from personal_ai.models import Command, Resolution
from personal_ai.runtime import LocalRuntime
from personal_ai.settings_ui import SettingsDialog
from personal_ai.speech import Microphone, Transcriber
from personal_ai.workers import Worker


def apply_style(app: QApplication) -> None:
    app.setStyle("Fusion")
    app.setFont(QFont("Inter", 10))
    palette = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#171b1c"),
        (QPalette.ColorRole.WindowText, "#e9eee9"),
        (QPalette.ColorRole.Base, "#101415"),
        (QPalette.ColorRole.AlternateBase, "#202628"),
        (QPalette.ColorRole.Text, "#e9eee9"),
        (QPalette.ColorRole.Button, "#2c3536"),
        (QPalette.ColorRole.ButtonText, "#e9eee9"),
        (QPalette.ColorRole.Highlight, "#355f51"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.ToolTipBase, "#202628"),
        (QPalette.ColorRole.ToolTipText, "#e9eee9"),
    ]:
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#788481"))
    app.setPalette(palette)
    app.setStyleSheet("""
        QWidget { font-size: 13px; }
        QLabel#title { font-size: 25px; font-weight: 600; }
        QLabel#status { color: #a3c5b5; padding: 5px 0; }
        QPushButton { padding: 9px 14px; border: 1px solid #3c4948; border-radius: 7px; }
        QPushButton:hover { background: #35433e; }
        QPushButton:pressed { background: #426951; }
        QPushButton:disabled { color: #718078; border-color: #2c3432; }
        QPushButton#send { background: #426951; border-color: #658b70; }
        QLineEdit, QPlainTextEdit, QTextEdit, QListWidget {
            border: 1px solid #303b38; border-radius: 7px; padding: 9px;
            selection-background-color: #355f51;
        }
        QLineEdit:focus, QPlainTextEdit:focus { border-color: #83ab91; }
        QListWidget::item { padding: 7px; border-radius: 4px; }
        QListWidget::item:selected { background: #355f51; }
        QTabWidget::pane { border: 1px solid #303b38; border-radius: 5px; }
        QTabBar::tab { padding: 10px 18px; }
        QTabBar::tab:selected { background: #355f51; }
        QCheckBox { spacing: 9px; padding: 5px 0; }
        QSplitter::handle { background: #171b1c; width: 14px; }
    """)


class MainWindow(QMainWindow):
    def __init__(self, runtime: LocalRuntime):
        super().__init__()
        self.runtime = runtime
        self.store = SettingsStore(runtime.directory)
        self.settings = self.store.load()
        self.apps = discover_apps()
        self.resolver = Resolver(runtime.classify)
        self.executor = Executor()
        self.transcriber = Transcriber(runtime.speech_directory)
        self.microphone = Microphone(self)
        self.speaker: QTextToSpeech | None = None
        self.worker: Worker | None = None
        self.callback = None
        self.closing = False
        self.recording = False
        self.setWindowTitle("Your Personal AI")
        self.resize(1040, 740)
        self.setMinimumSize(800, 560)

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(16)
        heading = QHBoxLayout()
        title = QLabel("Your Personal AI")
        title.setObjectName("title")
        heading.addWidget(title)
        heading.addStretch()
        self.settings_button = QPushButton("Settings")
        self.settings_button.clicked.connect(self.open_settings)
        heading.addWidget(self.settings_button)
        outer.addLayout(heading)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        sidebar = QWidget()
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.addWidget(QLabel("Commands"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a command")
        self.search.textChanged.connect(self.filter_commands)
        sidebar_layout.addWidget(self.search)
        self.command_list = QListWidget()
        self.command_list.itemDoubleClicked.connect(self.run_selected)
        self.command_list.setToolTip("Double-click a command to run it")
        sidebar_layout.addWidget(self.command_list)
        splitter.addWidget(sidebar)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        self.activity = QTextEdit()
        self.activity.setReadOnly(True)
        self.activity.setAcceptRichText(False)
        self.activity.document().setMaximumBlockCount(600)
        self.activity.setAccessibleName("Command activity")
        content_layout.addWidget(self.activity)
        self.status = QLabel("Ready")
        self.status.setObjectName("status")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        content_layout.addWidget(self.status)
        self.request = QLineEdit()
        self.request.setPlaceholderText("Try “open Notepad”")
        self.request.setAccessibleName("Command")
        self.request.returnPressed.connect(self.submit)
        content_layout.addWidget(self.request)
        controls = QHBoxLayout()
        self.mic_button = QPushButton("Hold to speak")
        self.mic_button.setAccessibleName("Hold to speak")
        self.mic_button.pressed.connect(self.start_recording)
        self.mic_button.released.connect(self.stop_recording)
        controls.addWidget(self.mic_button)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        self.send_button = QPushButton("Run command")
        self.send_button.setObjectName("send")
        self.send_button.clicked.connect(self.submit)
        controls.addWidget(self.send_button)
        content_layout.addLayout(controls)
        splitter.addWidget(content)
        splitter.setSizes([260, 700])
        outer.addWidget(splitter)
        self.setCentralWidget(container)
        self.refresh_commands()
        self.log("Assistant", "Type a command or hold the microphone button while speaking.")
        if not runtime.binary():
            self.log("Setup", "Open Settings to download the local runtime and models.")
        self.request.setFocus()

    def available_commands(self) -> list[Command]:
        hidden = set(self.settings.hidden_apps)
        return [
            *builtin_commands(),
            *(command for command in self.apps if command.id not in hidden),
            *(command for command in self.settings.commands if command.enabled),
        ]

    def refresh_commands(self):
        self.command_list.clear()
        for command in self.available_commands():
            item = QListWidgetItem(command.name)
            item.setData(Qt.ItemDataRole.UserRole, command.id)
            item.setToolTip(command.description or command.actions[0].program)
            self.command_list.addItem(item)
        self.filter_commands(self.search.text())

    def filter_commands(self, text):
        for index in range(self.command_list.count()):
            item = self.command_list.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())

    def log(self, author: str, text: str):
        cursor = self.activity.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        label_format = QTextCharFormat()
        label_format.setForeground(QColor("#a3c5b5"))
        label_format.setFontWeight(QFont.Weight.DemiBold)
        cursor.insertText(f"{author} · {datetime.now():%H:%M}\n", label_format)
        body_format = QTextCharFormat()
        body_format.setForeground(QColor("#e9eee9"))
        cursor.insertText(text + "\n\n", body_format)
        self.activity.setTextCursor(cursor)
        self.activity.ensureCursorVisible()

    def reply(self, text):
        self.log("Assistant", text)
        if self.settings.speak_replies:
            if self.speaker is None:
                self.speaker = QTextToSpeech(self)
                self.speaker.errorOccurred.connect(self.speech_error)
            if self.speaker.state() == QTextToSpeech.State.Error:
                self.status.setText("Spoken replies unavailable. Install a Windows speech voice.")
            else:
                self.speaker.say(text[:1200])

    def speech_error(self, _reason, message):
        self.status.setText("Spoken replies unavailable. " + message)

    def set_busy(self, busy):
        for widget in (
            self.request,
            self.send_button,
            self.mic_button,
            self.settings_button,
            self.command_list,
        ):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def begin(self, operation, callback, status):
        if self.worker:
            return
        self.set_busy(True)
        self.status.setText(status)
        self.callback = callback
        self.worker = Worker(operation, self)
        self.worker.progress.connect(self.status.setText)
        self.worker.finished.connect(self.task_finished)
        self.worker.start()

    def task_finished(self):
        worker = self.worker
        callback = self.callback
        self.worker = None
        self.callback = None
        self.set_busy(False)
        self.status.setText("Ready")
        if not self.closing:
            if worker.cancelled.is_set() or isinstance(worker.error, CancelledError):
                self.reply("Command cancelled. Any completed steps have already run.")
            elif worker.error:
                self.reply(str(worker.error))
            else:
                callback(worker.result)
            self.request.setFocus()
        worker.deleteLater()
        if self.closing:
            self.close()

    def submit(self):
        text = self.request.text().strip()
        if not text or self.worker or self.recording:
            return
        self.request.clear()
        self.log("You", text)
        self.resolve_text(text)

    def resolve_text(self, text):
        commands = self.available_commands()
        self.begin(
            lambda _progress, _cancelled: self.resolver.resolve(text, commands),
            self.handle_resolution,
            "Understanding your command…",
        )

    def handle_resolution(self, result: Resolution):
        commands = {command.id: command for command in self.available_commands()}
        if result.status == "unknown":
            self.reply(
                "I couldn’t find a configured command for that request. "
                "You can add one in Settings."
            )
            return
        identifier = result.command_id
        if result.status == "clarify":
            options = [commands[identifier] for identifier in result.candidates]
            labels = [f"{command.name} · {command.actions[0].program}" for command in options]
            choice, accepted = QInputDialog.getItem(
                self, "Choose a command", "Which command did you mean?", labels, 0, False
            )
            if not accepted:
                self.reply("Command cancelled.")
                return
            identifier = options[labels.index(choice)].id
        self.execute(commands[identifier])

    def execute(self, command: Command):
        confirmed = False
        if command.requires_confirmation:
            result = QMessageBox.question(
                self,
                f"{command.name} this computer?",
                f"{command.name} this computer now? Save your work before confirming.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                self.reply(f"{command.name} cancelled.")
                return
            confirmed = True
        self.begin(
            lambda progress, cancelled: self.executor.execute(
                command,
                power_confirmed=confirmed,
                progress=progress,
                cancelled=cancelled,
            ),
            self.reply,
            f"Running {command.name}…",
        )

    def run_selected(self, item):
        if self.worker or self.recording:
            return
        command = next(
            command
            for command in self.available_commands()
            if command.id == item.data(Qt.ItemDataRole.UserRole)
        )
        self.log("You", command.name)
        self.execute(command)

    def start_recording(self):
        if self.worker or self.recording:
            return
        if self.speaker:
            self.speaker.stop()
        try:
            self.microphone.start()
        except (OSError, RuntimeError) as error:
            self.reply(str(error))
            return
        self.recording = True
        self.mic_button.setText("Listening… release to run")
        self.status.setText("Listening")
        self.request.setEnabled(False)
        self.send_button.setEnabled(False)
        self.settings_button.setEnabled(False)
        self.command_list.setEnabled(False)

    def stop_recording(self):
        if not self.recording:
            return
        self.recording = False
        self.mic_button.setText("Hold to speak")
        audio = self.microphone.stop()

        self.begin(
            lambda _progress, _cancelled: self.transcriber.transcribe(audio),
            self.handle_voice,
            "Transcribing locally…",
        )

    def handle_voice(self, text):
        if not text:
            self.reply("I didn’t hear a command. Hold the button while speaking and try again.")
            return
        self.log("You · voice", text)
        self.resolve_text(text)

    def cancel(self):
        if self.worker:
            self.worker.cancelled.set()
            self.status.setText("Cancelling…")
            self.cancel_button.setEnabled(False)

    def open_settings(self):
        if self.worker or self.recording:
            return
        dialog = SettingsDialog(self.settings, self.apps, self.runtime, self.store, self)
        if dialog.exec():
            self.settings = dialog.settings
            if self.speaker and not self.settings.speak_replies:
                self.speaker.stop()
            self.refresh_commands()
        dialog.deleteLater()

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate and getattr(self, "recording", False):
            self.stop_recording()
        return super().event(event)

    def closeEvent(self, event):
        if self.recording:
            self.recording = False
            self.microphone.stop()
        if self.worker:
            self.closing = True
            self.cancel()
            event.ignore()
            return
        if self.speaker:
            self.speaker.stop()
        super().closeEvent(event)
