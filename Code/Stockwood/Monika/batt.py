"""Battery emulator for Kuraine."""

from __future__ import annotations

_STATE = {
    "charge": 76,
    "charging": True,
    "cycles": 142,
    "voltage": 4.01,
}


def get_battery():
    return dict(_STATE)


def read_battery():
    return get_battery()


def get_status():
    return get_battery()


def status():
    return get_battery()


def get():
    return get_battery()


def read():
    return get_battery()
