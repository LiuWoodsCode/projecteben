"""Configuration defaults for the Pi Zero adapter service."""

from __future__ import annotations

import os


DEFAULT_HOST = os.environ.get("PIZERO_ADAPTER_HOST", "0.0.0.0")
DEFAULT_PORT = int(os.environ.get("PIZERO_ADAPTER_PORT", "5000"))
DEFAULT_WIFI_INTERFACE = os.environ.get("PIZERO_WIFI_IFACE", "wlan0")
DEFAULT_USB_INTERFACE = os.environ.get("PIZERO_USB_IFACE", "usb0")
DEFAULT_USB_PROFILE = os.environ.get("PIZERO_USB_PROFILE", "USB Gadget (shared)")

