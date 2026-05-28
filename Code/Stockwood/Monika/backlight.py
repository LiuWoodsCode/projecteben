"""Backlight emulator for Kuraine."""

from __future__ import annotations

_BRIGHTNESS = 80


def _clamp(value):
    value = int(value)
    if value < 0:
        return 0
    if value > 100:
        return 100
    return value


def get_brightness():
    return _BRIGHTNESS


def read_brightness():
    return get_brightness()


def brightness():
    return get_brightness()


def get():
    return get_brightness()


def read():
    return get_brightness()


def set_brightness(value):
    global _BRIGHTNESS
    _BRIGHTNESS = _clamp(value)
    return _BRIGHTNESS


def write_brightness(value):
    return set_brightness(value)


def set(value):
    return set_brightness(value)


def write(value):
    return set_brightness(value)
