#!/usr/bin/env python3
import asyncio
import time

from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, signal
from dbus_next.constants import BusType, PropertyAccess
from dbus_next import Variant


UPOWER_BUS_NAME = "org.freedesktop.UPower"

UPOWER_PATH = "/org/freedesktop/UPower"
DISPLAY_PATH = "/org/freedesktop/UPower/devices/DisplayDevice"
BATTERY_PATH = "/org/freedesktop/UPower/devices/battery_BAT0"
AC_PATH = "/org/freedesktop/UPower/devices/line_power_AC"


# UPower device Type enum
DEVICE_TYPE_UNKNOWN = 0
DEVICE_TYPE_LINE_POWER = 1
DEVICE_TYPE_BATTERY = 2

# UPower device State enum
STATE_UNKNOWN = 0
STATE_CHARGING = 1
STATE_DISCHARGING = 2
STATE_EMPTY = 3
STATE_FULLY_CHARGED = 4
STATE_PENDING_CHARGE = 5
STATE_PENDING_DISCHARGE = 6

# UPower device Technology enum
TECHNOLOGY_UNKNOWN = 0
TECHNOLOGY_LITHIUM_ION = 1

# WarningLevel enum
WARNING_LEVEL_NONE = 1

# BatteryLevel enum
BATTERY_LEVEL_NONE = 1


class UPowerManager(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.UPower")

    @method()
    def EnumerateDevices(self) -> "ao":
        return [BATTERY_PATH, AC_PATH]

    @method()
    def GetDisplayDevice(self) -> "o":
        return DISPLAY_PATH

    @method()
    def GetCriticalAction(self) -> "s":
        return "PowerOff"

    @signal()
    def DeviceAdded(self, device: "o") -> None:
        pass

    @signal()
    def DeviceRemoved(self, device: "o") -> None:
        pass

    @dbus_property(access=PropertyAccess.READ)
    def DaemonVersion(self) -> "s":
        return "1.90.0-fake"

    @dbus_property(access=PropertyAccess.READ)
    def OnBattery(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def LidIsClosed(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def LidIsPresent(self) -> "b":
        return True


class BatteryDevice(ServiceInterface):
    def __init__(self, native_path="BAT0", is_display=False):
        super().__init__("org.freedesktop.UPower.Device")
        self.native_path = native_path
        self.is_display = is_display
        self.update_time = int(time.time())

    @method()
    def Refresh(self) -> None:
        self.update_time = int(time.time())

    @signal()
    def Changed(self) -> None:
        pass

    @dbus_property(access=PropertyAccess.READ)
    def NativePath(self) -> "s":
        return self.native_path

    @dbus_property(access=PropertyAccess.READ)
    def Vendor(self) -> "s":
        return "FakeBatteryCo"

    @dbus_property(access=PropertyAccess.READ)
    def Model(self) -> "s":
        return "BAT0"

    @dbus_property(access=PropertyAccess.READ)
    def Serial(self) -> "s":
        return "00000001"

    @dbus_property(access=PropertyAccess.READ)
    def UpdateTime(self) -> "t":
        return self.update_time

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "u":
        return DEVICE_TYPE_BATTERY

    @dbus_property(access=PropertyAccess.READ)
    def PowerSupply(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def HasHistory(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasStatistics(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Online(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Energy(self) -> "d":
        return 50.0

    @dbus_property(access=PropertyAccess.READ)
    def EnergyEmpty(self) -> "d":
        return 0.0

    @dbus_property(access=PropertyAccess.READ)
    def EnergyFull(self) -> "d":
        return 50.0

    @dbus_property(access=PropertyAccess.READ)
    def EnergyFullDesign(self) -> "d":
        return 50.0

    @dbus_property(access=PropertyAccess.READ)
    def EnergyRate(self) -> "d":
        return 0.0

    @dbus_property(access=PropertyAccess.READ)
    def Voltage(self) -> "d":
        return 12.6

    @dbus_property(access=PropertyAccess.READ)
    def Luminosity(self) -> "d":
        return 0.0

    @dbus_property(access=PropertyAccess.READ)
    def TimeToEmpty(self) -> "x":
        return 900

    @dbus_property(access=PropertyAccess.READ)
    def TimeToFull(self) -> "x":
        return 0

    @dbus_property(access=PropertyAccess.READ)
    def Percentage(self) -> "d":
        return 100.0

    @dbus_property(access=PropertyAccess.READ)
    def Temperature(self) -> "d":
        return 25.0

    @dbus_property(access=PropertyAccess.READ)
    def IsPresent(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def State(self) -> "u":
        return STATE_FULLY_CHARGED

    @dbus_property(access=PropertyAccess.READ)
    def IsRechargeable(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def Capacity(self) -> "d":
        return 100.0

    @dbus_property(access=PropertyAccess.READ)
    def Technology(self) -> "u":
        return TECHNOLOGY_LITHIUM_ION

    @dbus_property(access=PropertyAccess.READ)
    def WarningLevel(self) -> "u":
        return WARNING_LEVEL_NONE

    @dbus_property(access=PropertyAccess.READ)
    def BatteryLevel(self) -> "u":
        return BATTERY_LEVEL_NONE

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return "battery-full-charged-symbolic"


class LinePowerDevice(ServiceInterface):
    def __init__(self):
        super().__init__("org.freedesktop.UPower.Device")
        self.update_time = int(time.time())

    @method()
    def Refresh(self) -> None:
        self.update_time = int(time.time())

    @signal()
    def Changed(self) -> None:
        pass

    @dbus_property(access=PropertyAccess.READ)
    def NativePath(self) -> "s":
        return "AC"

    @dbus_property(access=PropertyAccess.READ)
    def Vendor(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def Model(self) -> "s":
        return "AC Adapter"

    @dbus_property(access=PropertyAccess.READ)
    def Serial(self) -> "s":
        return ""

    @dbus_property(access=PropertyAccess.READ)
    def UpdateTime(self) -> "t":
        return self.update_time

    @dbus_property(access=PropertyAccess.READ)
    def Type(self) -> "u":
        return DEVICE_TYPE_LINE_POWER

    @dbus_property(access=PropertyAccess.READ)
    def PowerSupply(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def HasHistory(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def HasStatistics(self) -> "b":
        return False

    @dbus_property(access=PropertyAccess.READ)
    def Online(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def Percentage(self) -> "d":
        return 0.0

    @dbus_property(access=PropertyAccess.READ)
    def IsPresent(self) -> "b":
        return True

    @dbus_property(access=PropertyAccess.READ)
    def State(self) -> "u":
        return STATE_UNKNOWN

    @dbus_property(access=PropertyAccess.READ)
    def WarningLevel(self) -> "u":
        return WARNING_LEVEL_NONE

    @dbus_property(access=PropertyAccess.READ)
    def BatteryLevel(self) -> "u":
        return BATTERY_LEVEL_NONE

    @dbus_property(access=PropertyAccess.READ)
    def IconName(self) -> "s":
        return "ac-adapter-symbolic"


async def main():
    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    await bus.request_name(UPOWER_BUS_NAME)

    manager = UPowerManager()
    display = BatteryDevice(native_path="DisplayDevice", is_display=True)
    battery = BatteryDevice(native_path="BAT0")
    ac = LinePowerDevice()

    bus.export(UPOWER_PATH, manager)
    bus.export(DISPLAY_PATH, display)
    bus.export(BATTERY_PATH, battery)
    bus.export(AC_PATH, ac)

    print("Fake UPower service running.")
    print("Battery: 100%, fully charged")
    print("AC adapter: online")

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())