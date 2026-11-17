"""Build the windowed Windows app with the project's installed dependencies."""

import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--windowed",
        "--onedir",
        "--name",
        "YourPersonalAI",
        "--specpath",
        str(root / "build"),
        "--paths",
        str(root / "src"),
        "--collect-all",
        "vosk",
        "--collect-data",
        "faster_whisper",
        "--copy-metadata",
        "faster-whisper",
        "--copy-metadata",
        "ctranslate2",
        "--copy-metadata",
        "huggingface-hub",
        str(root / "src/personal_ai/__main__.py"),
    ],
    cwd=root,
    check=True,
)
