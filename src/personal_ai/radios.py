"""Windows radio access begins on the UI thread; device operations run on a worker."""

import time

from winrt.runtime import ApartmentType, init_apartment, uninit_apartment
from winrt.windows.devices.radios import Radio, RadioAccessStatus, RadioKind, RadioState
from winrt.windows.foundation import AsyncStatus

RADIO_KINDS = {"wifi": RadioKind.WI_FI, "bluetooth": RadioKind.BLUETOOTH}
RADIO_LABELS = {"wifi": "Wi-Fi", "bluetooth": "Bluetooth"}


def request_radio_access():
    return Radio.request_access_async()


def _result(operation, cancelled, timeout):
    deadline = time.monotonic() + timeout
    while operation.status == AsyncStatus.STARTED:
        if cancelled.wait(0.05):
            operation.cancel()
            raise RuntimeError("Radio command cancelled.")
        if time.monotonic() >= deadline:
            operation.cancel()
            raise TimeoutError("Windows took too long to respond to the radio request.")
    return operation.get_results()


def _check_access(status, label):
    if status == RadioAccessStatus.DENIED_BY_USER:
        raise PermissionError(f"Windows radio access was declined for {label}.")
    if status != RadioAccessStatus.ALLOWED:
        raise PermissionError(
            f"Windows did not allow control of {label}. Check device policy and radio permissions."
        )


def _set_radios(setting, enabled, access, cancelled):
    label = RADIO_LABELS[setting]
    target = RadioState.ON if enabled else RadioState.OFF
    state_name = "on" if enabled else "off"
    radios = [
        r
        for r in _result(Radio.get_radios_async(), cancelled, 10)
        if r.kind == RADIO_KINDS[setting]
    ]
    if not radios:
        raise RuntimeError(f"Windows found no {label} radio.")
    if all(r.state == target for r in radios):
        return f"{label} is already {state_name}."
    if access is None:
        raise PermissionError(f"Windows radio access is required to change {label}.")
    _check_access(_result(access, cancelled, 60), label)
    if any(r.state == RadioState.DISABLED for r in radios):
        raise RuntimeError(f"{label} is disabled by Windows or a hardware switch.")
    for radio in radios:
        if cancelled.is_set():
            raise RuntimeError("Radio command cancelled.")
        if radio.state != target:
            _check_access(_result(radio.set_state_async(target), cancelled, 10), label)
    # An accepted request can still be overridden by hardware or system policy.
    deadline = time.monotonic() + 4
    while not all(r.state == target for r in radios):
        if cancelled.wait(0.1) or time.monotonic() >= deadline:
            raise RuntimeError(
                f"Windows accepted the request, but could not confirm {label} is {state_name}."
            )
    return f"{label} is {state_name}."


def set_radio(setting, enabled, access, cancelled):
    init_apartment(ApartmentType.MULTI_THREADED)
    try:
        return _set_radios(setting, enabled, access, cancelled)
    finally:
        uninit_apartment()
