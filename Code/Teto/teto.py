#!/usr/bin/env python3
"""
usb_gadget.py

A one-file Python module for creating Linux USB gadget devices with ConfigFS.
Designed for Raspberry Pi / Linux boards that support USB device mode.

Supports:
    - Composite USB gadgets
    - USB serial / CDC ACM
    - USB mass storage
    - USB HID keyboard
    - USB HID mouse
    - Raw HID descriptors for advanced devices

Typical use:

    from usb_gadget import USBGadget, make_backing_file, mount_tmpfs

    mount_tmpfs("/mnt/ramdisk", "2048M")
    make_backing_file("/mnt/ramdisk/fakeusb.img", "2048M", filesystem="vfat")

    g = USBGadget(
        name="ramusb",
        vendor_id=0x2E8A,
        product_id=0x0030,
        manufacturer="Raspberry Pi",
        product="Raspberry Pi USB 3.0 Flash Drive",
        serial="00000000PI5RAM1",
    )

    g.add_config("c.1", configuration="Config 1", max_power_ma=250)
    g.add_mass_storage("storage", backing_file="/mnt/ramdisk/fakeusb.img", removable=True)
    serial = g.add_serial("serial")
    keyboard = g.add_hid_keyboard("keyboard")
    mouse = g.add_hid_mouse("mouse")

    g.bind()

    keyboard.write_text("Hello from a Pi-backed USB gadget!\n")
    mouse.move(20, 0)

    # Later:
    g.unbind()
    g.remove()

Notes:
    - Must run as root for ConfigFS writes.
    - Requires the target board/kernel to support USB gadget mode.
    - Usually requires libcomposite and the relevant USB function modules.
    - Do not let the gadget host and Linux gadget side mount/write the same
      mass-storage backing file at the same time, unless you enjoy corruption.
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Dict, Iterable, List, Optional, Sequence, Tuple, Union

PathLike = Union[str, os.PathLike]

CONFIGFS_ROOT = Path("/sys/kernel/config")
USB_GADGET_ROOT = CONFIGFS_ROOT / "usb_gadget"
UDC_ROOT = Path("/sys/class/udc")


class GadgetError(RuntimeError):
    """Base exception for USB gadget errors."""


class RootRequiredError(GadgetError):
    """Raised when an operation requires root."""


class GadgetExistsError(GadgetError):
    """Raised when a gadget already exists and clobber=False."""


class GadgetNotBoundError(GadgetError):
    """Raised when an operation expects a bound gadget."""


class GadgetAlreadyBoundError(GadgetError):
    """Raised when trying to bind an already bound gadget."""


class FunctionError(GadgetError):
    """Raised for USB function related errors."""


# ---------------------------------------------------------------------------
# Low-level utilities
# ---------------------------------------------------------------------------


def require_root() -> None:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        raise RootRequiredError("This operation must be run as root.")


def run(
    args: Sequence[str],
    *,
    check: bool = True,
    capture: bool = False,
    text: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(args),
        check=check,
        capture_output=capture,
        text=text,
    )


def modprobe(module: str, *, optional: bool = True) -> bool:
    """Load a kernel module. Returns True if it loaded or was already loaded."""
    try:
        run(["modprobe", module], check=True, capture=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        if optional:
            return False
        raise


def ensure_configfs_mounted() -> None:
    """Mount ConfigFS if it is not already mounted."""
    require_root()
    CONFIGFS_ROOT.mkdir(parents=True, exist_ok=True)
    if USB_GADGET_ROOT.exists():
        return
    run(["mount", "-t", "configfs", "none", str(CONFIGFS_ROOT)])


def mount_tmpfs(path: PathLike, size: str = "2048M", *, mode: str = "1777") -> Path:
    """Mount a tmpfs, useful for RAM-backed mass-storage images."""
    require_root()
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    opts = f"size={size},mode={mode}"
    run(["mount", "-t", "tmpfs", "-o", opts, "tmpfs", str(target)])
    return target


def unmount(path: PathLike, *, lazy: bool = False) -> None:
    args = ["umount"]
    if lazy:
        args.append("-l")
    args.append(str(path))
    run(args)


def _write_text(path: Path, value: Union[str, int]) -> None:
    path.write_text(str(value), encoding="ascii")


def _write_bytes(path: Path, value: bytes) -> None:
    path.write_bytes(value)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="ascii", errors="replace").strip()


def _get_rpi_serial() -> str:
    serial_paths = (
        Path("/sys/firmware/devicetree/base/serial-number"),
        Path("/proc/device-tree/serial-number"),
    )
    for path in serial_paths:
        try:
            value = path.read_bytes().replace(b"\x00", b"").decode("ascii", errors="ignore").strip()
        except OSError:
            continue
        if value:
            return value

    try:
        with Path("/proc/cpuinfo").open("r", encoding="ascii", errors="replace") as f:
            for line in f:
                if line.lower().startswith("serial"):
                    _, _, value = line.partition(":")
                    value = value.strip()
                    if value:
                        return value
    except OSError:
        pass

    return "000000000000"


def _mkdir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _safe_rmdir(path: Path) -> None:
    try:
        path.rmdir()
    except FileNotFoundError:
        pass


def _remove_tree_bottom_up(root: Path) -> None:
    if not root.exists():
        return

    # ConfigFS does not behave like a normal filesystem. shutil.rmtree can be
    # too aggressive, so remove symlinks, files, then directories bottom-up.
    for current, dirs, files in os.walk(root, topdown=False, followlinks=False):
        current_path = Path(current)
        for name in files:
            p = current_path / name
            try:
                if p.is_symlink():
                    p.unlink()
            except FileNotFoundError:
                pass
        for name in dirs:
            p = current_path / name
            try:
                if p.is_symlink():
                    p.unlink()
                else:
                    p.rmdir()
            except (FileNotFoundError, OSError):
                pass
    try:
        root.rmdir()
    except (FileNotFoundError, OSError):
        pass


def parse_size(size: Union[int, str]) -> int:
    """Parse sizes like 2048M, 2G, 512MiB into bytes."""
    if isinstance(size, int):
        return size
    s = size.strip().replace("_", "")
    m = re.fullmatch(r"(?i)(\d+(?:\.\d+)?)(\s*)(b|k|kb|kib|m|mb|mib|g|gb|gib|t|tb|tib)?", s)
    if not m:
        raise ValueError(f"Invalid size: {size!r}")
    value = float(m.group(1))
    unit = (m.group(3) or "b").lower()
    mult = {
        "b": 1,
        "k": 1000,
        "kb": 1000,
        "m": 1000**2,
        "mb": 1000**2,
        "g": 1000**3,
        "gb": 1000**3,
        "t": 1000**4,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }[unit]
    return int(value * mult)


def make_backing_file(
    path: PathLike,
    size: Union[int, str],
    *,
    filesystem: Optional[str] = "vfat",
    sparse: bool = False,
    overwrite: bool = False,
    label: Optional[str] = None,
) -> Path:
    """
    Create a disk image for USB mass storage.

    filesystem:
        None / "none"  -> do not format
        "vfat" / "fat32" -> mkfs.vfat
        "ext4" -> mkfs.ext4
        anything else -> tries mkfs.<filesystem>
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    if p.exists() and not overwrite:
        raise FileExistsError(f"Backing file already exists: {p}")

    nbytes = parse_size(size)

    if sparse:
        with p.open("wb") as f:
            f.truncate(nbytes)
    else:
        chunk_size = 1024 * 1024
        zero = b"\0" * chunk_size
        remaining = nbytes
        with p.open("wb") as f:
            while remaining > 0:
                chunk = zero if remaining >= chunk_size else b"\0" * remaining
                f.write(chunk)
                remaining -= len(chunk)
            f.flush()
            os.fsync(f.fileno())

    if filesystem and filesystem.lower() not in {"none", "raw"}:
        fs = filesystem.lower()
        if fs in {"vfat", "fat", "fat32", "msdos"}:
            cmd = ["mkfs.vfat"]
            if label:
                cmd += ["-n", label[:11]]
            cmd.append(str(p))
        elif fs == "ext4":
            cmd = ["mkfs.ext4", "-F"]
            if label:
                cmd += ["-L", label]
            cmd.append(str(p))
        else:
            cmd = [f"mkfs.{fs}", str(p)]
        run(cmd)

    return p


def list_udcs() -> List[str]:
    if not UDC_ROOT.exists():
        return []
    return sorted(p.name for p in UDC_ROOT.iterdir() if p.is_dir() or p.is_symlink())


def pick_udc(preferred: Optional[str] = None) -> str:
    udcs = list_udcs()
    if preferred:
        if preferred not in udcs:
            raise GadgetError(f"UDC {preferred!r} was not found. Available: {udcs}")
        return preferred
    if not udcs:
        raise GadgetError("No USB Device Controller found in /sys/class/udc.")
    return udcs[0]


def _dev_glob(pattern: str) -> List[Path]:
    return sorted(Path(p) for p in glob.glob(pattern))


def _wait_for_new_device(
    pattern: str,
    before: Iterable[Path],
    *,
    timeout: float = 5.0,
) -> Optional[Path]:
    before_set = {str(p) for p in before}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _dev_glob(pattern)
        new = [p for p in current if str(p) not in before_set]
        if new:
            return new[0]
        time.sleep(0.05)
    return None


def _wait_for_any_device(pattern: str, *, timeout: float = 5.0) -> Optional[Path]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _dev_glob(pattern)
        if current:
            return current[0]
        time.sleep(0.05)
    return None


# ---------------------------------------------------------------------------
# HID descriptors and helpers
# ---------------------------------------------------------------------------


HID_KEYBOARD_REPORT_DESC = bytes(
    [
        0x05,
        0x01,  # Usage Page (Generic Desktop)
        0x09,
        0x06,  # Usage (Keyboard)
        0xA1,
        0x01,  # Collection (Application)
        0x05,
        0x07,  # Usage Page (Keyboard)
        0x19,
        0xE0,  # Usage Minimum (Keyboard LeftControl)
        0x29,
        0xE7,  # Usage Maximum (Keyboard Right GUI)
        0x15,
        0x00,  # Logical Minimum (0)
        0x25,
        0x01,  # Logical Maximum (1)
        0x75,
        0x01,  # Report Size (1)
        0x95,
        0x08,  # Report Count (8)
        0x81,
        0x02,  # Input (Data,Var,Abs) modifier byte
        0x95,
        0x01,  # Report Count (1)
        0x75,
        0x08,  # Report Size (8)
        0x81,
        0x01,  # Input (Const) reserved byte
        0x95,
        0x05,  # Report Count (5)
        0x75,
        0x01,  # Report Size (1)
        0x05,
        0x08,  # Usage Page (LEDs)
        0x19,
        0x01,  # Usage Minimum (Num Lock)
        0x29,
        0x05,  # Usage Maximum (Kana)
        0x91,
        0x02,  # Output (Data,Var,Abs) LED report
        0x95,
        0x01,  # Report Count (1)
        0x75,
        0x03,  # Report Size (3)
        0x91,
        0x01,  # Output (Const) LED padding
        0x95,
        0x06,  # Report Count (6)
        0x75,
        0x08,  # Report Size (8)
        0x15,
        0x00,  # Logical Minimum (0)
        0x25,
        0x65,  # Logical Maximum (101)
        0x05,
        0x07,  # Usage Page (Keyboard)
        0x19,
        0x00,  # Usage Minimum (Reserved)
        0x29,
        0x65,  # Usage Maximum (Keyboard Application)
        0x81,
        0x00,  # Input (Data,Ary,Abs)
        0xC0,  # End Collection
    ]
)


HID_MOUSE_REPORT_DESC = bytes(
    [
        0x05,
        0x01,  # Usage Page (Generic Desktop)
        0x09,
        0x02,  # Usage (Mouse)
        0xA1,
        0x01,  # Collection (Application)
        0x09,
        0x01,  # Usage (Pointer)
        0xA1,
        0x00,  # Collection (Physical)
        0x05,
        0x09,  # Usage Page (Buttons)
        0x19,
        0x01,  # Usage Minimum (Button 1)
        0x29,
        0x03,  # Usage Maximum (Button 3)
        0x15,
        0x00,  # Logical Minimum (0)
        0x25,
        0x01,  # Logical Maximum (1)
        0x95,
        0x03,  # Report Count (3)
        0x75,
        0x01,  # Report Size (1)
        0x81,
        0x02,  # Input (Data,Var,Abs)
        0x95,
        0x01,  # Report Count (1)
        0x75,
        0x05,  # Report Size (5)
        0x81,
        0x01,  # Input (Const)
        0x05,
        0x01,  # Usage Page (Generic Desktop)
        0x09,
        0x30,  # Usage (X)
        0x09,
        0x31,  # Usage (Y)
        0x09,
        0x38,  # Usage (Wheel)
        0x15,
        0x81,  # Logical Minimum (-127)
        0x25,
        0x7F,  # Logical Maximum (127)
        0x75,
        0x08,  # Report Size (8)
        0x95,
        0x03,  # Report Count (3)
        0x81,
        0x06,  # Input (Data,Var,Rel)
        0xC0,  # End Collection
        0xC0,  # End Collection
    ]
)


MOD_LCTRL = 0x01
MOD_LSHIFT = 0x02
MOD_LALT = 0x04
MOD_LGUI = 0x08
MOD_RCTRL = 0x10
MOD_RSHIFT = 0x20
MOD_RALT = 0x40
MOD_RGUI = 0x80

KEY_NONE = 0x00
KEY_A = 0x04
KEY_B = 0x05
KEY_C = 0x06
KEY_D = 0x07
KEY_E = 0x08
KEY_F = 0x09
KEY_G = 0x0A
KEY_H = 0x0B
KEY_I = 0x0C
KEY_J = 0x0D
KEY_K = 0x0E
KEY_L = 0x0F
KEY_M = 0x10
KEY_N = 0x11
KEY_O = 0x12
KEY_P = 0x13
KEY_Q = 0x14
KEY_R = 0x15
KEY_S = 0x16
KEY_T = 0x17
KEY_U = 0x18
KEY_V = 0x19
KEY_W = 0x1A
KEY_X = 0x1B
KEY_Y = 0x1C
KEY_Z = 0x1D
KEY_1 = 0x1E
KEY_2 = 0x1F
KEY_3 = 0x20
KEY_4 = 0x21
KEY_5 = 0x22
KEY_6 = 0x23
KEY_7 = 0x24
KEY_8 = 0x25
KEY_9 = 0x26
KEY_0 = 0x27
KEY_ENTER = 0x28
KEY_ESC = 0x29
KEY_BACKSPACE = 0x2A
KEY_TAB = 0x2B
KEY_SPACE = 0x2C
KEY_MINUS = 0x2D
KEY_EQUAL = 0x2E
KEY_LEFTBRACE = 0x2F
KEY_RIGHTBRACE = 0x30
KEY_BACKSLASH = 0x31
KEY_SEMICOLON = 0x33
KEY_APOSTROPHE = 0x34
KEY_GRAVE = 0x35
KEY_COMMA = 0x36
KEY_DOT = 0x37
KEY_SLASH = 0x38
KEY_CAPSLOCK = 0x39
KEY_F1 = 0x3A
KEY_F2 = 0x3B
KEY_F3 = 0x3C
KEY_F4 = 0x3D
KEY_F5 = 0x3E
KEY_F6 = 0x3F
KEY_F7 = 0x40
KEY_F8 = 0x41
KEY_F9 = 0x42
KEY_F10 = 0x43
KEY_F11 = 0x44
KEY_F12 = 0x45
KEY_PRINTSCREEN = 0x46
KEY_SCROLLLOCK = 0x47
KEY_PAUSE = 0x48
KEY_INSERT = 0x49
KEY_HOME = 0x4A
KEY_PAGEUP = 0x4B
KEY_DELETE = 0x4C
KEY_END = 0x4D
KEY_PAGEDOWN = 0x4E
KEY_RIGHT = 0x4F
KEY_LEFT = 0x50
KEY_DOWN = 0x51
KEY_UP = 0x52

SPECIAL_KEYS: Dict[str, int] = {
    "ENTER": KEY_ENTER,
    "RETURN": KEY_ENTER,
    "ESC": KEY_ESC,
    "ESCAPE": KEY_ESC,
    "BACKSPACE": KEY_BACKSPACE,
    "TAB": KEY_TAB,
    "SPACE": KEY_SPACE,
    "CAPSLOCK": KEY_CAPSLOCK,
    "F1": KEY_F1,
    "F2": KEY_F2,
    "F3": KEY_F3,
    "F4": KEY_F4,
    "F5": KEY_F5,
    "F6": KEY_F6,
    "F7": KEY_F7,
    "F8": KEY_F8,
    "F9": KEY_F9,
    "F10": KEY_F10,
    "F11": KEY_F11,
    "F12": KEY_F12,
    "PRINTSCREEN": KEY_PRINTSCREEN,
    "SCROLLLOCK": KEY_SCROLLLOCK,
    "PAUSE": KEY_PAUSE,
    "INSERT": KEY_INSERT,
    "HOME": KEY_HOME,
    "PAGEUP": KEY_PAGEUP,
    "DELETE": KEY_DELETE,
    "DEL": KEY_DELETE,
    "END": KEY_END,
    "PAGEDOWN": KEY_PAGEDOWN,
    "RIGHT": KEY_RIGHT,
    "LEFT": KEY_LEFT,
    "DOWN": KEY_DOWN,
    "UP": KEY_UP,
}

US_KEYBOARD: Dict[str, Tuple[int, int]] = {
    "a": (KEY_A, 0),
    "b": (KEY_B, 0),
    "c": (KEY_C, 0),
    "d": (KEY_D, 0),
    "e": (KEY_E, 0),
    "f": (KEY_F, 0),
    "g": (KEY_G, 0),
    "h": (KEY_H, 0),
    "i": (KEY_I, 0),
    "j": (KEY_J, 0),
    "k": (KEY_K, 0),
    "l": (KEY_L, 0),
    "m": (KEY_M, 0),
    "n": (KEY_N, 0),
    "o": (KEY_O, 0),
    "p": (KEY_P, 0),
    "q": (KEY_Q, 0),
    "r": (KEY_R, 0),
    "s": (KEY_S, 0),
    "t": (KEY_T, 0),
    "u": (KEY_U, 0),
    "v": (KEY_V, 0),
    "w": (KEY_W, 0),
    "x": (KEY_X, 0),
    "y": (KEY_Y, 0),
    "z": (KEY_Z, 0),
    "A": (KEY_A, MOD_LSHIFT),
    "B": (KEY_B, MOD_LSHIFT),
    "C": (KEY_C, MOD_LSHIFT),
    "D": (KEY_D, MOD_LSHIFT),
    "E": (KEY_E, MOD_LSHIFT),
    "F": (KEY_F, MOD_LSHIFT),
    "G": (KEY_G, MOD_LSHIFT),
    "H": (KEY_H, MOD_LSHIFT),
    "I": (KEY_I, MOD_LSHIFT),
    "J": (KEY_J, MOD_LSHIFT),
    "K": (KEY_K, MOD_LSHIFT),
    "L": (KEY_L, MOD_LSHIFT),
    "M": (KEY_M, MOD_LSHIFT),
    "N": (KEY_N, MOD_LSHIFT),
    "O": (KEY_O, MOD_LSHIFT),
    "P": (KEY_P, MOD_LSHIFT),
    "Q": (KEY_Q, MOD_LSHIFT),
    "R": (KEY_R, MOD_LSHIFT),
    "S": (KEY_S, MOD_LSHIFT),
    "T": (KEY_T, MOD_LSHIFT),
    "U": (KEY_U, MOD_LSHIFT),
    "V": (KEY_V, MOD_LSHIFT),
    "W": (KEY_W, MOD_LSHIFT),
    "X": (KEY_X, MOD_LSHIFT),
    "Y": (KEY_Y, MOD_LSHIFT),
    "Z": (KEY_Z, MOD_LSHIFT),
    "1": (KEY_1, 0),
    "2": (KEY_2, 0),
    "3": (KEY_3, 0),
    "4": (KEY_4, 0),
    "5": (KEY_5, 0),
    "6": (KEY_6, 0),
    "7": (KEY_7, 0),
    "8": (KEY_8, 0),
    "9": (KEY_9, 0),
    "0": (KEY_0, 0),
    "!": (KEY_1, MOD_LSHIFT),
    "@": (KEY_2, MOD_LSHIFT),
    "#": (KEY_3, MOD_LSHIFT),
    "$": (KEY_4, MOD_LSHIFT),
    "%": (KEY_5, MOD_LSHIFT),
    "^": (KEY_6, MOD_LSHIFT),
    "&": (KEY_7, MOD_LSHIFT),
    "*": (KEY_8, MOD_LSHIFT),
    "(": (KEY_9, MOD_LSHIFT),
    ")": (KEY_0, MOD_LSHIFT),
    "\n": (KEY_ENTER, 0),
    "\r": (KEY_ENTER, 0),
    "\t": (KEY_TAB, 0),
    " ": (KEY_SPACE, 0),
    "-": (KEY_MINUS, 0),
    "_": (KEY_MINUS, MOD_LSHIFT),
    "=": (KEY_EQUAL, 0),
    "+": (KEY_EQUAL, MOD_LSHIFT),
    "[": (KEY_LEFTBRACE, 0),
    "{": (KEY_LEFTBRACE, MOD_LSHIFT),
    "]": (KEY_RIGHTBRACE, 0),
    "}": (KEY_RIGHTBRACE, MOD_LSHIFT),
    "\\": (KEY_BACKSLASH, 0),
    "|": (KEY_BACKSLASH, MOD_LSHIFT),
    ";": (KEY_SEMICOLON, 0),
    ":": (KEY_SEMICOLON, MOD_LSHIFT),
    "'": (KEY_APOSTROPHE, 0),
    '"': (KEY_APOSTROPHE, MOD_LSHIFT),
    "`": (KEY_GRAVE, 0),
    "~": (KEY_GRAVE, MOD_LSHIFT),
    ",": (KEY_COMMA, 0),
    "<": (KEY_COMMA, MOD_LSHIFT),
    ".": (KEY_DOT, 0),
    ">": (KEY_DOT, MOD_LSHIFT),
    "/": (KEY_SLASH, 0),
    "?": (KEY_SLASH, MOD_LSHIFT),
}


# ---------------------------------------------------------------------------
# Config and function classes
# ---------------------------------------------------------------------------


@dataclass
class GadgetConfig:
    gadget: "USBGadget"
    name: str = "c.1"
    configuration: str = "Config 1"
    max_power_ma: int = 250
    bm_attributes: Optional[int] = None

    @property
    def path(self) -> Path:
        return self.gadget.path / "configs" / self.name

    def create(self) -> None:
        _mkdir(self.path)
        strings = _mkdir(self.path / "strings" / self.gadget.language)
        _write_text(strings / "configuration", self.configuration)
        _write_text(self.path / "MaxPower", int(self.max_power_ma / 2))
        if self.bm_attributes is not None:
            _write_text(self.path / "bmAttributes", f"0x{self.bm_attributes:02x}")

    def link(self, func: "USBFunction") -> None:
        target = func.path
        link = self.path / func.func_name
        if link.exists() or link.is_symlink():
            return
        link.symlink_to(target)

    def unlink_all(self) -> None:
        if not self.path.exists():
            return
        for child in self.path.iterdir():
            if child.is_symlink():
                child.unlink()


@dataclass
class USBFunction:
    gadget: "USBGadget"
    kind: str
    name: str
    config: str = "c.1"

    @property
    def func_name(self) -> str:
        return f"{self.kind}.{self.name}"

    @property
    def path(self) -> Path:
        return self.gadget.path / "functions" / self.func_name

    def create(self) -> None:
        _mkdir(self.path)

    def after_bind(self, before_devices: Dict[str, List[Path]]) -> None:
        pass

    def remove(self) -> None:
        _safe_rmdir(self.path)


@dataclass
class MassStorageLUN:
    backing_file: Optional[PathLike] = None
    removable: bool = True
    readonly: bool = False
    cdrom: bool = False
    nofua: bool = False
    inquiry_string: Optional[str] = None


@dataclass
class MassStorageFunction(USBFunction):
    stall: bool = True
    luns: List[MassStorageLUN] = field(default_factory=list)

    def __init__(
        self,
        gadget: "USBGadget",
        name: str,
        *,
        backing_file: Optional[PathLike] = None,
        removable: bool = True,
        readonly: bool = False,
        cdrom: bool = False,
        nofua: bool = False,
        stall: bool = True,
        inquiry_string: Optional[str] = None,
        config: str = "c.1",
    ) -> None:
        super().__init__(gadget=gadget, kind="mass_storage", name=name, config=config)
        self.stall = stall
        self.luns = [
            MassStorageLUN(
                backing_file=backing_file,
                removable=removable,
                readonly=readonly,
                cdrom=cdrom,
                nofua=nofua,
                inquiry_string=inquiry_string,
            )
        ]

    def add_lun(
        self,
        *,
        backing_file: Optional[PathLike],
        removable: bool = True,
        readonly: bool = False,
        cdrom: bool = False,
        nofua: bool = False,
        inquiry_string: Optional[str] = None,
    ) -> "MassStorageFunction":
        if self.path.exists():
            raise FunctionError("Add all mass-storage LUNs before create()/bind().")
        self.luns.append(
            MassStorageLUN(
                backing_file=backing_file,
                removable=removable,
                readonly=readonly,
                cdrom=cdrom,
                nofua=nofua,
                inquiry_string=inquiry_string,
            )
        )
        return self

    def create(self) -> None:
        super().create()
        _write_text(self.path / "stall", 1 if self.stall else 0)

        for i, lun in enumerate(self.luns):
            lun_path = self.path / f"lun.{i}"
            _mkdir(lun_path)
            _write_text(lun_path / "removable", 1 if lun.removable else 0)
            _write_text(lun_path / "ro", 1 if lun.readonly else 0)
            _write_text(lun_path / "cdrom", 1 if lun.cdrom else 0)
            nofua_path = lun_path / "nofua"
            if nofua_path.exists():
                _write_text(nofua_path, 1 if lun.nofua else 0)
            if lun.inquiry_string is not None:
                inquiry_path = lun_path / "inquiry_string"
                if inquiry_path.exists():
                    _write_text(inquiry_path, lun.inquiry_string[:36])
            # Attach the backing file last; some kernels reject later LUN
            # attribute changes once storage is activated.
            if lun.backing_file is not None:
                _write_text(lun_path / "file", str(Path(lun.backing_file)))

    def set_backing_file(self, path: Optional[PathLike], *, lun: int = 0) -> None:
        lun_path = self.path / f"lun.{lun}" / "file"
        if not lun_path.exists():
            raise FunctionError(f"LUN {lun} does not exist for {self.func_name}.")
        _write_text(lun_path, "" if path is None else str(Path(path)))

    def eject(self, *, lun: int = 0) -> None:
        self.set_backing_file(None, lun=lun)


@dataclass
class SerialFunction(USBFunction):
    device: Optional[Path] = None

    def __init__(self, gadget: "USBGadget", name: str, *, config: str = "c.1") -> None:
        super().__init__(gadget=gadget, kind="acm", name=name, config=config)
        self.device = None

    def after_bind(self, before_devices: Dict[str, List[Path]]) -> None:
        self.device = _wait_for_new_device("/dev/ttyGS*", before_devices.get("ttyGS", []))
        if self.device is None:
            self.device = _wait_for_any_device("/dev/ttyGS*")

    def open(self, mode: str = "r+b", buffering: int = 0) -> BinaryIO:
        if self.device is None:
            self.device = _wait_for_any_device("/dev/ttyGS*")
        if self.device is None:
            raise GadgetNotBoundError("No /dev/ttyGS* device found. Is the gadget bound?")
        return open(self.device, mode, buffering=buffering)


@dataclass
class HIDFunction(USBFunction):
    report_desc: bytes = b""
    report_length: int = 0
    protocol: int = 0
    subclass: int = 0
    device: Optional[Path] = None

    def __init__(
        self,
        gadget: "USBGadget",
        name: str,
        *,
        report_desc: bytes,
        report_length: int,
        protocol: int = 0,
        subclass: int = 0,
        config: str = "c.1",
    ) -> None:
        super().__init__(gadget=gadget, kind="hid", name=name, config=config)
        self.report_desc = bytes(report_desc)
        self.report_length = int(report_length)
        self.protocol = int(protocol)
        self.subclass = int(subclass)
        self.device = None

    def create(self) -> None:
        super().create()
        _write_text(self.path / "protocol", self.protocol)
        _write_text(self.path / "subclass", self.subclass)
        _write_text(self.path / "report_length", self.report_length)
        _write_bytes(self.path / "report_desc", self.report_desc)

    def after_bind(self, before_devices: Dict[str, List[Path]]) -> None:
        self.device = _wait_for_new_device("/dev/hidg*", before_devices.get("hidg", []))
        if self.device is None:
            # Fallback for already-bound or weird enumeration order.
            available = _dev_glob("/dev/hidg*")
            used = {str(f.device) for f in self.gadget.functions if isinstance(f, HIDFunction) and f.device}
            for p in available:
                if str(p) not in used:
                    self.device = p
                    break
            if self.device is None:
                self.device = available[0] if available else None

    def open(self, mode: str = "r+b", buffering: int = 0) -> BinaryIO:
        if self.device is None:
            self.device = _wait_for_any_device("/dev/hidg*")
        if self.device is None:
            raise GadgetNotBoundError("No /dev/hidg* device found. Is the gadget bound?")
        return open(self.device, mode, buffering=buffering)

    def send_report(self, report: Union[bytes, bytearray, Sequence[int]]) -> None:
        data = bytes(report)
        if len(data) != self.report_length:
            raise ValueError(f"Expected {self.report_length} bytes, got {len(data)}.")
        with self.open("r+b", buffering=0) as f:
            f.write(data)
            f.flush()


class HIDKeyboard(HIDFunction):
    """Boot-protocol HID keyboard helper with a US keyboard layout map."""

    def __init__(self, gadget: "USBGadget", name: str, *, config: str = "c.1") -> None:
        super().__init__(
            gadget,
            name,
            report_desc=HID_KEYBOARD_REPORT_DESC,
            report_length=8,
            protocol=1,
            subclass=1,
            config=config,
        )

    def report(self, keys: Sequence[int] = (), modifiers: int = 0) -> bytes:
        key_list = list(keys)[:6]
        key_list += [0] * (6 - len(key_list))
        return bytes([modifiers & 0xFF, 0x00, *[k & 0xFF for k in key_list]])

    def press(self, *keys: int, modifiers: int = 0) -> None:
        self.send_report(self.report(keys, modifiers))

    def release(self) -> None:
        self.send_report(b"\x00" * 8)

    def tap(self, *keys: int, modifiers: int = 0, delay: float = 0.02) -> None:
        self.press(*keys, modifiers=modifiers)
        time.sleep(delay)
        self.release()
        time.sleep(delay)

    def key(self, name: str, *, modifiers: int = 0, delay: float = 0.02) -> None:
        keycode = SPECIAL_KEYS.get(name.upper())
        if keycode is None:
            raise KeyError(f"Unknown special key: {name!r}")
        self.tap(keycode, modifiers=modifiers, delay=delay)

    def write_char(self, ch: str, *, delay: float = 0.01) -> None:
        if ch not in US_KEYBOARD:
            raise KeyError(f"No US-keyboard mapping for character: {ch!r}")
        keycode, mod = US_KEYBOARD[ch]
        self.tap(keycode, modifiers=mod, delay=delay)

    def write_text(self, text: str, *, delay: float = 0.01) -> None:
        for ch in text:
            self.write_char(ch, delay=delay)

    def hotkey(self, *names: str, delay: float = 0.02) -> None:
        """
        Press a simple named hotkey, e.g. hotkey("CTRL", "ALT", "DELETE").
        Modifiers: CTRL, SHIFT, ALT, GUI/WIN, RCTRL, RSHIFT, RALT, RGUI.
        """
        mods = 0
        keys: List[int] = []
        for name in names:
            n = name.upper()
            if n in {"CTRL", "CONTROL", "LCTRL"}:
                mods |= MOD_LCTRL
            elif n in {"SHIFT", "LSHIFT"}:
                mods |= MOD_LSHIFT
            elif n in {"ALT", "LALT"}:
                mods |= MOD_LALT
            elif n in {"GUI", "WIN", "WINDOWS", "CMD", "META", "LGUI"}:
                mods |= MOD_LGUI
            elif n == "RCTRL":
                mods |= MOD_RCTRL
            elif n == "RSHIFT":
                mods |= MOD_RSHIFT
            elif n == "RALT":
                mods |= MOD_RALT
            elif n == "RGUI":
                mods |= MOD_RGUI
            elif len(name) == 1 and name in US_KEYBOARD:
                keycode, char_mod = US_KEYBOARD[name]
                mods |= char_mod
                keys.append(keycode)
            elif n in SPECIAL_KEYS:
                keys.append(SPECIAL_KEYS[n])
            else:
                raise KeyError(f"Unknown key name: {name!r}")
        self.tap(*keys, modifiers=mods, delay=delay)


class HIDMouse(HIDFunction):
    """Simple relative HID mouse helper with 3 buttons and a wheel."""

    LEFT = 0x01
    RIGHT = 0x02
    MIDDLE = 0x04

    def __init__(self, gadget: "USBGadget", name: str, *, config: str = "c.1") -> None:
        super().__init__(
            gadget,
            name,
            report_desc=HID_MOUSE_REPORT_DESC,
            report_length=4,
            protocol=2,
            subclass=1,
            config=config,
        )

    @staticmethod
    def _i8(value: int) -> int:
        if value < -127 or value > 127:
            raise ValueError("HID mouse deltas must be between -127 and 127.")
        return value & 0xFF

    def move(self, x: int = 0, y: int = 0, wheel: int = 0, *, buttons: int = 0) -> None:
        self.send_report(bytes([buttons & 0x07, self._i8(x), self._i8(y), self._i8(wheel)]))

    def button(self, buttons: int, *, down_time: float = 0.05) -> None:
        self.move(buttons=buttons)
        time.sleep(down_time)
        self.move(buttons=0)

    def click(self, button: int = LEFT, *, down_time: float = 0.05) -> None:
        self.button(button, down_time=down_time)

    def scroll(self, amount: int) -> None:
        while amount:
            step = max(-127, min(127, amount))
            self.move(wheel=step)
            amount -= step


# ---------------------------------------------------------------------------
# Main gadget class
# ---------------------------------------------------------------------------


class USBGadget:
    """
    Represents a Linux USB ConfigFS gadget.

    The class intentionally maps closely to ConfigFS, while still providing
    enough guardrails to avoid the classic "why is my UDC wedged" experience.
    """

    def __init__(
        self,
        name: str,
        *,
        vendor_id: int = 0x1D6B,
        product_id: int = 0x0104,
        bcd_usb: int = 0x0200,
        bcd_device: int = 0x0100,
        device_class: int = 0xEF,
        device_subclass: int = 0x02,
        device_protocol: int = 0x01,
        manufacturer: str = "Linux",
        product: str = "USB Gadget",
        serial: Optional[str] = None,
        language: str = "0x409",
        configfs_root: PathLike = USB_GADGET_ROOT,
        clobber: bool = False,
        auto_modprobe: bool = True,
    ) -> None:
        self.name = name
        self.vendor_id = int(vendor_id)
        self.product_id = int(product_id)
        self.bcd_usb = int(bcd_usb)
        self.bcd_device = int(bcd_device)
        self.device_class = int(device_class)
        self.device_subclass = int(device_subclass)
        self.device_protocol = int(device_protocol)
        self.manufacturer = manufacturer
        self.product = product
        self.serial = serial if serial is not None else _get_rpi_serial()
        self.language = language
        self.configfs_root = Path(configfs_root)
        self.path = self.configfs_root / self.name
        self.configs: Dict[str, GadgetConfig] = {}
        self.functions: List[USBFunction] = []
        self.clobber = clobber
        self.auto_modprobe = auto_modprobe
        self._created = False

    # ---- construction -----------------------------------------------------

    def add_config(
        self,
        name: str = "c.1",
        *,
        configuration: str = "Config 1",
        max_power_ma: int = 250,
        bm_attributes: Optional[int] = None,
    ) -> GadgetConfig:
        cfg = GadgetConfig(
            gadget=self,
            name=name,
            configuration=configuration,
            max_power_ma=max_power_ma,
            bm_attributes=bm_attributes,
        )
        self.configs[name] = cfg
        return cfg

    def _ensure_config(self, config: str) -> None:
        if config not in self.configs:
            self.add_config(config)

    def add_mass_storage(
        self,
        name: str = "usb0",
        *,
        backing_file: Optional[PathLike],
        removable: bool = True,
        readonly: bool = False,
        cdrom: bool = False,
        nofua: bool = False,
        stall: bool = True,
        inquiry_string: Optional[str] = None,
        config: str = "c.1",
    ) -> MassStorageFunction:
        self._ensure_config(config)
        f = MassStorageFunction(
            self,
            name,
            backing_file=backing_file,
            removable=removable,
            readonly=readonly,
            cdrom=cdrom,
            nofua=nofua,
            stall=stall,
            inquiry_string=inquiry_string,
            config=config,
        )
        self.functions.append(f)
        return f

    def add_serial(self, name: str = "usb0", *, config: str = "c.1") -> SerialFunction:
        self._ensure_config(config)
        f = SerialFunction(self, name, config=config)
        self.functions.append(f)
        return f

    def add_hid(
        self,
        name: str,
        *,
        report_desc: bytes,
        report_length: int,
        protocol: int = 0,
        subclass: int = 0,
        config: str = "c.1",
    ) -> HIDFunction:
        self._ensure_config(config)
        f = HIDFunction(
            self,
            name,
            report_desc=report_desc,
            report_length=report_length,
            protocol=protocol,
            subclass=subclass,
            config=config,
        )
        self.functions.append(f)
        return f

    def add_hid_keyboard(self, name: str = "usb0", *, config: str = "c.1") -> HIDKeyboard:
        self._ensure_config(config)
        f = HIDKeyboard(self, name, config=config)
        self.functions.append(f)
        return f

    def add_hid_mouse(self, name: str = "usb0", *, config: str = "c.1") -> HIDMouse:
        self._ensure_config(config)
        f = HIDMouse(self, name, config=config)
        self.functions.append(f)
        return f

    # ---- ConfigFS lifecycle ----------------------------------------------

    def create(self) -> "USBGadget":
        require_root()
        if self.auto_modprobe:
            modprobe("libcomposite")
            # These are optional because some kernels autoload them.
            for module in ("usb_f_mass_storage", "usb_f_acm", "usb_f_hid"):
                modprobe(module)

        ensure_configfs_mounted()
        self.configfs_root.mkdir(parents=True, exist_ok=True)

        if self.path.exists():
            if not self.clobber:
                raise GadgetExistsError(f"Gadget already exists: {self.path}")
            self.unbind(ignore_missing=True)
            self.remove(ignore_missing=True)

        _mkdir(self.path)

        _write_text(self.path / "idVendor", f"0x{self.vendor_id:04x}")
        _write_text(self.path / "idProduct", f"0x{self.product_id:04x}")
        _write_text(self.path / "bcdUSB", f"0x{self.bcd_usb:04x}")
        _write_text(self.path / "bcdDevice", f"0x{self.bcd_device:04x}")
        _write_text(self.path / "bDeviceClass", f"0x{self.device_class:02x}")
        _write_text(self.path / "bDeviceSubClass", f"0x{self.device_subclass:02x}")
        _write_text(self.path / "bDeviceProtocol", f"0x{self.device_protocol:02x}")

        strings = _mkdir(self.path / "strings" / self.language)
        _write_text(strings / "manufacturer", self.manufacturer)
        _write_text(strings / "product", self.product)
        _write_text(strings / "serialnumber", self.serial)

        if not self.configs:
            self.add_config("c.1")

        for cfg in self.configs.values():
            cfg.create()

        for func in self.functions:
            func.create()
            self.configs[func.config].link(func)

        self._created = True
        return self

    @property
    def bound_udc(self) -> str:
        udc_path = self.path / "UDC"
        if not udc_path.exists():
            return ""
        return _read_text(udc_path)

    def is_bound(self) -> bool:
        return bool(self.path.exists() and self.bound_udc)

    def bind(self, udc: Optional[str] = None, *, timeout: float = 5.0) -> "USBGadget":
        require_root()
        if not self.path.exists() or not self._created:
            self.create()
        if self.bound_udc:
            raise GadgetAlreadyBoundError(f"Gadget already bound to {self.bound_udc}.")

        before = {
            "hidg": _dev_glob("/dev/hidg*"),
            "ttyGS": _dev_glob("/dev/ttyGS*"),
        }

        chosen = pick_udc(udc)
        _write_text(self.path / "UDC", chosen)

        # Give the function devices a moment to appear.
        deadline = time.monotonic() + timeout
        for func in self.functions:
            remaining = max(0.1, deadline - time.monotonic())
            if isinstance(func, (HIDFunction, SerialFunction)):
                # The helper internally waits. Adjust per-function by temporary
                # monkey-free minimalism: just call it; it has its own 5 sec cap.
                func.after_bind(before)
            else:
                func.after_bind(before)
            if time.monotonic() > deadline:
                break

        return self

    def unbind(self, *, ignore_missing: bool = False) -> "USBGadget":
        require_root()
        udc_path = self.path / "UDC"
        if not udc_path.exists():
            if ignore_missing:
                return self
            raise GadgetError(f"Gadget does not exist: {self.path}")
        try:
            if _read_text(udc_path):
                _write_text(udc_path, "")
        except OSError:
            if not ignore_missing:
                raise
        return self

    def remove(self, *, ignore_missing: bool = False) -> None:
        require_root()
        if not self.path.exists():
            if ignore_missing:
                return
            raise GadgetError(f"Gadget does not exist: {self.path}")

        self.unbind(ignore_missing=True)

        for cfg in self.configs.values():
            cfg.unlink_all()

        # Also remove any config symlinks we did not track, because clobbering
        # a manually edited gadget should not strand function directories.
        configs_root = self.path / "configs"
        if configs_root.exists():
            for cfg_path in configs_root.iterdir():
                if cfg_path.is_dir():
                    for child in cfg_path.iterdir():
                        if child.is_symlink():
                            child.unlink()

        # Remove function dirs. Some functions create lun.* subdirs; remove those
        # first, then the function itself.
        functions_root = self.path / "functions"
        if functions_root.exists():
            for func_path in sorted(functions_root.iterdir(), reverse=True):
                if func_path.is_dir() and not func_path.is_symlink():
                    # Remove function dirs. ConfigFS is not a normal filesystem:
                    # mass_storage/lun.* directories are kernel-managed and must not be
                    # rmdir'd directly. Clear their backing file, then remove the function.
                    functions_root = self.path / "functions"
                    if functions_root.exists():
                        for func_path in sorted(functions_root.iterdir(), reverse=True):
                            if not func_path.is_dir() or func_path.is_symlink():
                                continue

                            if func_path.name.startswith("mass_storage."):
                                for lun_path in sorted(func_path.glob("lun.*")):
                                    file_attr = lun_path / "file"
                                    if file_attr.exists():
                                        try:
                                            _write_text(file_attr, "")
                                        except OSError:
                                            pass

                            _safe_rmdir(func_path)
                    _safe_rmdir(func_path)

        _remove_tree_bottom_up(self.path)
        self._created = False

    def status(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "path": str(self.path),
            "exists": self.path.exists(),
            "bound": self.is_bound(),
            "udc": self.bound_udc if self.path.exists() else "",
            "configs": list(self.configs.keys()),
            "functions": [f.func_name for f in self.functions],
            "hid_devices": [str(f.device) for f in self.functions if isinstance(f, HIDFunction) and f.device],
            "serial_devices": [str(f.device) for f in self.functions if isinstance(f, SerialFunction) and f.device],
        }

    def __enter__(self) -> "USBGadget":
        self.create()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.unbind(ignore_missing=True)
        self.remove(ignore_missing=True)


# ---------------------------------------------------------------------------
# Convenience builders
# ---------------------------------------------------------------------------

def cleanup_gadget(name: str, *, configfs_root: PathLike = USB_GADGET_ROOT) -> None:
    """Best-effort unbind and remove an existing gadget by name."""
    g = USBGadget(name, configfs_root=configfs_root, clobber=True)
    g.unbind(ignore_missing=True)
    g.remove(ignore_missing=True)


__all__ = [
    "CONFIGFS_ROOT",
    "USB_GADGET_ROOT",
    "UDC_ROOT",
    "GadgetError",
    "RootRequiredError",
    "GadgetExistsError",
    "GadgetNotBoundError",
    "GadgetAlreadyBoundError",
    "FunctionError",
    "USBGadget",
    "GadgetConfig",
    "USBFunction",
    "MassStorageFunction",
    "MassStorageLUN",
    "SerialFunction",
    "HIDFunction",
    "HIDKeyboard",
    "HIDMouse",
    "HID_KEYBOARD_REPORT_DESC",
    "HID_MOUSE_REPORT_DESC",
    "MOD_LCTRL",
    "MOD_LSHIFT",
    "MOD_LALT",
    "MOD_LGUI",
    "MOD_RCTRL",
    "MOD_RSHIFT",
    "MOD_RALT",
    "MOD_RGUI",
    "SPECIAL_KEYS",
    "US_KEYBOARD",
    "require_root",
    "run",
    "modprobe",
    "ensure_configfs_mounted",
    "mount_tmpfs",
    "unmount",
    "parse_size",
    "make_backing_file",
    "list_udcs",
    "pick_udc",
    "cleanup_gadget",
]
