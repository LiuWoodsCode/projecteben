#!/usr/bin/env python3

import glob
import signal
import time
from pathlib import Path
from typing import Optional

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gio, GLib, Gtk


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_NAME = "Violet Evergarden"

POLL_INTERVAL_MS = 1000

# >= 85 C: ordinary overheating notification.
OVERHEAT_THRESHOLD_MC = 85_000

# The ordinary warning becomes eligible to fire again after the machine
# cools below this temperature. This prevents notification chatter around
# exactly 85 C.
OVERHEAT_RESET_MC = 82_000

# > 85 C continuously for 2.5 minutes: critical shutdown.
CRITICAL_THRESHOLD_MC = 85_000
CRITICAL_DURATION_SECONDS = 150.0

# Time between critical window appearing and shutdown request.
SHUTDOWN_DELAY_MS = 10_000

# Don't call a missing cooling device a tachometer failure until it has
# remained absent for this long.
TACH_FAILURE_CONFIRM_SECONDS = 5.0

THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")


# ---------------------------------------------------------------------------
# Messages
#
# Keep all user-visible notification text here so it is easy to modify.
# ---------------------------------------------------------------------------

MESSAGES = {
    "tachometer_check_failed": {
        "title": "Tachometer check failed",
        "body": (
            "No cooling device was detected, indicating that the fan "
            "tachometer check failed. Check that the fan is connected "
            "and operating correctly."
        ),
    },

    "undervoltage": {
        "title": "Undervoltage detected",
        "body": (
            "The system is receiving insufficient power. Check the "
            "power supply, power cable, and connected peripherals."
        ),
    },

    "overheating": {
        "title": "System is overheating",
        "body": (
            "The system temperature has reached 85°C or higher. "
            "Reduce the workload or improve cooling."
        ),
    },
}


CRITICAL_MESSAGE = (
    "The system is getting too hot and needs to cool down. "
    "It will now shut down."
)


# ---------------------------------------------------------------------------
# Basic sysfs helpers
# ---------------------------------------------------------------------------

def read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        return None


def read_int(path: Path) -> Optional[int]:
    value = read_text(path)

    if value is None:
        return None

    try:
        return int(value)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Cooling device detection
# ---------------------------------------------------------------------------

def get_cooling_devices() -> list[Path]:
    """
    Return all cooling devices currently registered with the Linux thermal
    subsystem.

    On the target Raspberry Pi system, the firmware fan tachometer check
    determines whether the cooling fan device is exposed.
    """

    return [
        Path(path)
        for path in glob.glob("/sys/class/thermal/cooling_device*")
    ]


def cooling_device_exists() -> bool:
    return bool(get_cooling_devices())


# ---------------------------------------------------------------------------
# Raspberry Pi undervoltage detection
# ---------------------------------------------------------------------------

def find_raspberry_pi_undervoltage_alarm() -> Optional[Path]:
    """
    Locate the raspberrypi-hwmon undervoltage alarm.

    The kernel driver normally appears as an hwmon device named "rpi_volt"
    and exposes:

        in0_lcrit_alarm

    0 = normal
    1 = undervoltage alarm
    """

    for hwmon_path in glob.glob("/sys/class/hwmon/hwmon*"):
        hwmon = Path(hwmon_path)

        name = read_text(hwmon / "name")

        if name != "rpi_volt":
            continue

        alarm = hwmon / "in0_lcrit_alarm"

        if alarm.exists():
            return alarm

    return None


# ---------------------------------------------------------------------------
# Notification support
# ---------------------------------------------------------------------------

class NotificationClient:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(
            Gio.BusType.SESSION,
            None,
        )

    def send(self, message_id: str) -> None:
        message = MESSAGES[message_id]

        title = message["title"]
        body = message["body"]

        print(f"Notification: {title}")
        print(f"  {body}")

        parameters = GLib.Variant(
            "(susssasa{sv}i)",
            (
                APP_NAME,   # app_name
                0,          # replaces_id
                "",         # app_icon
                title,      # summary
                body,       # body
                [],         # actions
                {},         # hints
                -1,         # notification server chooses timeout
            ),
        )

        try:
            self.bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                parameters,
                GLib.VariantType("(u)"),
                Gio.DBusCallFlags.NONE,
                5_000,
                None,
            )

        except GLib.Error as exc:
            print(f"Unable to send notification: {exc}")


# ---------------------------------------------------------------------------
# Shutdown support
# ---------------------------------------------------------------------------

class ShutdownController:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(
            Gio.BusType.SYSTEM,
            None,
        )

    def power_off(self) -> bool:
        """
        Ask systemd-logind to power off the computer.

        interactive=False is intentional. A thermal emergency should not
        stop for an authentication dialog.
        """

        print("Requesting system power-off...")

        try:
            self.bus.call_sync(
                "org.freedesktop.login1",
                "/org/freedesktop/login1",
                "org.freedesktop.login1.Manager",
                "PowerOff",
                GLib.Variant(
                    "(b)",
                    (False,),
                ),
                None,
                Gio.DBusCallFlags.NONE,
                5_000,
                None,
            )

            return True

        except GLib.Error as exc:
            print(f"Unable to power off system: {exc}")
            return False


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class HardwareMonitor:
    def __init__(self, loop: GLib.MainLoop) -> None:
        self.loop = loop

        self.notifications = NotificationClient()
        self.shutdown = ShutdownController()

        # ---------------------------------------------------------------
        # Tachometer state
        # ---------------------------------------------------------------

        self.no_cooling_device_since: Optional[float] = None
        self.tach_failure_active = False

        # ---------------------------------------------------------------
        # Undervoltage state
        # ---------------------------------------------------------------

        self.undervoltage_alarm_path: Optional[Path] = None
        self.undervoltage_active = False

        self.last_undervoltage_path_search = 0.0
        self.warned_missing_undervoltage_sensor = False

        # ---------------------------------------------------------------
        # Temperature state
        # ---------------------------------------------------------------

        self.overheat_notification_active = False
        self.critical_temperature_since: Optional[float] = None

        # Once the critical action fires, it is irreversible from this
        # daemon's point of view.
        self.critical_triggered = False

        self.critical_window: Optional[Gtk.Window] = None

    # ------------------------------------------------------------------
    # Cooling / tachometer
    # ------------------------------------------------------------------

    def check_tachometer(self, now: float) -> None:
        if cooling_device_exists():
            self.no_cooling_device_since = None

            if self.tach_failure_active:
                print("Cooling device detected again.")

            self.tach_failure_active = False
            return

        if self.no_cooling_device_since is None:
            self.no_cooling_device_since = now
            return

        missing_duration = now - self.no_cooling_device_since

        if (
            missing_duration >= TACH_FAILURE_CONFIRM_SECONDS
            and not self.tach_failure_active
        ):
            self.tach_failure_active = True

            self.notifications.send(
                "tachometer_check_failed"
            )

    # ------------------------------------------------------------------
    # Undervoltage
    # ------------------------------------------------------------------

    def refresh_undervoltage_alarm_path(self, now: float) -> None:
        # Search again occasionally in case the hwmon driver appeared
        # after this daemon started.
        if (
            self.undervoltage_alarm_path is not None
            and self.undervoltage_alarm_path.exists()
        ):
            return

        if now - self.last_undervoltage_path_search < 30.0:
            return

        self.last_undervoltage_path_search = now

        self.undervoltage_alarm_path = (
            find_raspberry_pi_undervoltage_alarm()
        )

        if self.undervoltage_alarm_path is not None:
            print(
                "Raspberry Pi undervoltage alarm: "
                f"{self.undervoltage_alarm_path}"
            )

            self.warned_missing_undervoltage_sensor = False

        elif not self.warned_missing_undervoltage_sensor:
            print(
                "raspberrypi-hwmon undervoltage alarm "
                "was not found."
            )

            self.warned_missing_undervoltage_sensor = True

    def check_undervoltage(self, now: float) -> None:
        self.refresh_undervoltage_alarm_path(now)

        path = self.undervoltage_alarm_path

        if path is None:
            return

        alarm = read_int(path)

        if alarm is None:
            # The hwmon path may have disappeared/re-enumerated.
            self.undervoltage_alarm_path = None
            return

        undervoltage = alarm != 0

        if undervoltage and not self.undervoltage_active:
            self.undervoltage_active = True

            self.notifications.send(
                "undervoltage"
            )

        elif not undervoltage:
            if self.undervoltage_active:
                print("Voltage returned to normal.")

            self.undervoltage_active = False

    # ------------------------------------------------------------------
    # Temperature
    # ------------------------------------------------------------------

    def check_temperature(self, now: float) -> None:
        temperature_mc = read_int(THERMAL_ZONE)

        if temperature_mc is None:
            print(
                f"Unable to read temperature from {THERMAL_ZONE}"
            )
            return

        temperature_c = temperature_mc / 1000.0

        # ---------------------------------------------------------------
        # Non-critical notification
        # ---------------------------------------------------------------

        if temperature_mc >= OVERHEAT_THRESHOLD_MC:
            if not self.overheat_notification_active:
                self.overheat_notification_active = True

                print(
                    f"High temperature detected: "
                    f"{temperature_c:.1f}°C"
                )

                self.notifications.send(
                    "overheating"
                )

        elif temperature_mc <= OVERHEAT_RESET_MC:
            if self.overheat_notification_active:
                print(
                    f"Temperature returned below warning range: "
                    f"{temperature_c:.1f}°C"
                )

            self.overheat_notification_active = False

        # ---------------------------------------------------------------
        # Critical condition
        #
        # Requirement is specifically OVER 85 C continuously.
        # At exactly 85 C, only the ordinary warning applies.
        # ---------------------------------------------------------------

        if temperature_mc > CRITICAL_THRESHOLD_MC:
            if self.critical_temperature_since is None:
                self.critical_temperature_since = now

                print(
                    f"Temperature is above critical threshold: "
                    f"{temperature_c:.1f}°C"
                )

            critical_duration = (
                now - self.critical_temperature_since
            )

            if (
                critical_duration >= CRITICAL_DURATION_SECONDS
                and not self.critical_triggered
            ):
                self.trigger_critical_shutdown()

        else:
            if self.critical_temperature_since is not None:
                print(
                    "Critical-temperature timer reset."
                )

            self.critical_temperature_since = None

    # ------------------------------------------------------------------
    # Critical UI
    # ------------------------------------------------------------------

    def trigger_critical_shutdown(self) -> None:
        self.critical_triggered = True

        print(
            "Temperature has remained above 85°C for "
            "2.5 minutes."
        )

        window = Gtk.Window()

        # No title bar, controls, close button, or resize handles.
        window.set_decorated(False)
        window.set_deletable(False)
        window.set_resizable(False)
        window.set_modal(True)
        window.set_hide_on_close(False)

        # A reasonable fixed warning-dialog size.
        window.set_default_size(
            620,
            160,
        )

        # Ignore GTK/window-manager close requests such as Alt+F4.
        window.connect(
            "close-request",
            self.on_critical_window_close_request,
        )

        label = Gtk.Label(
            label=CRITICAL_MESSAGE
        )

        label.set_wrap(True)
        label.set_justify(
            Gtk.Justification.CENTER
        )

        label.set_xalign(0.5)
        label.set_yalign(0.5)

        label.set_selectable(False)

        label.set_margin_top(32)
        label.set_margin_bottom(32)
        label.set_margin_start(40)
        label.set_margin_end(40)

        window.set_child(label)

        self.critical_window = window

        # On Wayland, placement is ultimately controlled by the compositor.
        # Presenting an undecorated fixed-size modal window normally causes
        # it to be placed prominently/centrally according to compositor
        # policy.
        window.present()

        print(
            "Critical temperature warning displayed. "
            "Shutdown in 10 seconds."
        )

        GLib.timeout_add(
            SHUTDOWN_DELAY_MS,
            self.perform_shutdown,
        )

    @staticmethod
    def on_critical_window_close_request(
        _window: Gtk.Window,
    ) -> bool:
        # True stops GTK's normal close processing.
        return True

    def perform_shutdown(self) -> bool:
        if self.shutdown.power_off():
            # No additional timer.
            return False

        # If logind rejected the request, keep the warning window on-screen
        # and retry. A transient D-Bus failure should not defeat thermal
        # protection.
        print(
            "Shutdown request failed; retrying in 5 seconds."
        )

        GLib.timeout_add(
            5_000,
            self.perform_shutdown,
        )

        return False

    # ------------------------------------------------------------------
    # Main polling cycle
    # ------------------------------------------------------------------

    def poll(self) -> bool:
        now = time.monotonic()

        self.check_tachometer(now)
        self.check_undervoltage(now)
        self.check_temperature(now)

        # Returning True keeps the GLib timer alive.
        return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    GLib.set_application_name(APP_NAME)
    GLib.set_prgname("aaron-swartz-hardware-monitor")

    Gtk.init()

    loop = GLib.MainLoop()

    monitor = HardwareMonitor(loop)

    def stop_service(
        _signum: int,
        _frame,
    ) -> None:
        GLib.idle_add(loop.quit)

    signal.signal(
        signal.SIGTERM,
        stop_service,
    )

    signal.signal(
        signal.SIGINT,
        stop_service,
    )

    print(f"{APP_NAME} hardware monitor starting.")

    print(
        "Temperature source: "
        f"{THERMAL_ZONE}"
    )

    # First pass immediately.
    monitor.poll()

    # Subsequent checks once per second.
    GLib.timeout_add(
        POLL_INTERVAL_MS,
        monitor.poll,
    )

    loop.run()


if __name__ == "__main__":
    main()