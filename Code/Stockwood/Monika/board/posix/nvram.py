"""NVRAM emulator for Monika."""

from __future__ import annotations

_SIZE = 4096
_MEMORY = bytearray(_SIZE)


def _check_range(addr, length):
    addr = int(addr)
    length = int(length)
    if addr < 0 or length < 0 or addr + length > _SIZE:
        raise ValueError("range out of bounds")
    return addr, length


def read_nvram(addr, length):
    addr, length = _check_range(addr, length)
    return bytes(_MEMORY[addr : addr + length])


def get_nvram(addr, length):
    return read_nvram(addr, length)


def read(addr, length):
    return read_nvram(addr, length)


def get(addr, length):
    return read_nvram(addr, length)


def write_nvram(addr, data):
    addr = int(addr)
    if isinstance(data, int):
        payload = bytes([data])
    else:
        payload = bytes(data)

    _check_range(addr, len(payload))
    _MEMORY[addr : addr + len(payload)] = payload
    return True


def set_nvram(addr, data):
    return write_nvram(addr, data)


def write(addr, data):
    return write_nvram(addr, data)


def set(addr, data):
    return write_nvram(addr, data)
