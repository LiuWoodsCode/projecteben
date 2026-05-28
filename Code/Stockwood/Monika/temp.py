"""Temperature emulator for Kuraine."""

from __future__ import annotations

_STATE = {
    "case": 38.5,
    "batt": 35.2,
}


def get_temperature():
    return dict(_STATE)


def read_temperature():
    return get_temperature()


def get_temps():
    return get_temperature()


def temperature():
    return get_temperature()


def get():
    return get_temperature()


def read():
    return get_temperature()
