import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from personal_ai.models import Action, Command


class ActionDialog(QDialog):
    def __init__(self, action: Action | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Command step")
        self.resize(570, 400)
        self.result_action: Action | None = None
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.kind = QComboBox()
        self.kind.addItem("Open an app", "launch")
        self.kind.addItem("Run a script and wait for completion", "script")
        self.program = QLineEdit()
        program_row = QWidget()
        program_layout = QHBoxLayout(program_row)
        program_layout.setContentsMargins(0, 0, 0, 0)
        program_layout.addWidget(self.program)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_program)
        program_layout.addWidget(browse)
        self.arguments = QPlainTextEdit()
        self.arguments.setPlaceholderText(
            "One argument per line. Enter paths without surrounding quotes."
        )
        self.arguments.setMaximumHeight(100)
        script = QPushButton("Choose a Python or PowerShell script…")
        script.clicked.connect(self.browse_script)
        self.directory = QLineEdit()
        directory_row = QWidget()
        directory_layout = QHBoxLayout(directory_row)
        directory_layout.setContentsMargins(0, 0, 0, 0)
        directory_layout.addWidget(self.directory)
        folder = QPushButton("Browse…")
        folder.clicked.connect(self.browse_directory)
        directory_layout.addWidget(folder)
        form.addRow("Action", self.kind)
        form.addRow("Program / interpreter", program_row)
        form.addRow("Arguments", self.arguments)
        form.addRow("", script)
        form.addRow("Working folder", directory_row)
        layout.addLayout(form)
        note = QLabel("Script steps wait for an exit code. Later steps stop if a script fails.")
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if action:
            self.kind.setCurrentIndex(0 if action.kind == "launch" else 1)
            self.program.setText(action.program)
            self.arguments.setPlainText("\n".join(action.arguments))
            self.directory.setText(action.working_directory)

    def browse_program(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose an app or interpreter", "", "Programs (*.exe *.lnk);;All files (*)"
        )
        if path:
            self.program.setText(path)

    def browse_script(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose a script", "", "Scripts (*.ps1 *.py)")
        if not path:
            return
        self.kind.setCurrentIndex(1)
        if Path(path).suffix.casefold() == ".py":
            self.program.setText(sys.executable)
            self.arguments.setPlainText(path)
        else:
            self.program.setText("powershell.exe")
            self.arguments.setPlainText("-NoProfile\n-File\n" + path)
        self.directory.setText(str(Path(path).parent))

    def browse_directory(self):
        path = QFileDialog.getExistingDirectory(self, "Working folder")
        if path:
            self.directory.setText(path)

    def save(self):
        try:
            self.result_action = Action(
                kind=self.kind.currentData(),
                program=self.program.text().strip(),
                arguments=self.arguments.toPlainText().splitlines(),
                working_directory=self.directory.text().strip(),
            )
        except ValueError as error:
            QMessageBox.warning(self, "Check this step", str(error))
            return
        self.accept()


class CommandDialog(QDialog):
    def __init__(self, command: Command | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit command" if command else "New command")
        self.resize(620, 560)
        self.command = command
        self.result_command: Command | None = None
        self.actions = list(command.actions) if command else []
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(command.name if command else "")
        self.name.setMaxLength(120)
        self.description = QLineEdit(command.description if command else "")
        self.description.setMaxLength(500)
        self.phrases = QPlainTextEdit("\n".join(command.phrases) if command else "")
        self.phrases.setPlaceholderText("One example phrase per line")
        self.phrases.setMaximumHeight(90)
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(command.enabled if command else True)
        form.addRow("Name", self.name)
        form.addRow("Description", self.description)
        form.addRow("Example phrases", self.phrases)
        form.addRow("", self.enabled)
        layout.addLayout(form)
        layout.addWidget(QLabel("Steps run in the order shown"))
        self.steps = QListWidget()
        self.steps.itemDoubleClicked.connect(self.edit_step)
        layout.addWidget(self.steps)
        row = QHBoxLayout()
        for title, slot in [
            ("Add step", self.add_step),
            ("Edit", self.edit_step),
            ("Remove", self.remove_step),
            ("Move up", lambda: self.move_step(-1)),
            ("Move down", lambda: self.move_step(1)),
        ]:
            button = QPushButton(title)
            button.clicked.connect(slot)
            row.addWidget(button)
        layout.addLayout(row)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self, selected=0):
        self.steps.clear()
        for index, action in enumerate(self.actions, 1):
            verb = "Open" if action.kind == "launch" else "Run"
            self.steps.addItem(f"{index}. {verb} {Path(action.program).name}")
        self.steps.setCurrentRow(selected)

    def add_step(self):
        dialog = ActionDialog(parent=self)
        if dialog.exec() and dialog.result_action:
            self.actions.append(dialog.result_action)
            self.refresh(len(self.actions) - 1)
        dialog.deleteLater()

    def edit_step(self):
        index = self.steps.currentRow()
        if index < 0:
            return
        dialog = ActionDialog(self.actions[index], self)
        if dialog.exec() and dialog.result_action:
            self.actions[index] = dialog.result_action
            self.refresh(index)
        dialog.deleteLater()

    def remove_step(self):
        index = self.steps.currentRow()
        if index >= 0:
            self.actions.pop(index)
            self.refresh(min(index, len(self.actions) - 1))

    def move_step(self, delta):
        index = self.steps.currentRow()
        target = index + delta
        if index >= 0 and 0 <= target < len(self.actions):
            self.actions[index], self.actions[target] = self.actions[target], self.actions[index]
            self.refresh(target)

    def save(self):
        data = {
            "name": self.name.text(),
            "description": self.description.text(),
            "phrases": [
                line.strip() for line in self.phrases.toPlainText().splitlines() if line.strip()
            ],
            "enabled": self.enabled.isChecked(),
            "actions": self.actions,
        }
        if self.command:
            data["id"] = self.command.id
        try:
            self.result_command = Command.model_validate(data)
        except ValueError as error:
            QMessageBox.warning(self, "Check this command", str(error))
            return
        self.accept()
