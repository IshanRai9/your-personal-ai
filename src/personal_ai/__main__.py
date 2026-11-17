import argparse
import os
import sys

from PySide6.QtCore import QCoreApplication, QLockFile

from personal_ai.config import data_directory
from personal_ai.runtime import LocalRuntime


def main() -> int:
    parser = argparse.ArgumentParser(description="Private Windows voice and text commands")
    parser.add_argument("--setup", action="store_true", help="Download local runtime and models")
    parser.add_argument(
        "--background", action="store_true", help="Start quietly in the system tray"
    )
    parser.add_argument(
        "--install", action="store_true", help="Install Start Menu and sign-in shortcuts"
    )
    parser.add_argument("--data-dir", help="Use a specific local data folder")
    arguments = parser.parse_args()
    if arguments.data_dir:
        os.environ["YPA_DATA_DIR"] = arguments.data_dir
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
            from personal_ai.instance import show_existing
            from personal_ai.windows import Shortcuts

            if arguments.install:
                Shortcuts(directory).install()
            if not arguments.background and not arguments.install:
                show_existing(directory)
        return 1 if arguments.setup else 0
    runtime = LocalRuntime(directory)
    try:
        if arguments.setup:
            print(runtime.setup(lambda message: print(message, flush=True)))
            return 0
        from personal_ai.instance import InstanceServer
        from personal_ai.ui import MainWindow, apply_style
        from personal_ai.windows import Shortcuts

        apply_style(app)
        window = MainWindow(runtime)
        window.install_tray()
        window.instance_server = InstanceServer(directory, window)
        if arguments.install:
            Shortcuts(directory).install()
            window.refresh_tray()
        if not arguments.background and not arguments.install:
            window.show()
        app.commitDataRequest.connect(lambda _session: window.request_quit())
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
    from multiprocessing import freeze_support

    freeze_support()
    raise SystemExit(main())
