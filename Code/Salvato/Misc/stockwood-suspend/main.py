#!/usr/bin/env python3

import argparse
import json
import os
import re
import pwd
import signal
import select
import logging
import subprocess
import sys
import traceback
import time
import threading
from glob import glob
from evdev import InputDevice, ecodes, list_devices

# Skip these process names when suspending regular-user processes
BLOCKED_PROCESS_NAMES = {
    "labwc",
    "python",
    "wf-panel-pi",
    "wlr-randr",
    "Xwayland",
    "pipewire",
    "wireplumber",
    "dbus-daemon",
    "xdg-desktop-portal",
    "gvfs",
    "gnome-keyring-daemon",
    "bash",
    "systemd",
    "sd-pam",
    "sshd",
    "sshd-session",
    "login",
    "zsh",
    "sudo",
    "su",
    "python3",  # your own process
    "sway",
    "swaybg",
}

BLOCKED_PROCESS_PREFIXES = (
    "python",
    "ssh",
    "sway"
)

SLEEP_STATE_FILE = "/tmp/pi_fake_sleep_pids.txt"
DISPLAY_STATE_FILE = "/tmp/pi_fake_sleep_displays.json"
SERVICE_STATE_FILE = "/tmp/pi_fake_sleep_services.json"
COOLING_STATE_FILE = "/tmp/pi_fake_sleep_cooling.json"
DEFAULT_DISPLAY_MODE = "1920x1080"
CPU_SLOW_GOVERNOR = "powersave"
CPU_SLOW_MAX_FREQ = "100000"
CPU_RESTORE_GOVERNOR = "ondemand"
CPU_RESTORE_MAX_FREQ = "2400000"
COOLING_REFRESH_SECONDS = 5
REASSERTION_REFRESH_SECONDS = 30


ESSENTIAL_SYSTEMD_SERVICES = {
    "dbus.service",
    "systemd-journald.service",
    "systemd-logind.service",
    "systemd-udevd.service",
    "polkit.service",
    "NetworkManager.service",
    "wpa_supplicant.service",
    "lightdm.service",
    "accounts-daemon.service",
    "sshd.service",
    "ssh.service",
    "rpi-hotspot.service",
}

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(levelname)s] %(message)s",
)
LOGGER = logging.getLogger("stockwood-suspend")


def run(cmd):
    LOGGER.debug("Running shell command: %s", cmd)
    return subprocess.run(cmd, shell=True, check=False)


def run_args(args):
    LOGGER.debug("Running command: %s", " ".join(args))
    return subprocess.run(args, check=False)


def write_sysfs_value(path, value):
    LOGGER.debug("Writing %s to %s", value, path)
    with open(path, "w") as f:
        f.write(str(value))


def is_protected_systemd_service(unit):
    return (
        unit in ESSENTIAL_SYSTEMD_SERVICES
        or re.fullmatch(r"user@\d+\.service", unit)
        or re.fullmatch(r"getty@tty\d+\.service", unit)
        or re.fullmatch(r"serial-getty@tty[A-Za-z0-9]+\.service", unit)
    )


def is_blocked_process_name(name):
    lowered = name.lower()

    if lowered in BLOCKED_PROCESS_NAMES:
        return True

    return any(lowered.startswith(prefix) for prefix in BLOCKED_PROCESS_PREFIXES)


def get_process_uid(pid):
    with open(f"/proc/{pid}/status", "r") as f:
        for line in f:
            if line.startswith("Uid:"):
                parts = line.split()
                if len(parts) >= 2:
                    return int(parts[1])

    raise RuntimeError(f"Unable to determine uid for pid {pid}")


def is_regular_user_uid(uid):
    if uid == 0:
        return False

    try:
        return pwd.getpwuid(uid).pw_uid >= 1000
    except KeyError:
        return False


def is_part_of_systemd_service(pid):
    try:
        with open(f"/proc/{pid}/cgroup", "r") as f:
            for line in f:
                parts = line.strip().split(":", 2)
                if len(parts) != 3:
                    continue

                cgroup_path = parts[2].rstrip("/")
                if not cgroup_path or cgroup_path == "/":
                    continue

                if os.path.basename(cgroup_path).endswith(".service"):
                    return True
    except Exception:
        LOGGER.debug("Unable to inspect cgroup for pid %s", pid, exc_info=True)

    return False


def get_session_user():
    session_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or os.environ.get("LOGNAME")

    if not session_user:
        raise RuntimeError("Unable to determine the session user")

    return session_user


def get_session_user_info():
    session_user = get_session_user()

    try:
        return session_user, pwd.getpwnam(session_user)
    except KeyError as exc:
        raise RuntimeError(f"Unable to resolve session user: {session_user}") from exc

def get_wayland_env():
    env = os.environ.copy()

    if env.get("XDG_RUNTIME_DIR") and env.get("WAYLAND_DISPLAY"):
        LOGGER.debug(
            "Using existing Wayland environment: XDG_RUNTIME_DIR=%s WAYLAND_DISPLAY=%s",
            env.get("XDG_RUNTIME_DIR"),
            env.get("WAYLAND_DISPLAY"),
        )
        return env

    session_user = get_session_user()

    user_info = pwd.getpwnam(session_user)
    runtime_dir = f"/run/user/{user_info.pw_uid}"

    if not os.path.isdir(runtime_dir):
        raise RuntimeError(f"Wayland runtime directory not found: {runtime_dir}")

    env["XDG_RUNTIME_DIR"] = runtime_dir

    if not env.get("WAYLAND_DISPLAY"):
        sockets = sorted(glob(os.path.join(runtime_dir, "wayland-*")))
        if not sockets:
            raise RuntimeError(f"No Wayland socket found in {runtime_dir}")

        env["WAYLAND_DISPLAY"] = os.path.basename(sockets[0])

    LOGGER.debug(
        "Using recovered Wayland environment: user=%s XDG_RUNTIME_DIR=%s WAYLAND_DISPLAY=%s",
        session_user,
        env.get("XDG_RUNTIME_DIR"),
        env.get("WAYLAND_DISPLAY"),
    )
    return env


def lock_machine():
    LOGGER.info("Locking screen with swaylock")
    env = get_wayland_env()
    session_user, user_info = get_session_user_info()
    env["HOME"] = user_info.pw_dir

    def drop_privileges():
        os.initgroups(session_user, user_info.pw_gid)
        os.setgid(user_info.pw_gid)
        os.setuid(user_info.pw_uid)

    result = subprocess.run(
        ["swaylock", "-p"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        preexec_fn=drop_privileges,
    )

    if result.returncode != 0:
        output = result.stdout.strip() or result.stderr.strip()
        raise RuntimeError(f"swaylock failed: {output or 'no output'}")


def show_error(msg):
    try:
        LOGGER.error(msg)
        subprocess.Popen(["zenity", "--error", "--text", msg])
    except Exception:
        print("[ERROR]", msg)


def show_panic_warning(msg, timeout_seconds=10):
    try:
        LOGGER.error(msg)
        subprocess.Popen(
            [
                "zenity",
                "--error",
                "--text",
                msg,
            ]
        )
    except Exception:
        print("[ERROR]", msg)

def kernel_panic(enabled=True):
    if not enabled:
        LOGGER.info("Kernel panic disabled by no-panic mode")
        return

    print("[!] Triggering kernel panic...")
    try:
        with open("/proc/sys/kernel/sysrq", "w") as f:
            f.write("1")
        with open("/proc/sysrq-trigger", "w") as f:
            f.write("c")
    except Exception as e:
        print("[CRITICAL] Failed to panic:", e)


def get_target_pids():
    pids = []

    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue

        try:
            with open(f"/proc/{pid}/comm", "r") as f:
                name = f.read().strip()

            uid = get_process_uid(pid)

            if not is_regular_user_uid(uid):
                continue

            if is_part_of_systemd_service(pid):
                LOGGER.debug("Skipping service-managed process: %s (%s)", name, pid)
                continue

            if is_blocked_process_name(name):
                LOGGER.debug("Skipping blocked process: %s (%s)", name, pid)
                continue

            LOGGER.debug("Matched regular-user process: %s (%s)", name, pid)
            pids.append(int(pid))
        except Exception:
            continue

    LOGGER.info("Found %d regular-user process(es) eligible for suspension", len(pids))
    return pids


def freeze_processes(pids):
    self_pid = os.getpid()
    parent_pid = os.getppid()

    for pid in pids:
        if pid in (self_pid, parent_pid):
            LOGGER.debug("We just tried to sigstop ourselves", pid)
            continue
        try:
            LOGGER.debug("Suspending pid %s", pid)
            os.kill(pid, signal.SIGSTOP)
        except Exception:
            LOGGER.exception("Failed to suspend pid %s", pid)
            pass


def resume_processes(pids):
    for pid in pids:
        LOGGER.debug("Resuming pid %s", pid)
        os.kill(pid, signal.SIGCONT)  # strict: don't suppress


def get_cpu_governor_paths():
    return sorted(glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_governor"))


def get_cpu_max_freq_paths():
    return sorted(glob("/sys/devices/system/cpu/cpu*/cpufreq/scaling_max_freq"))


def set_cpu_slow_state():
    LOGGER.info("Reapplying slow CPU settings")

    for path in get_cpu_governor_paths():
        try:
            write_sysfs_value(path, CPU_SLOW_GOVERNOR)
        except Exception:
            LOGGER.exception("Failed to set CPU governor: %s", path)

    for path in get_cpu_max_freq_paths():
        try:
            write_sysfs_value(path, CPU_SLOW_MAX_FREQ)
        except Exception:
            LOGGER.exception("Failed to cap CPU frequency: %s", path)


def restore_cpu_state():
    LOGGER.info("Restoring CPU power settings")

    for path in get_cpu_governor_paths():
        try:
            write_sysfs_value(path, CPU_RESTORE_GOVERNOR)
        except Exception:
            LOGGER.exception("Failed to restore CPU governor: %s", path)

    for path in get_cpu_max_freq_paths():
        try:
            write_sysfs_value(path, CPU_RESTORE_MAX_FREQ)
        except Exception:
            LOGGER.exception("Failed to restore CPU frequency cap: %s", path)


def get_cooling_devices():
    devices = []

    for device_path in sorted(glob("/sys/class/thermal/cooling_device*")):
        cur_state_path = os.path.join(device_path, "cur_state")
        type_path = os.path.join(device_path, "type")

        try:
            with open(cur_state_path, "r") as f:
                cur_state = int(f.read().strip())
        except Exception:
            LOGGER.debug("Skipping unreadable cooling device: %s", device_path, exc_info=True)
            continue

        device_type = "unknown"
        try:
            with open(type_path, "r") as f:
                device_type = f.read().strip() or "unknown"
        except Exception:
            LOGGER.debug("Unable to read cooling device type: %s", device_path, exc_info=True)

        devices.append(
            {
                "path": device_path,
                "type": device_type,
                "cur_state": cur_state,
            }
        )

    LOGGER.info("Captured %d cooling device(s)", len(devices))
    return devices


def save_cooling_state(devices):
    LOGGER.debug("Saving cooling state to %s", COOLING_STATE_FILE)
    with open(COOLING_STATE_FILE, "w") as f:
        json.dump(devices, f)


def load_cooling_state():
    try:
        LOGGER.debug("Loading cooling state from %s", COOLING_STATE_FILE)
        with open(COOLING_STATE_FILE, "r") as f:
            devices = json.load(f)
            LOGGER.info("Loaded %d cooling device(s) from saved state", len(devices))
            return devices
    except Exception:
        LOGGER.debug("No saved cooling state available", exc_info=True)
        return []


def turn_off_cooling_devices(devices):
    for device in devices:
        cur_state_path = os.path.join(device["path"], "cur_state")
        device_type = device.get("type", device["path"])

        try:
            LOGGER.info("Turning off cooling device %s", device_type)
            write_sysfs_value(cur_state_path, 0)
        except Exception:
            LOGGER.exception("Failed to turn off cooling device %s", device_type)


def restore_cooling_devices(devices):
    for device in devices:
        cur_state_path = os.path.join(device["path"], "cur_state")
        device_type = device.get("type", device["path"])
        cur_state = device.get("cur_state", 0)

        try:
            LOGGER.info("Restoring cooling device %s to state %s", device_type, cur_state)
            write_sysfs_value(cur_state_path, cur_state)
        except Exception:
            LOGGER.exception("Failed to restore cooling device %s", device_type)


def save_pids(pids):
    LOGGER.debug("Saving %d pid(s) to %s", len(pids), SLEEP_STATE_FILE)
    with open(SLEEP_STATE_FILE, "w") as f:
        for pid in pids:
            f.write(f"{pid}\n")


def load_pids():
    LOGGER.debug("Loading suspended pids from %s", SLEEP_STATE_FILE)
    with open(SLEEP_STATE_FILE, "r") as f:
        return [int(x.strip()) for x in f.readlines()]


def get_service_state():
    result = subprocess.run(
        ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout.strip() or result.stderr.strip()

    if result.returncode != 0:
        raise RuntimeError(f"systemctl list-units failed: {output or 'no output'}")

    services = []

    for line in output.splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        parts = stripped.split(None, 4)

        if len(parts) < 4:
            continue

        unit, load_state, active_state, sub_state = parts[:4]
        services.append(
            {
                "unit": unit,
                "load_state": load_state,
                "active_state": active_state,
                "sub_state": sub_state,
                "running": active_state == "active" and sub_state == "running",
            }
        )

    LOGGER.info("Captured %d service unit(s)", len(services))
    return services


def save_service_state(services):
    LOGGER.debug("Saving service state to %s", SERVICE_STATE_FILE)
    with open(SERVICE_STATE_FILE, "w") as f:
        json.dump(services, f)


def load_service_state():
    try:
        LOGGER.debug("Loading service state from %s", SERVICE_STATE_FILE)
        with open(SERVICE_STATE_FILE, "r") as f:
            services = json.load(f)
            LOGGER.info("Loaded %d service unit(s) from saved state", len(services))
            return services
    except Exception:
        LOGGER.debug("No saved service state available", exc_info=True)
        return []


def stop_systemd_services(services):
    for service in services:
        if not service.get("running"):
            continue

        unit = service["unit"]

        if is_protected_systemd_service(unit):
            LOGGER.debug("Keeping protected service running: %s", unit)
            continue

        LOGGER.info("Stopping service %s", unit)

        try:
            subprocess.run(["systemctl", "stop", unit], check=False)
        except Exception:
            LOGGER.exception("Failed to stop service %s", unit)


def restore_systemd_services(services):
    for service in services:
        if not service.get("running"):
            continue

        unit = service["unit"]

        if is_protected_systemd_service(unit):
            LOGGER.debug("Skipping protected service on restore: %s", unit)
            continue

        LOGGER.info("Starting service %s", unit)

        try:
            subprocess.run(["systemctl", "start", unit], check=False)
        except Exception:
            LOGGER.exception("Failed to start service %s", unit)


def get_display_state():
    env = get_wayland_env()
    result = subprocess.run(["wlr-randr"], capture_output=True, text=True, check=False, env=env)
    output = result.stdout.strip() or result.stderr.strip()

    if result.returncode != 0:
        raise RuntimeError(f"wlr-randr failed: {output or 'no output'}")

    if not output:
        raise RuntimeError("No display data returned by wlr-randr")

    LOGGER.debug("wlr-randr output:\n%s", output)

    displays = []
    current_output = None
    current_mode = None

    for line in output.splitlines():
        stripped = line.strip()

        if not stripped:
            continue

        if line[:1] not in {" ", "\t"}:
            if current_output is not None:
                displays.append({
                    "output": current_output,
                    "mode": current_mode or DEFAULT_DISPLAY_MODE,
                })
                LOGGER.debug(
                    "Parsed display state: output=%s mode=%s",
                    current_output,
                    current_mode or DEFAULT_DISPLAY_MODE,
                )

            current_output = stripped.split()[0]
            current_mode = None
            continue

        if "(current)" in stripped:
            match = re.match(r"^(\d+x\d+)", stripped)
            if match:
                current_mode = match.group(1)

    if current_output is not None:
        displays.append({
            "output": current_output,
            "mode": current_mode or DEFAULT_DISPLAY_MODE,
        })
        LOGGER.debug(
            "Parsed display state: output=%s mode=%s",
            current_output,
            current_mode or DEFAULT_DISPLAY_MODE,
        )

    if not displays:
        raise RuntimeError("No active displays found")

    LOGGER.info("Captured %d display(s)", len(displays))
    return displays


def save_display_state(displays):
    LOGGER.debug("Saving display state to %s", DISPLAY_STATE_FILE)
    with open(DISPLAY_STATE_FILE, "w") as f:
        json.dump(displays, f)


def load_display_state():
    try:
        LOGGER.debug("Loading display state from %s", DISPLAY_STATE_FILE)
        with open(DISPLAY_STATE_FILE, "r") as f:
            displays = json.load(f)
            LOGGER.info("Loaded %d display(s) from saved state", len(displays))
            return displays
    except Exception:
        LOGGER.debug("No saved display state available", exc_info=True)
        return []


def turn_off_displays(displays):
    env = get_wayland_env()
    for display in displays:
        LOGGER.info("Turning off display %s", display["output"])
        subprocess.run(["wlr-randr", "--output", display["output"], "--off"], check=False, env=env)


def turn_on_displays(displays):
    env = get_wayland_env()
    for display in displays:
        command = ["wlr-randr", "--output", display["output"], "--on", "--mode", display["mode"]]
        LOGGER.info("Restoring display %s at mode %s", display["output"], display["mode"])
        subprocess.run(command, check=False, env=env)


def restart_panel():
    LOGGER.info("Restarting wf-panel-pi to refresh the taskbar")
    subprocess.run(["pkill", "-9", "wf-panel-pi"], check=False)


def minimize_power():
    LOGGER.info("Preparing displays and CPU for fake sleep")
    displays = get_display_state()
    cooling_devices = get_cooling_devices()
    save_display_state(displays)
    save_cooling_state(cooling_devices)
    turn_off_displays(displays)
    set_cpu_slow_state()
    turn_off_cooling_devices(cooling_devices)


def restore_power():
    LOGGER.info("Restoring displays and CPU power settings")
    displays = load_display_state()
    if displays:
        turn_on_displays(displays)
        restart_panel()

    restore_cooling_devices(load_cooling_state())
    restore_cpu_state()


def force_screen_on_for_error():
    displays = load_display_state()

    if not displays:
        LOGGER.debug("No saved display state available to force on for error display")
        return

    LOGGER.info("Forcing displays on so wake failure can be shown")

    try:
        turn_on_displays(displays)
        restart_panel()
    except Exception:
        LOGGER.exception("Failed to force displays on for wake failure warning")


def wait_for_wake(cooling_devices):
    devices = []
    next_cpu_refresh = time.monotonic() + COOLING_REFRESH_SECONDS
    next_process_refresh = time.monotonic() + REASSERTION_REFRESH_SECONDS

    try:
        LOGGER.info("Waiting for wake event")
        for device_path in list_devices():
            try:
                devices.append(InputDevice(device_path))
                LOGGER.debug("Watching input device: %s", device_path)
            except Exception:
                continue

        if not devices:
            raise RuntimeError("No readable input devices found")

        while True:
            now = time.monotonic()
            next_refresh = min(next_cpu_refresh, next_process_refresh)
            timeout = max(0.0, next_refresh - now)

            readable_devices, _, _ = select.select(devices, [], [], timeout)

            for device in readable_devices:
                for event in device.read():
                    if event.type == ecodes.EV_KEY and event.code == ecodes.KEY_POWER and event.value == 1:
                        LOGGER.info("Wake event: power key")
                        return

                    if event.type == ecodes.EV_KEY and event.value == 1:
                        LOGGER.info("Wake event: key press")
                        return

                    if event.type == ecodes.EV_REL and event.value != 0:
                        LOGGER.info("Wake event: relative input")
                        return

            now = time.monotonic()

            if now >= next_cpu_refresh:
                set_cpu_slow_state()
                turn_off_cooling_devices(cooling_devices)
                next_cpu_refresh = now + COOLING_REFRESH_SECONDS

            if now >= next_process_refresh:
                refreshed_pids = get_target_pids()
                LOGGER.info("Re-freezing %d process(es)", len(refreshed_pids))
                freeze_processes(refreshed_pids)

                refreshed_services = get_service_state()
                LOGGER.info(
                    "Re-stopping %d service unit(s) except essentials",
                    sum(
                        1
                        for service in refreshed_services
                        if service.get("running") and not is_protected_systemd_service(service["unit"])
                    ),
                )
                stop_systemd_services(refreshed_services)
                next_process_refresh = now + REASSERTION_REFRESH_SECONDS
    finally:
        for device in devices:
            device.close()


def main(no_panic=False, lock_on_wake=False):
    LOGGER.info("Entering fake sleep (whitelist mode)")

    try:
        pids = get_target_pids()
        services = get_service_state()

        save_pids(pids)
        save_service_state(services)

        LOGGER.info(
            "Stopping %d service unit(s)",
            sum(1 for service in services if service.get("running") and not is_protected_systemd_service(service["unit"])),
        )
        stop_systemd_services(services)

        LOGGER.info("Suspending %d process(es)", len(pids))
        freeze_processes(pids)

        minimize_power()
        cooling_devices = load_cooling_state()
        wait_for_wake(cooling_devices)

        LOGGER.critical("Resume failure:\n%s", traceback.format_exc())
    except Exception:
        err = traceback.format_exc()
        restore_power()
        restore_systemd_services(load_service_state())
        show_error(f"Sleep failure:\n\n{err}")
        return

    try:
        restore_power()
        restore_systemd_services(load_service_state())
        pids = load_pids()
        LOGGER.info("Resuming %d process(es)", len(pids))
        resume_processes(pids)
        if lock_on_wake:
            try:
                lock_machine()
            except Exception:
                LOGGER.exception("Failed to lock session on wake; continuing with resume")
    except Exception:
        err = traceback.format_exc()
        LOGGER.critical("Resume failure:\n%s", err)

        time.sleep(5)
        if no_panic:
            show_error(
                "Wake failure:\n\n"
                f"{err}\n"
                "The system may be unstable. It is recommended to restart the system as soon as possible to prevent potential data loss and instability."
            )
        else:
            show_panic_warning(
                "Wake failure:\n\n"
                f"{err}\n"
                "The system will panic NOW!!",
            )
        return

    LOGGER.info("Resume complete")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--no-panic",
        action="store_true",
        help="Show wake failures instead of triggering a kernel panic.",
    )
    parser.add_argument(
        "--lock-on-wake",
        action="store_true",
        help="Lock the active session after wake before resuming suspended processes.",
    )
    args = parser.parse_args()
    main(no_panic=args.no_panic, lock_on_wake=args.lock_on_wake)
