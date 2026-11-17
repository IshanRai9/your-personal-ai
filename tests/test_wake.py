import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from personal_ai.config import SettingsStore
from personal_ai.wake import WakeListener


class WakeTests(unittest.TestCase):
    def test_legacy_settings_preserve_hidden_apps_when_wake_is_enabled(self):
        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder))
            store.path.write_text(json.dumps({"version": 1, "hidden_apps": ["app.hidden"]}))
            settings = store.load()
            self.assertFalse(settings.wake_enabled)
            self.assertEqual(settings.wake_phrase, "Hey Assistant")
            settings.wake_enabled = True
            settings.wake_phrase = "Hello computer"
            store.save(settings)
            saved = store.load()
            self.assertTrue(saved.wake_enabled)
            self.assertEqual(saved.hidden_apps, ["app.hidden"])
            self.assertEqual(saved.wake_phrase, "Hello computer")

    def test_wake_audio_is_bounded_and_discarded_on_pause(self):
        listener = WakeListener(Path("missing"), "Hey Assistant")
        frame = np.zeros(1600, dtype=np.float32)
        listener.feed(frame)
        self.assertTrue(listener.audio.empty())
        listener.resume()
        for _ in range(200):
            listener.feed(frame)
        self.assertLessEqual(listener.audio.qsize(), listener.audio.maxsize)
        listener.pause()
        listener.feed(frame)
        self.assertTrue(listener.audio.empty())
        self.assertFalse(listener.active.is_set())
