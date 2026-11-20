from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from PIL import ImageGrab

from personal_ai.tabs import WindowsDesktop
from personal_ai.window_commands import check_window


def capture_window(target, folder, cancelled):
    desktop = WindowsDesktop()
    check_window(desktop, target)
    if desktop.user.IsIconic(target.handle):
        raise ValueError("Restore the window before taking its screenshot.")
    with ImageGrab.grab(window=target.handle) as image:
        check_window(desktop, target)
        if cancelled.is_set():
            raise RuntimeError("Screenshot cancelled.")
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        with NamedTemporaryFile(
            prefix=f"Window_{stamp}_", suffix=".png", dir=folder, delete=False
        ) as stream:
            path = Path(stream.name)
            try:
                image.save(stream, format="PNG")
                stream.flush()
            except Exception:
                stream.close()
                path.unlink(missing_ok=True)
                raise
    return f"Screenshot saved to {path}"
