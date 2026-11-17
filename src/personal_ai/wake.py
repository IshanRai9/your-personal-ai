import json
import queue
import threading
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

from personal_ai.commands import normalize


class WakeListener(QThread):
    ready = Signal()
    detected = Signal()
    error = Signal(str)

    def __init__(self, directory: Path, phrase: str, parent=None):
        super().__init__(parent)
        self.directory = directory
        self.phrase = normalize(phrase)
        self.model_ready = False
        self.active = threading.Event()
        self.stopped = threading.Event()
        self.reset = threading.Event()
        self.audio: queue.Queue[bytes] = queue.Queue(maxsize=100)

    def feed(self, samples: np.ndarray):
        if not self.active.is_set():
            return
        pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
        try:
            self.audio.put_nowait(pcm)
        except queue.Full:
            self.clear_audio()
            self.reset.set()

    def clear_audio(self):
        while True:
            try:
                self.audio.get_nowait()
            except queue.Empty:
                return

    def pause(self):
        self.active.clear()
        self.clear_audio()
        self.reset.set()

    def resume(self):
        self.reset.set()
        self.active.set()

    def stop(self):
        self.pause()
        self.stopped.set()

    def run(self):
        try:
            if not (self.directory / "am/final.mdl").is_file():
                raise RuntimeError(
                    "Download the wake model in Settings before enabling wake listening."
                )
            from vosk import KaldiRecognizer, Model, SetLogLevel

            SetLogLevel(-1)
            model = Model(str(self.directory))
            missing = [word for word in self.phrase.split() if model.vosk_model_find_word(word) < 0]
            if missing:
                raise RuntimeError("Wake phrase contains unsupported words. Try “Hey Assistant”.")
            recognizer = KaldiRecognizer(model, 16000, json.dumps([self.phrase, "[unk]"]))
            recognizer.SetWords(True)
            self.model_ready = True
            self.ready.emit()
            while not self.stopped.is_set():
                try:
                    audio = self.audio.get(timeout=0.1)
                except queue.Empty:
                    continue
                if self.reset.is_set():
                    recognizer.Reset()
                    self.reset.clear()
                if not self.active.is_set():
                    continue
                if recognizer.AcceptWaveform(audio):
                    result = json.loads(recognizer.Result())
                    words = result.get("result", [])
                    confident = words and all(word.get("conf", 0) >= 0.8 for word in words)
                    if normalize(result.get("text", "")) == self.phrase and confident:
                        if self.active.is_set():
                            self.pause()
                            self.detected.emit()
        except Exception as error:
            self.error.emit(str(error))
        finally:
            self.model_ready = False
            self.pause()
