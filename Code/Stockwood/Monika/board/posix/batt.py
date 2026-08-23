"""Battery emulator for Monika."""

from __future__ import annotations

_STATE = {
    "charge": 76,                  # % — real HW: state of charge, exposed as capacity
    "status": "Charging",          # real HW: Charging, Discharging, Full, Not charging, Unknown
    "voltage": 4.01,               # V — real HW: instantaneous battery voltage, voltage_now
    "power": 4.7,                  # W — real HW: instantaneous charge/discharge rate, power_now
    "current": 1.17,               # A — real HW: signed current; useful because UPower checks < 0
    "temp": 23.2,                  # °C — real HW: battery/gauge temperature

    # Capacity / wear (battery health)
    "energy_now": 12.3,            # Wh — real HW: presently stored energy
    "energy_full": 16.2,           # Wh — real HW: learned present full-charge capacity
    "energy_full_design": 17.0,    # Wh — real HW: factory design capacity
    "cycles": 142,                 # real HW: accumulated equivalent full charge cycles

    # Hardware identity
    "present": True,               # real HW: pack physically detected
    "manufacturer": "SMP",         # real HW: manufacturer reported by battery/gauge
    "model": "EC-EMU-BATTERY",     # real HW: pack model identifier
    "serial": "00000001",          # real HW: pack serial number
    "technology": "Li-ion",        # real HW: battery chemistry

    # Design characteristics, specifically voltage for some reason? 
    "voltage_min_design": 3.0,     # V — real HW: manufacturer's minimum design voltage
    "voltage_max_design": 4.2,     # V — real HW: manufacturer's maximum design voltage

    # Device classification, we need these for upower to know this is the "laptop"'s internal battery
    "scope": "System",             # real HW: System for the laptop's main battery
    "type": "Battery",             # real HW: identifies this power_supply as a battery
}
def get_battery():
    return dict(_STATE)


def read_battery():
    return get_battery()


def get_status():
    return get_battery()


def status():
    return get_battery()


def get():
    return get_battery()


def read():
    return get_battery()
