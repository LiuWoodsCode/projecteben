#!/usr/bin/env python3

import os
import subprocess
import tempfile
from pathlib import Path

FAKE_DMI = {
    "/sys/devices/virtual/dmi/id/chassis_type": "10\n",  # Notebook
    "/sys/class/dmi/id/chassis_type": "10\n",

    "/sys/class/dmi/id/sys_vendor": "Framework\n",
    "/sys/class/dmi/id/product_name": "Laptop 13 (AMD Ryzen 7040Series)\n",
    "/sys/class/dmi/id/product_version": "A7\n",
    "/sys/class/dmi/id/board_vendor": "Framework\n",
    "/sys/class/dmi/id/board_name": "FRANMDCP07\n",
}


def run(*args):
    print("+", *args)
    subprocess.run(args, check=True)


def overlay_file(target, contents):
    tmpdir = tempfile.mkdtemp(prefix="frameworkspoof-")

    fakefile = os.path.join(tmpdir, "value")

    with open(fakefile, "w") as f:
        f.write(contents)

    run("mount", "--bind", fakefile, target)


def battery_exists():
    ps = Path("/sys/class/power_supply")

    if not ps.exists():
        return False

    for supply in ps.iterdir():
        type_file = supply / "type"

        try:
            if type_file.read_text().strip() == "Battery":
                scope_file = supply / "scope"

                if not scope_file.exists():
                    return True

                if scope_file.read_text().strip() != "Device":
                    return True

        except Exception:
            pass

    return False


def create_fake_battery():
    print("No battery detected; creating fake BAT0")

    base = "/tmp/fake-battery/BAT0"
    os.makedirs(base, exist_ok=True)

    files = {
        "type": "Battery\n",
        "status": "Discharging\n",
        "capacity": "85\n",
        "scope": "System\n",
    }

    for name, value in files.items():
        with open(os.path.join(base, name), "w") as f:
            f.write(value)

    run(
        "mount",
        "--bind",
        "/tmp/fake-battery/BAT0",
        "/sys/class/power_supply/BAT0"
    )


def main():
    if os.geteuid() != 0:
        raise SystemExit("Must run as root")

    for target, value in FAKE_DMI.items():
        if os.path.exists(target):
            try:
                overlay_file(target, value)
            except Exception as e:
                print(f"Failed to spoof {target}: {e}")

    if not battery_exists():
        print("Battery not found.")
        print("Creating fake battery...")
        try:
            create_fake_battery()
        except Exception as e:
            print("Battery spoof failed:", e)
    else:
        print("Real battery already exists; skipping battery spoof.")

    print("Framework Laptop 13 spoof applied.")


if __name__ == "__main__":
    main()

