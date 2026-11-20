import ctypes

import comtypes
import psutil
import win32api
import win32con
import win32security
from pycaw.api.audiopolicy import IAudioSessionControl2
from pycaw.constants import DEVICE_STATE, EDataFlow
from pycaw.utils import AudioSession, AudioUtilities

from personal_ai.window_commands import app_key


def lock_pc():
    user = ctypes.WinDLL("user32", use_last_error=True)
    if not user.LockWorkStation():
        raise ctypes.WinError(ctypes.get_last_error())
    return "Asked Windows to lock your computer."


def sleep_pc():
    suspend = ctypes.WinDLL("powrprof", use_last_error=True).SetSuspendState
    suspend.argtypes = [ctypes.c_ubyte] * 3
    suspend.restype = ctypes.c_ubyte
    token = win32security.OpenProcessToken(
        win32api.GetCurrentProcess(), win32con.TOKEN_ADJUST_PRIVILEGES | win32con.TOKEN_QUERY
    )
    try:
        privilege = win32security.LookupPrivilegeValue(None, win32security.SE_SHUTDOWN_NAME)
        previous = win32security.AdjustTokenPrivileges(
            token, False, [(privilege, win32con.SE_PRIVILEGE_ENABLED)]
        )
        try:
            if not suspend(False, False, False):
                raise ctypes.WinError(ctypes.get_last_error())
        finally:
            win32security.AdjustTokenPrivileges(token, False, previous)
    finally:
        token.Close()
    return "Sleep requested."


def playback_sessions():
    for device in AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value):
        sessions = device.AudioSessionManager.GetSessionEnumerator()
        for index in range(sessions.GetCount()):
            control = sessions.GetSession(index)
            if control:
                yield AudioSession(control.QueryInterface(IAudioSessionControl2))


def mute_sessions(name, muted):
    count = 0
    for session in playback_sessions():
        try:
            process = session.Process
            matches = process is not None and app_key(process.name()) == app_key(name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if matches:
            session.SimpleAudioVolume.SetMute(muted, None)
            count += 1
    if not count:
        raise ValueError(f"No playback session for {name}. Play audio in the app and try again.")
    return f"{'Muted' if muted else 'Unmuted'} {name}."


def set_app_mute(name, muted):
    comtypes.CoInitialize()
    try:
        return mute_sessions(name, muted)
    finally:
        comtypes.CoUninitialize()


def set_system_sound(enabled):
    comtypes.CoInitialize()
    try:
        speakers = AudioUtilities.GetSpeakers()
        if speakers is None:
            raise RuntimeError("Windows found no default audio output.")
        volume = speakers.EndpointVolume
        muted = not enabled
        if bool(volume.GetMute()) != muted:
            volume.SetMute(muted, None)
        if bool(volume.GetMute()) != muted:
            raise RuntimeError("Windows could not confirm the system sound setting.")
        return "System sound is unmuted." if enabled else "System sound is muted."
    finally:
        comtypes.CoUninitialize()
