import argparse
import os
import sys

from PySide6.QtCore import QCoreApplication, QLockFile

from personal_ai.config import data_directory
from personal_ai.runtime import LocalRuntime


def main() -> int:
    parser = argparse.ArgumentParser(description="Private Windows voice and text commands")
    parser.add_argument("--setup", action="store_true", help="Download local runtime and models")
    arguments = parser.parse_args()
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    if arguments.setup:
        app = QCoreApplication(sys.argv)
    else:
        from PySide6.QtWidgets import QApplication

        app = QApplication(sys.argv)
    app.setApplicationName("Your Personal AI")
    directory = data_directory()
    directory.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(directory / "assistant.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        if arguments.setup:
            print("Close the running assistant before starting setup.", file=sys.stderr)
        else:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(None, "Your Personal AI", "The assistant is already running.")
        return 1
    runtime = LocalRuntime(directory)
    try:
        if arguments.setup:
            print(runtime.setup(lambda message: print(message, flush=True)))
            return 0
        from personal_ai.ui import MainWindow, apply_style

        apply_style(app)
        window = MainWindow(runtime)
        window.show()
        return app.exec()
    except (OSError, RuntimeError, ValueError) as error:
        if arguments.setup:
            print(str(error), file=sys.stderr)
        else:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.critical(None, "Could not start", str(error))
        return 1
    finally:
        runtime.stop()
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
