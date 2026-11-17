import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from personal_ai.windows import ProcessJob, Shortcuts


@unittest.skipUnless(os.name == "nt", "Windows integration")
class WindowsIntegrationTests(unittest.TestCase):
    def test_startup_shortcut_can_be_enabled_and_disabled(self):
        with tempfile.TemporaryDirectory() as folder:
            with patch.dict(os.environ, {"APPDATA": folder}):
                shortcuts = Shortcuts(Path(folder) / "Profile Data")
                shortcuts.set_startup(True)
                self.assertTrue(shortcuts.launcher.is_file())
                self.assertIn("--background".encode("utf-16-le"), shortcuts.startup.read_bytes())
                shortcuts.set_startup(True)
                shortcuts.set_startup(False)
                self.assertFalse(shortcuts.startup.exists())
                self.assertTrue(shortcuts.launcher.is_file())

    def test_closing_process_job_terminates_the_owned_child(self):
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        job = ProcessJob()
        try:
            job.attach(process)
            job.close()
            process.wait(timeout=3)
        finally:
            job.close()
            if process.poll() is None:
                process.kill()
                process.wait()
