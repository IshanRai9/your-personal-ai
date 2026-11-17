import os
import tempfile
from pathlib import Path

from personal_ai.models import Settings


def data_directory() -> Path:
    override = os.environ.get("YPA_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return (
        Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "YourPersonalAI"
    )


class SettingsStore:
    def __init__(self, directory: Path | None = None):
        self.directory = directory or data_directory()
        self.path = self.directory / "settings.json"

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        return Settings.model_validate_json(self.path.read_text(encoding="utf-8"))

    def save(self, settings: Settings) -> None:
        validated = Settings.model_validate(settings.model_dump())
        self.directory.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(validated.model_dump_json(indent=2))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
