import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import httpx

from personal_ai.runtime import LocalRuntime


class RuntimeInstallationTests(unittest.TestCase):
    def archive(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("ollama.exe", b"new executable")
            archive.writestr("lib/ollama/library.dll", b"library")
        return buffer.getvalue()

    def test_failed_extraction_preserves_existing_runtime_and_retry_recovers(self):
        payload = self.archive()
        transport = httpx.MockTransport(lambda request: httpx.Response(200, content=payload))
        client = httpx.Client(transport=transport)
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            installed = directory / "ollama"
            installed.mkdir()
            executable = installed / "ollama.exe"
            executable.write_bytes(b"previous executable")
            runtime = LocalRuntime(directory)
            with patch("personal_ai.runtime.httpx.Client", return_value=client):
                with patch("zipfile.ZipFile.extractall", side_effect=OSError("disk full")):
                    with self.assertRaisesRegex(OSError, "disk full"):
                        runtime._download_runtime(lambda _: None)
            self.assertEqual(executable.read_bytes(), b"previous executable")
            self.assertFalse((directory / "ollama-download.zip").exists())
            self.assertFalse(list(directory.glob(".ollama-*")))
            retry = httpx.Client(transport=transport)
            with patch("personal_ai.runtime.httpx.Client", return_value=retry):
                runtime._download_runtime(lambda _: None)
            self.assertEqual(executable.read_bytes(), b"new executable")
            self.assertTrue((installed / "lib/ollama/library.dll").is_file())

    def test_archive_cannot_write_outside_staging(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("../../outside.txt", b"bad path")
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=buffer.getvalue())
        )
        with tempfile.TemporaryDirectory() as folder:
            client = httpx.Client(transport=transport)
            with patch("personal_ai.runtime.httpx.Client", return_value=client):
                with self.assertRaisesRegex(ValueError, "invalid path"):
                    LocalRuntime(Path(folder))._download_runtime(lambda _: None)
            self.assertEqual(list(Path(folder).iterdir()), [])
