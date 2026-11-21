import unittest
from unittest.mock import patch

import numpy as np

from personal_ai.speech import CommandEndpoint


class CommandEndpointTests(unittest.TestCase):
    def test_follow_up_allows_ten_seconds_to_start_and_a_longer_pause(self):
        with patch("faster_whisper.vad.get_speech_timestamps", return_value=[]) as detect:
            endpoint = CommandEndpoint(follow_up=True)
            silence = np.zeros(1600, dtype=np.float32)
            for _ in range(99):
                self.assertFalse(endpoint.feed(silence))
            self.assertTrue(endpoint.feed(silence))

            endpoint = CommandEndpoint(follow_up=True)
            for _ in range(90):
                self.assertFalse(endpoint.feed(silence))
            detect.return_value = [{"start": 30400, "end": 32000}]
            self.assertFalse(endpoint.feed(silence))
            detect.return_value = []
            for _ in range(14):
                self.assertFalse(endpoint.feed(silence))
            self.assertTrue(endpoint.feed(silence))

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
