from datetime import datetime

from PySide6.QtCore import QEvent, Qt, QTimer
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
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from personal_ai.commands import CancelledError, Executor, Resolver, builtin_commands, discover_apps
from personal_ai.config import SettingsStore
from personal_ai.models import Command, Resolution
from personal_ai.runtime import LocalRuntime
from personal_ai.settings_ui import SettingsDialog
from personal_ai.speech import CommandEndpoint, Microphone, Transcriber
from personal_ai.tabs import TabTracker
from personal_ai.wake import WakeListener
from personal_ai.windows import Shortcuts
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
        self.tab_tracker = TabTracker()
        self.command_target = None
        self.transcriber = Transcriber(runtime.speech_directory)
        self.microphone = Microphone(self)
        self.speaker: QTextToSpeech | None = None
        self.worker: Worker | None = None
        self.callback = None
        self.closing = False
        self.quit_requested = False
        self.tray: QSystemTrayIcon | None = None
        self.tray_wake_action = None
        self.tray_startup_action = None
        self.recording = False
        self.automatic_recording = False
        self.wake_listener: WakeListener | None = None
        self.wake_failed = False
        self.settings_open = False
        self.wake_pending = False
        self.command_endpoint: CommandEndpoint | None = None
        self.recording_timer = QTimer(self)
        self.recording_timer.setSingleShot(True)
        self.recording_timer.timeout.connect(self.stop_recording)
        self.microphone.audio_ready.connect(self.audio_received)
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
        self.wake_button = QPushButton()
        self.wake_button.setCheckable(True)
        self.wake_button.setChecked(self.settings.wake_enabled)
        self.wake_button.toggled.connect(self.toggle_wake)
        heading.addWidget(self.wake_button)
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
        self.update_wake_button()
        try:
            self.tab_tracker.start()
        except OSError as error:
            self.log("Tab commands", str(error))
        QTimer.singleShot(0, self.resume_wake)

    def install_tray(self):
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.setWindowIcon(icon)
        self.tray = QSystemTrayIcon(icon, self)
        menu = QMenu(self)
        menu.addAction("Open assistant", self.show_window)
        menu.addAction("Settings", self.open_tray_settings)
        self.tray_wake_action = menu.addAction("Wake listening")
        self.tray_wake_action.setCheckable(True)
        self.tray_wake_action.toggled.connect(self.wake_button.setChecked)
        self.tray_startup_action = menu.addAction("Start with Windows")
        self.tray_startup_action.setCheckable(True)
        self.tray_startup_action.toggled.connect(self.set_startup)
        menu.addSeparator()
        menu.addAction("Quit", self.request_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self.tray_activated)
        self.refresh_tray()
        self.tray.show()
        QApplication.instance().setQuitOnLastWindowClosed(False)

    def refresh_tray(self):
        if not self.tray:
            return
        self.tray.setToolTip("Your Personal AI · " + self.wake_button.text())
        self.tray_wake_action.blockSignals(True)
        self.tray_wake_action.setChecked(self.wake_button.isChecked())
        self.tray_wake_action.blockSignals(False)
        self.tray_startup_action.blockSignals(True)
        self.tray_startup_action.setChecked(Shortcuts(self.store.directory).startup.exists())
        self.tray_startup_action.blockSignals(False)

    def set_startup(self, enabled):
        try:
            Shortcuts(self.store.directory).set_startup(enabled)
        except OSError as error:
            QMessageBox.warning(self, "Could not update Windows startup", str(error))
        self.refresh_tray()

    def show_window(self):
        if self.closing:
            return
        self.tab_tracker.remember()
        was_hidden = not self.isVisible()
        self.showNormal()
        if was_hidden:
            # A hidden Windows launch can suppress the first native show.
            self.hide()
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_activated(self, reason):
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_window()

    def open_tray_settings(self):
        self.show_window()
        self.open_settings()

    def request_quit(self):
        if self.quit_requested:
            return
        self.quit_requested = True
        for worker in self.findChildren(Worker):
            if worker.isRunning():
                worker.cancelled.set()
                worker.finished.connect(self.close)
        self.runtime.stop()
        self.close()

    def update_wake_button(self):
        self.wake_button.setText(
            "Wake listening on"
            if self.settings.wake_enabled and not self.wake_failed
            else "Wake listening off"
        )
        self.wake_button.setToolTip(
            f"Say “{self.settings.wake_phrase}”, pause, then speak a command."
        )
        self.refresh_tray()

    def toggle_wake(self, enabled):
        self.settings.wake_enabled = enabled
        try:
            self.store.save(self.settings)
        except (OSError, ValueError) as error:
            self.log("Settings", f"Wake preference could not be saved. {error}")
        self.wake_failed = False
        if enabled:
            self.resume_wake()
        else:
            self.pause_wake()
            if self.wake_listener:
                self.wake_listener.stop()
            if self.automatic_recording:
                self.recording_timer.stop()
                self.recording = False
                self.automatic_recording = False
                self.microphone.stop()
                self.command_endpoint = None
                self.mic_button.setText("Hold to speak")
                self.set_busy(False)
            if not self.worker and not self.recording:
                self.status.setText("Ready")
        self.update_wake_button()

    def pause_wake(self):
        if self.wake_listener:
            self.wake_listener.pause()
        if not self.recording and self.microphone.source is not None:
            self.microphone.stop()

    def resume_wake(self):
        if (
            not self.settings.wake_enabled
            or self.wake_failed
            or self.closing
            or self.worker
            or self.recording
            or self.settings_open
            or self.wake_pending
        ):
            return
        if self.speaker and self.speaker.state() not in {
            QTextToSpeech.State.Ready,
            QTextToSpeech.State.Error,
        }:
            return
        if self.wake_listener is None:
            self.wake_listener = WakeListener(
                self.runtime.wake_directory, self.settings.wake_phrase, self
            )
            self.wake_listener.ready.connect(self.resume_wake)
            self.wake_listener.detected.connect(self.wake_detected)
            self.wake_listener.error.connect(self.wake_error)
            self.wake_listener.finished.connect(self.wake_finished)
            self.status.setText("Starting local wake listening…")
            self.wake_listener.start()
            return
        if not self.wake_listener.model_ready or self.wake_listener.stopped.is_set():
            return
        try:
            self.wake_listener.resume()
            if self.microphone.source is None:
                self.microphone.start(streaming=True)
            self.status.setText(f"Listening for “{self.settings.wake_phrase}”")
        except (OSError, RuntimeError) as error:
            self.wake_error(str(error))

    def wake_error(self, message):
        self.wake_failed = True
        self.pause_wake()
        if self.wake_listener:
            self.wake_listener.stop()
        self.wake_button.blockSignals(True)
        self.wake_button.setChecked(False)
        self.wake_button.blockSignals(False)
        self.update_wake_button()
        self.log("Wake listening", message)
        self.status.setText("Wake listening unavailable")

    def wake_finished(self):
        listener = self.wake_listener
        self.wake_listener = None
        if listener:
            listener.deleteLater()
        if self.closing:
            self.close()
        else:
            QTimer.singleShot(0, self.resume_wake)

    def wake_detected(self):
        if (
            self.worker
            or self.recording
            or self.closing
            or self.settings_open
            or not self.settings.wake_enabled
            or self.wake_pending
        ):
            return
        if self.speaker and self.speaker.state() not in {
            QTextToSpeech.State.Ready,
            QTextToSpeech.State.Error,
        }:
            return
        self.pause_wake()
        self.wake_pending = True
        self.show_window()
        self.status.setText("Awake. Speak your command after the tone.")
        QApplication.beep()
        QTimer.singleShot(250, self.record_wake_command)

    def record_wake_command(self):
        self.wake_pending = False
        if (
            self.settings.wake_enabled
            and not self.closing
            and not self.worker
            and not self.settings_open
        ):
            self.start_recording(automatic=True)
        else:
            self.resume_wake()

    def audio_received(self, audio):
        if self.wake_listener:
            self.wake_listener.feed(audio)
        if not self.automatic_recording or not self.recording or not len(audio):
            return
        if self.command_endpoint and self.command_endpoint.feed(audio):
            QTimer.singleShot(0, self.stop_recording)

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
                self.speaker.stateChanged.connect(self.speaker_state_changed)
            if self.speaker.state() == QTextToSpeech.State.Error:
                self.status.setText("Spoken replies unavailable. Install a Windows speech voice.")
            else:
                self.pause_wake()
                self.speaker.say(text[:1200])

    def speaker_state_changed(self, state):
        if state in {QTextToSpeech.State.Ready, QTextToSpeech.State.Error}:
            self.resume_wake()
        else:
            self.pause_wake()

    def speech_error(self, _reason, message):
        self.status.setText("Spoken replies unavailable. " + message)

    def set_busy(self, busy):
        if busy:
            self.pause_wake()
        else:
            QTimer.singleShot(0, self.resume_wake)
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
        self.command_target = self.tab_tracker.capture()
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
        tab_target = self.command_target
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
                tab_target=tab_target,
            ),
            self.reply,
            f"Running {command.name}…",
        )

    def run_selected(self, item):
        if self.worker or self.recording:
            return
        self.command_target = self.tab_tracker.capture()
        command = next(
            command
            for command in self.available_commands()
            if command.id == item.data(Qt.ItemDataRole.UserRole)
        )
        self.log("You", command.name)
        self.execute(command)

    def start_recording(self, automatic=False):
        if self.worker or self.recording:
            return
        self.command_target = self.tab_tracker.capture()
        self.pause_wake()
        self.recording = True
        self.automatic_recording = automatic
        if self.speaker:
            self.speaker.stop()
        try:
            self.command_endpoint = CommandEndpoint() if automatic else None
            self.microphone.start()
        except (OSError, RuntimeError) as error:
            self.recording = False
            self.automatic_recording = False
            self.command_endpoint = None
            self.reply(str(error))
            QTimer.singleShot(0, self.resume_wake)
            return
        if automatic:
            self.recording_timer.start(10000)
        self.mic_button.setText(
            "Listening… click to finish" if automatic else "Listening… release to run"
        )
        self.status.setText("Listening")
        self.request.setEnabled(False)
        self.send_button.setEnabled(False)
        self.settings_button.setEnabled(False)
        self.command_list.setEnabled(False)

    def stop_recording(self):
        if not self.recording:
            return
        empty_command = (
            self.automatic_recording
            and self.command_endpoint is not None
            and self.command_endpoint.last_speech is None
        )
        self.recording_timer.stop()
        self.recording = False
        self.automatic_recording = False
        self.mic_button.setText("Hold to speak")
        audio = self.microphone.stop()
        self.command_endpoint = None
        if empty_command:
            self.set_busy(False)
            self.reply("I didn’t hear a command. Please try again.")
            return

        self.begin(
            lambda _progress, _cancelled: self.transcriber.transcribe(audio),
            self.handle_voice,
            "Transcribing locally…",
        )

    def handle_voice(self, text):
        if not text:
            self.reply("I didn’t hear a command. Please try again.")
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
        self.settings_open = True
        self.pause_wake()
        dialog = SettingsDialog(self.settings, self.apps, self.runtime, self.store, self)
        if dialog.exec():
            self.settings = dialog.settings
            if self.speaker and not self.settings.speak_replies:
                self.speaker.stop()
            self.refresh_commands()
            self.wake_failed = False
            if self.wake_listener:
                self.wake_listener.stop()
            self.wake_button.blockSignals(True)
            self.wake_button.setChecked(self.settings.wake_enabled)
            self.wake_button.blockSignals(False)
            self.update_wake_button()
        dialog.deleteLater()
        self.settings_open = False
        self.refresh_tray()
        QTimer.singleShot(0, self.resume_wake)

    def event(self, event):
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and getattr(self, "recording", False)
            and not self.automatic_recording
        ):
            self.stop_recording()
        return super().event(event)

    def closeEvent(self, event):
        if self.tray is not None and not self.quit_requested:
            self.hide()
            event.ignore()
            return
        self.closing = True
        self.tab_tracker.stop()
        self.recording_timer.stop()
        if self.recording:
            self.recording = False
            self.automatic_recording = False
            self.microphone.stop()
        self.command_endpoint = None
        self.pause_wake()
        if self.wake_listener:
            self.wake_listener.stop()
        active_downloads = any(worker.isRunning() for worker in self.findChildren(Worker))
        if (
            self.worker
            or active_downloads
            or (self.wake_listener and self.wake_listener.isRunning())
        ):
            self.cancel()
            event.ignore()
            return
        if self.speaker:
            self.speaker.stop()
        if self.tray:
            self.tray.hide()
            QApplication.instance().quit()
        super().closeEvent(event)
