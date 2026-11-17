from pathlib import Path

import numpy as np
from PySide6.QtCore import QBuffer, QIODevice, QObject
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices


class Microphone(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.source: QAudioSource | None = None
        self.buffer = QBuffer(self)
        self.format = QAudioFormat()

    def start(self) -> None:
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise RuntimeError(
                "Connect a microphone and enable microphone access in Windows Settings."
            )
        audio_format = device.preferredFormat()
        self.format = audio_format
        self.buffer.setData(b"")
        self.buffer.open(QIODevice.OpenModeFlag.ReadWrite)
        self.source = QAudioSource(device, audio_format, self)
        self.source.start(self.buffer)
        if self.source.error().value != 0:
            self.stop()
            raise RuntimeError("Cannot record. Check Windows microphone permissions and try again.")

    def stop(self) -> np.ndarray:
        if self.source is None:
            return np.array([], dtype=np.float32)
        self.source.stop()
        self.source.deleteLater()
        self.source = None
        raw = bytes(self.buffer.data())
        self.buffer.close()
        self.buffer.setData(b"")
        formats = {
            QAudioFormat.SampleFormat.Int16: (np.int16, 32768.0),
            QAudioFormat.SampleFormat.Int32: (np.int32, 2147483648.0),
            QAudioFormat.SampleFormat.UInt8: (np.uint8, 128.0),
            QAudioFormat.SampleFormat.Float: (np.float32, 1.0),
        }
        if self.format.sampleFormat() not in formats:
            raise RuntimeError("This microphone's audio format is unsupported.")
        dtype, scale = formats[self.format.sampleFormat()]
        frame_size = np.dtype(dtype).itemsize * self.format.channelCount()
        complete = len(raw) // frame_size * frame_size
        audio = np.frombuffer(raw[:complete], dtype=dtype).astype(np.float32)
        if self.format.sampleFormat() == QAudioFormat.SampleFormat.UInt8:
            audio -= 128
        audio /= scale
        audio = audio.reshape(-1, self.format.channelCount()).mean(axis=1)
        rate = self.format.sampleRate()
        if rate != 16000 and len(audio):
            audio = np.interp(
                np.arange(int(len(audio) * 16000 / rate)) * rate / 16000,
                np.arange(len(audio)),
                audio,
            ).astype(np.float32)
        return audio


class Transcriber:
    def __init__(self, directory: Path):
        self.directory = directory
        self.model = None

    def transcribe(self, audio: np.ndarray) -> str:
        if len(audio) < 4000:
            return ""
        if not (self.directory / "model.bin").is_file():
            raise RuntimeError("Download English speech recognition through Settings first.")
        if self.model is None:
            import onnxruntime
            from faster_whisper import WhisperModel

            onnxruntime.disable_telemetry_events()
            self.model = WhisperModel(
                str(self.directory),
                device="cpu",
                compute_type="int8",
                cpu_threads=4,
                local_files_only=True,
            )
        segments, _ = self.model.transcribe(
            audio,
            language="en",
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
