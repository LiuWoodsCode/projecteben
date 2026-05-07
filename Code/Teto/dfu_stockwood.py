#!/usr/bin/env python3

import os
import subprocess
import signal
import sys
import time
from pathlib import Path

from teto import USBGadget


MMC_DEVICE = "/dev/mmcblk0"

# Partitions commonly mounted on Raspberry Pi OS
POSSIBLE_PARTITIONS = [
    "/dev/mmcblk0p1",
    "/dev/mmcblk0p2",
]


def run(cmd):
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def is_mounted(device):
    with open("/proc/mounts", "r") as f:
        for line in f:
            if line.startswith(device + " "):
                return True
    return False


def unmount_partition(device):
    if is_mounted(device):
        print(f"Unmounting {device}...")
        run(["umount", device])


def remount_partition(device):
    print(f"Remounting {device}...")
    run(["mount", device])


def safe_unmount_all():
    print("Syncing filesystems...")
    run(["sync"])

    for part in POSSIBLE_PARTITIONS:
        if Path(part).exists():
            try:
                unmount_partition(part)
            except subprocess.CalledProcessError as e:
                print(f"WARNING: Failed to unmount {part}: {e}")


def safe_remount_all():
    for part in POSSIBLE_PARTITIONS:
        if Path(part).exists():
            try:
                remount_partition(part)
            except subprocess.CalledProcessError as e:
                print(f"WARNING: Failed to remount {part}: {e}")


def cleanup(gadget):
    print("\nCleaning up gadget...")

    try:
        gadget.unbind()
    except Exception as e:
        print("unbind failed:", e)

    try:
        gadget.remove()
    except Exception as e:
        print("remove failed:", e)

    print("Re-mounting partitions...")
    safe_remount_all()


def main():
    if os.geteuid() != 0:
        print("Run as root.")
        sys.exit(1)

    print("Preparing MMC device for USB export...")

    safe_unmount_all()

    print("Final sync...")
    run(["sync"])

    # Create gadget
    g = USBGadget(
        name="mmcshare",
        vendor_id=0xFFFF,
        product_id=0xFFFF,
        manufacturer="Pixel Prowler",
        product="NoelleStockwood Device Firmware Recovery mass storage device",
        clobber=True,
    )

    g.add_config(
        "c.1",
        configuration="Mass Storage",
        max_power_ma=250,
    )

    # Export the ENTIRE block device
    g.add_mass_storage(
        "storage",
        backing_file=MMC_DEVICE,
        removable=False,
        readonly=False,

        # Important:
        # Prevents aggressive writeback behavior from some hosts.
        nofua=True,
    )

    print("Binding gadget...")
    g.bind()

    print()
    print("USB Mass Storage gadget active.")
    print(f"Host now sees: {MMC_DEVICE}")
    print()
    print("DO NOT mount/write the device locally while exported.")
    print("Press Ctrl+C to stop.")

    def signal_handler(sig, frame):
        cleanup(g)
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()