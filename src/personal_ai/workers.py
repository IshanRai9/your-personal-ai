import threading
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    progress = Signal(str)

    def __init__(self, operation: Callable, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancelled = threading.Event()
        self.result = None
        self.error: Exception | None = None

    def run(self):
        try:
            self.result = self.operation(self.progress.emit, self.cancelled)
        except Exception as error:
            self.error = error
