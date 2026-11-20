import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from winrt.windows.devices.radios import RadioAccessStatus, RadioKind, RadioState
from winrt.windows.foundation import AsyncStatus

from personal_ai.commands import Executor, Resolver, bind_request, builtin_commands
from personal_ai.pc_controls import set_system_sound
from personal_ai.radios import _set_radios


def resolve(phrase):
    commands = bind_request(phrase, builtin_commands())
    result = Resolver(None).resolve(phrase, commands)
    return next(command for command in commands if command.id == result.command_id)


def operation(value):
    return Mock(status=AsyncStatus.COMPLETED, get_results=Mock(return_value=value))


class RadioDevice:
    def __init__(self, kind, state):
        self.kind = kind
        self.state = state
        self.requests = []

    def set_state_async(self, state):
        self.requests.append(state)
        self.state = state
        return operation(RadioAccessStatus.ALLOWED)


class SettingsCommandTests(unittest.TestCase):
    def test_phrases_resolve_offline_and_route_to_the_correct_setting(self):
        with patch("personal_ai.commands.set_radio", return_value="ok") as radio:
            for phrase, key, enabled in [
                ("turn Wi-Fi off", "wifi", False),
                ("turn on wifi", "wifi", True),
                ("disable Bluetooth", "bluetooth", False),
                ("switch blue tooth on", "bluetooth", True),
            ]:
                with self.subTest(phrase=phrase):
                    Executor().execute(resolve(phrase))
                    self.assertEqual(radio.call_args.args[:2], (key, enabled))
        self.assertEqual(resolve("mute speakers").id, "builtin.sound_off")
        self.assertEqual(resolve("unmute system audio").id, "builtin.sound_on")
        self.assertEqual(resolve("mute audio for Discord").id, "builtin.mute_app")

    def test_only_requested_radio_changes_and_repeating_command_is_a_noop(self):
        wifi = RadioDevice(RadioKind.WI_FI, RadioState.ON)
        bluetooth = RadioDevice(RadioKind.BLUETOOTH, RadioState.ON)
        access = operation(RadioAccessStatus.ALLOWED)
        with patch(
            "personal_ai.radios.Radio", get_radios_async=lambda: operation([wifi, bluetooth])
        ):
            self.assertEqual(_set_radios("wifi", False, access, threading.Event()), "Wi-Fi is off.")
            self.assertIn("already off", _set_radios("wifi", False, access, threading.Event()))
            self.assertEqual(wifi.requests, [RadioState.OFF])
            self.assertEqual(bluetooth.requests, [])

    def test_absent_denied_disabled_and_cancelled(self):
        wifi = RadioDevice(RadioKind.WI_FI, RadioState.ON)
        access = operation(RadioAccessStatus.ALLOWED)
        cancel = threading.Event()
        with patch("personal_ai.radios.Radio", get_radios_async=lambda: operation([wifi])):
            with self.assertRaisesRegex(RuntimeError, "no Bluetooth"):
                _set_radios("bluetooth", False, access, cancel)
            for status in [RadioAccessStatus.DENIED_BY_SYSTEM, RadioAccessStatus.DENIED_BY_USER]:
                with self.assertRaises(PermissionError):
                    _set_radios("wifi", False, operation(status), cancel)
            wifi.state = RadioState.DISABLED
            with self.assertRaisesRegex(RuntimeError, "disabled"):
                _set_radios("wifi", True, access, cancel)
            wifi.state = RadioState.ON
            cancel.set()
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                _set_radios("wifi", False, access, cancel)
        self.assertEqual(wifi.requests, [])

    def test_accepted_request_without_a_state_change_is_reported(self):
        wifi = RadioDevice(RadioKind.WI_FI, RadioState.OFF)
        wifi.set_state_async = Mock(return_value=operation(RadioAccessStatus.ALLOWED))
        with (
            patch("personal_ai.radios.Radio", get_radios_async=lambda: operation([wifi])),
            patch("personal_ai.radios.time.monotonic", side_effect=[0, 0, 0, 0, 6]),
            self.assertRaisesRegex(RuntimeError, "could not confirm"),
        ):
            _set_radios("wifi", True, operation(RadioAccessStatus.ALLOWED), threading.Event())

    def test_system_sound_only_updates_master_mute(self):
        endpoint = Mock()
        endpoint.GetMute.side_effect = [False, True, True, True, True, False]
        with patch(
            "personal_ai.pc_controls.AudioUtilities.GetSpeakers",
            return_value=SimpleNamespace(EndpointVolume=endpoint),
        ):
            self.assertIn("muted", set_system_sound(False))
            set_system_sound(False)
            endpoint.SetMute.assert_called_once_with(True, None)
            Executor().execute(resolve("turn on sound"))
            endpoint.SetMute.assert_called_with(False, None)
