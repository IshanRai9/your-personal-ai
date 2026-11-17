"""Windows shortcuts and ownership of the assistant's model processes."""

import ctypes
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
from threading import Lock

_DLL_LOCK = Lock()


@contextmanager
def external_program():
    if os.name != "nt" or not getattr(sys, "frozen", False):
        yield
        return
    with _DLL_LOCK:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetDllDirectoryW.argtypes = [wintypes.DWORD, wintypes.LPWSTR]
        kernel.GetDllDirectoryW.restype = wintypes.DWORD
        kernel.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
        kernel.SetDllDirectoryW.restype = wintypes.BOOL
        previous = ctypes.create_unicode_buffer(32768)
        kernel.GetDllDirectoryW(len(previous), previous)
        kernel.SetDllDirectoryW(None)
        try:
            yield
        finally:
            kernel.SetDllDirectoryW(previous.value or None)


def external_process(arguments, **options):
    environment = dict(options.pop("env", os.environ))
    if getattr(sys, "frozen", False):
        bundle = Path(sys._MEIPASS).resolve()
        environment["PATH"] = os.pathsep.join(
            part
            for part in environment.get("PATH", "").split(os.pathsep)
            if part and not Path(part).resolve().is_relative_to(bundle)
        )
    with external_program():
        return subprocess.Popen(arguments, env=environment, **options)


class GUID(ctypes.Structure):
    _fields_ = [
        ("data1", wintypes.DWORD),
        ("data2", wintypes.WORD),
        ("data3", wintypes.WORD),
        ("data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def parse(cls, value: str):
        return cls.from_buffer_copy(uuid.UUID(value).bytes_le)


def _com_method(pointer, index, *arguments):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *arguments)(table[index])


def _check_hresult(result):
    if result < 0:
        raise OSError(f"Windows shortcut operation failed (0x{result & 0xFFFFFFFF:08X}).")


def create_shortcut(path: Path, target: Path, arguments: list[str]) -> None:
    if os.name != "nt":
        raise OSError("Windows shortcuts require Windows.")
    target = target.resolve()
    if not target.is_file():
        raise FileNotFoundError(f"The app launcher is missing: {target}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + ".tmp.lnk")
    ole = ctypes.WinDLL("ole32")
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    initialized = ole.CoInitializeEx(None, 2)
    if initialized < 0 and initialized != -2147417850:
        _check_hresult(initialized)
    shell_link = ctypes.c_void_p()
    persistence = ctypes.c_void_p()
    try:
        class_id = GUID.parse("00021401-0000-0000-C000-000000000046")
        interface_id = GUID.parse("000214F9-0000-0000-C000-000000000046")
        persist_id = GUID.parse("0000010B-0000-0000-C000-000000000046")
        ole.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        _check_hresult(
            ole.CoCreateInstance(
                ctypes.byref(class_id),
                None,
                1,
                ctypes.byref(interface_id),
                ctypes.byref(shell_link),
            )
        )
        for index, value in [
            (20, str(target)),
            (11, subprocess.list2cmdline(arguments)),
            (9, str(target.parent)),
            (7, "Your Personal AI"),
        ]:
            _check_hresult(_com_method(shell_link, index, wintypes.LPCWSTR)(shell_link, value))
        _check_hresult(
            _com_method(shell_link, 0, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))(
                shell_link, ctypes.byref(persist_id), ctypes.byref(persistence)
            )
        )
        _check_hresult(
            _com_method(persistence, 6, wintypes.LPCWSTR, wintypes.BOOL)(
                persistence, str(temporary), True
            )
        )
        os.replace(temporary, path)
    finally:
        if persistence:
            _com_method(persistence, 2)(persistence)
        if shell_link:
            _com_method(shell_link, 2)(shell_link)
        if initialized >= 0:
            ole.CoUninitialize()
        temporary.unlink(missing_ok=True)


class Shortcuts:
    def __init__(self, data_directory: Path):
        self.programs = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs"
        self.launcher = self.programs / "Your Personal AI.lnk"
        self.startup = self.programs / "Startup/Your Personal AI.lnk"
        self.executable = Path(sys.executable)
        self.arguments = []
        if not getattr(sys, "frozen", False):
            self.executable = self.executable.with_name("pythonw.exe")
            self.arguments = ["-m", "personal_ai"]
        self.arguments += ["--data-dir", str(data_directory.resolve())]

    def install(self):
        create_shortcut(self.launcher, self.executable, self.arguments)
        self.set_startup(True)

    def set_startup(self, enabled: bool):
        if enabled:
            create_shortcut(self.launcher, self.executable, self.arguments)
            create_shortcut(self.startup, self.executable, [*self.arguments, "--background"])
        else:
            self.startup.unlink(missing_ok=True)


class ProcessJob:
    def __init__(self):
        self.handle = None
        if os.name != "nt":
            return
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("min_working_set", ctypes.c_size_t),
                ("max_working_set", ctypes.c_size_t),
                ("active_processes", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io_counters", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process_memory", ctypes.c_size_t),
                ("peak_job_memory", ctypes.c_size_t),
            ]

        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        ):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach(self, process: subprocess.Popen):
        if self.handle and not self.kernel.AssignProcessToJobObject(
            self.handle, int(process._handle)
        ):
            raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self):
        if self.handle:
            self.kernel.TerminateJobObject(self.handle, 1)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None

    def __del__(self):
        self.close()
