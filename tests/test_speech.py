import unittest
from unittest.mock import patch

import numpy as np

from personal_ai.speech import CommandEndpoint


class CommandEndpointTests(unittest.TestCase):
    def test_brief_pause_continues_and_long_pause_finishes(self):
        with patch("faster_whisper.vad.get_speech_timestamps") as detect:
            endpoint = CommandEndpoint()
            detect.return_value = [{"start": 0, "end": 1600}]
            self.assertFalse(endpoint.feed(np.zeros(1600, dtype=np.float32)))
            self.assertFalse(endpoint.feed(np.zeros(8000, dtype=np.float32)))
            # Speech resumes after half a second, resetting the end-of-command gap.
            detect.return_value = [{"start": 0, "end": 11200}]
            self.assertFalse(endpoint.feed(np.zeros(1600, dtype=np.float32)))
            self.assertFalse(endpoint.feed(np.zeros(11200, dtype=np.float32)))
            self.assertTrue(endpoint.feed(np.zeros(1600, dtype=np.float32)))

    def test_background_noise_times_out_without_becoming_a_command(self):
        endpoint = CommandEndpoint()
        noise = np.random.default_rng(7).normal(0, 0.02, 1600).astype(np.float32)
        for _ in range(29):
            self.assertFalse(endpoint.feed(noise))
        self.assertTrue(endpoint.feed(noise))
        self.assertIsNone(endpoint.last_speech)
        self.assertLessEqual(len(endpoint.audio), 32000)
