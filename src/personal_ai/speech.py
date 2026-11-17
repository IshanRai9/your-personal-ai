from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices


class Microphone(QObject):
    audio_ready = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.source: QAudioSource | None = None
        self.device = None
        self.streaming = False
        self.chunks: list[np.ndarray] = []
        self.pending_bytes = b""
        self.format = QAudioFormat()

    def start(self, streaming: bool = False) -> None:
        if self.source is not None:
            raise RuntimeError("Microphone capture is already active.")
        device = QMediaDevices.defaultAudioInput()
        if device.isNull():
            raise RuntimeError(
                "Connect a microphone and enable microphone access in Windows Settings."
            )
        audio_format = device.preferredFormat()
        self.format = audio_format
        self.streaming = streaming
        self.chunks.clear()
        self.pending_bytes = b""
        self.source = QAudioSource(device, audio_format, self)
        self.device = self.source.start()
        if self.source.error().value != 0:
            self.stop()
            raise RuntimeError("Cannot record. Check Windows microphone permissions and try again.")
        self.device.readyRead.connect(self.read_audio)

    def stop(self) -> np.ndarray:
        if self.source is None:
            return np.array([], dtype=np.float32)
        self.read_audio()
        self.source.stop()
        self.source.deleteLater()
        self.source = None
        self.device = None
        audio = np.concatenate(self.chunks) if self.chunks else np.array([], dtype=np.float32)
        self.chunks.clear()
        self.pending_bytes = b""
        return audio

    def read_audio(self):
        if self.device is None or not self.device.isOpen():
            return
        raw = self.pending_bytes + bytes(self.device.readAll())
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
        self.pending_bytes = raw[complete:]
        if not complete:
            return
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
        if not self.streaming:
            self.chunks.append(audio)
        self.audio_ready.emit(audio)


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
