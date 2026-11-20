import ctypes
import os
import re
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from personal_ai.windows import external_program

Location = QStandardPaths.StandardLocation
PERSONAL_FOLDERS = {
    "downloads": Location.DownloadLocation,
    "desktop": Location.DesktopLocation,
    "documents": Location.DocumentsLocation,
    "pictures": Location.PicturesLocation,
    "music": Location.MusicLocation,
    "videos": Location.MoviesLocation,
}


def personal_folders():
    return {
        name: Path(path)
        for name, location in PERSONAL_FOLDERS.items()
        if (path := QStandardPaths.writableLocation(location))
    }


def local_drive(path):
    return bool(path.drive and ctypes.windll.kernel32.GetDriveTypeW(path.anchor) in (2, 3, 6))


def open_folder(name):
    path = personal_folders().get(name.casefold(), Path(name).expanduser())
    if not path.is_absolute() or not local_drive(path) or not path.is_dir():
        raise ValueError("Choose a personal folder name or an existing absolute local folder path.")
    with external_program():
        os.startfile(str(path), "explore")
    return f"Opened {path}."


def find_files(query, roots, cancelled):
    query = re.sub(r"\s+dot\s+", ".", query.strip(), flags=re.I).casefold()
    if not query or any(char in query for char in '\\/:*?"<>|'):
        raise ValueError("Enter a file name, for example budget.xlsx.")
    matches = set()
    for root in dict.fromkeys(roots):
        if not local_drive(root):
            continue
        for directory, folders, files in os.walk(root):
            if cancelled.is_set():
                raise RuntimeError("File search cancelled.")
            base = Path(directory)
            folders[:] = [
                name
                for name in folders
                if not (base / name).is_junction() and not (base / name).is_symlink()
            ]
            for name in files:
                if query in name.casefold():
                    matches.add(str(base / name))
                    if len(matches) >= 25:
                        return "First 25 matches:\n" + "\n".join(sorted(matches))
    if not matches:
        return f"No matching files for {query} in your local personal folders."
    return "\n".join(sorted(matches))
