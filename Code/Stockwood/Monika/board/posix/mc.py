import importlib
import os
import pty
import select
import time

MODULE_NAMES = {
    "battery": "batt",
    "backlight": "backlight",
    "nvram": "nvram",
}

MODULE_CACHE = {}

# --- Helpers ---

def get_module(name):
    if name not in MODULE_CACHE:
        MODULE_CACHE[name] = importlib.import_module(name)
    return MODULE_CACHE[name]


def call_any(obj, names, arg_options=None):
    arg_options = arg_options or [tuple()]
    last_error = None

    for name in names:
        if not hasattr(obj, name):
            continue

        candidate = getattr(obj, name)
        if not callable(candidate):
            if tuple() in arg_options:
                return candidate
            continue

        for args in arg_options:
            try:
                return candidate(*args)
            except TypeError as exc:
                last_error = exc

    if last_error is not None:
        raise last_error
    raise AttributeError(f"No supported call found on {obj!r}")


def as_int(value):
    if isinstance(value, bool):
        return int(value)

    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if text.endswith("%"):
        text = text[:-1]
    return int(float(text))


def as_float(value):
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if text.endswith("V") or text.endswith("C"):
        text = text[:-1]
    return float(text)


def as_bool(value):
    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "on", "charging"}


def normalize_mapping(value):
    if isinstance(value, dict):
        return value

    if hasattr(value, "_asdict"):
        return value._asdict()

    if hasattr(value, "__dict__"):
        return vars(value)

    return None


def get_battery_data():
    module = get_module(MODULE_NAMES["battery"])
    result = call_any(
        module,
        ["get_battery", "read_battery", "battery", "get_status", "status", "get", "read"],
    )

    data = normalize_mapping(result) or {}

    # Legacy tuple/list format:
    # (charge, charging, cycles, voltage)
    if not data and isinstance(result, (tuple, list)) and len(result) >= 4:
        charging = bool(result[1])

        return {
            "charge": as_int(result[0]),
            "status": "Charging" if charging else "Discharging",
            "cycles": as_int(result[2]),
            "voltage": as_float(result[3]),

            "power": 0.0,
            "current": 0.0,
            "temp": 0.0,

            "energy_now": 0.0,
            "energy_full": 0.0,
            "energy_full_design": 0.0,

            "present": True,
            "manufacturer": "",
            "model": "",
            "serial": "",
            "technology": "Unknown",

            "voltage_min_design": 0.0,
            "voltage_max_design": 0.0,

            "scope": "System",
            "type": "Battery",
        }

    battery = data.get("battery", data)

    # Prefer an explicit status, but remain compatible with older battery
    # modules that only expose charging/is_charging.
    status = battery.get("status", battery.get("state"))

    if status is None:
        charging = as_bool(
            battery.get(
                "charging",
                battery.get(
                    "is_charging",
                    battery.get("isCharging", False),
                ),
            )
        )
        status = "Charging" if charging else "Discharging"
    else:
        status = str(status)

    return {
        # Dynamic state
        "charge": as_int(
            battery.get(
                "charge",
                battery.get(
                    "percent",
                    battery.get("percentage", 0),
                ),
            )
        ),

        "status": status,

        "voltage": as_float(
            battery.get(
                "voltage",
                battery.get(
                    "voltage_now",
                    battery.get("vbat", 0.0),
                ),
            )
        ),

        "power": as_float(
            battery.get(
                "power",
                battery.get("power_now", 0.0),
            )
        ),

        "current": as_float(
            battery.get(
                "current",
                battery.get("current_now", 0.0),
            )
        ),

        "temp": as_float(
            battery.get(
                "temp",
                battery.get(
                    "temperature",
                    battery.get("battery_temp", 0.0),
                ),
            )
        ),

        # Capacity / wear
        "energy_now": as_float(
            battery.get(
                "energy_now",
                battery.get("energy", 0.0),
            )
        ),

        "energy_full": as_float(
            battery.get(
                "energy_full",
                battery.get("full_energy", 0.0),
            )
        ),

        "energy_full_design": as_float(
            battery.get(
                "energy_full_design",
                battery.get(
                    "design_energy",
                    battery.get("energy_design", 0.0),
                ),
            )
        ),

        "cycles": as_int(
            battery.get(
                "cycles",
                battery.get("cycle_count", 0),
            )
        ),

        # Hardware identity
        "present": as_bool(
            battery.get("present", True)
        ),

        "manufacturer": str(
            battery.get(
                "manufacturer",
                battery.get("vendor", ""),
            )
        ),

        "model": str(
            battery.get(
                "model",
                battery.get("model_name", ""),
            )
        ),

        "serial": str(
            battery.get(
                "serial",
                battery.get("serial_number", ""),
            )
        ),

        "technology": str(
            battery.get(
                "technology",
                battery.get("chemistry", "Unknown"),
            )
        ),

        # Design characteristics
        "voltage_min_design": as_float(
            battery.get(
                "voltage_min_design",
                battery.get("design_voltage_min", 0.0),
            )
        ),

        "voltage_max_design": as_float(
            battery.get(
                "voltage_max_design",
                battery.get("design_voltage_max", 0.0),
            )
        ),

        # Classification
        "scope": str(
            battery.get("scope", "System")
        ),

        "type": str(
            battery.get("type", "Battery")
        ),
    }

def get_brightness():
    module = get_module(MODULE_NAMES["backlight"])
    result = call_any(
        module,
        ["get_brightness", "read_brightness", "brightness", "get", "read"],
    )
    return as_int(result)


def set_brightness(value):
    module = get_module(MODULE_NAMES["backlight"])
    call_any(
        module,
        ["set_brightness", "write_brightness", "brightness", "set", "write"],
        [(value,),],
    )


def nvram_read(addr, length):
    module = get_module(MODULE_NAMES["nvram"])
    result = call_any(
        module,
        ["read_nvram", "get_nvram", "read", "get"],
        [
            (addr, length),
            (addr, [length]),
        ],
    )

    if isinstance(result, (bytes, bytearray, list, tuple)):
        return bytes(result)

    if isinstance(result, str):
        text = result.strip().replace(" ", "")
        if len(text) % 2 == 0:
            try:
                return bytes.fromhex(text)
            except ValueError:
                pass

    raise TypeError("nvram read returned unsupported data")


def nvram_write(addr, data):
    module = get_module(MODULE_NAMES["nvram"])
    payload = bytes(data)
    call_any(
        module,
        ["write_nvram", "set_nvram", "write", "set"],
        [
            (addr, payload),
            (addr, list(payload)),
            (addr, *payload),
        ],
    )


def ensure_startup_ready():
    while True:
        try:
            print("Checking system components, this may take some time")
            for i in range(1,3):
                print("-" * 20)
                print(f"Check Pass {i}")
                print("-" * 20)
                battery = get_battery_data()
                print("Batt Comm OK!")
                brightness = get_brightness()
                set_brightness(brightness)
                brightness_check = get_brightness()

                if brightness_check != brightness:
                    raise RuntimeError("brightness verify failed")
                
                print("Backlight Comm OK!")

                print("NVRAM Comm Checks Start")
                print("NVRAM Read Start")
                nvram_block = nvram_read(0, 4)
                print("NVRAM Read Success!")
                print("NVRAM Write Start")
                nvram_write(0, nvram_block)
                print("NVRAM Write Success!")
                nvram_check = nvram_read(0, 4)

                if nvram_check != nvram_block:
                    raise RuntimeError("nvram verify failed")
                
                print("NVRAM Comm Check Success!")

            print("Ready For Commands!")
            return

        except Exception as exc:
            # this is meant to cause a bootloop
            # as this will cause Stockwood to timeout and act like Eben connected to a monitor
            print(f"Startup probe failed: {exc}")
            print("Will try again in 1 sec")
            time.sleep(1)

def resp_ok(extra=""):
    return f"{extra}OK\r\n"

def resp_error(msg=""):
    return f"ERROR{':' + msg if msg else ''}\r\n"

# --- Command Handler ---

def handle_command(cmd):
    cmd = cmd.strip().upper()

    # ---- BASIC ----
    if cmd == "AT":
        return resp_ok()

    if cmd == "PING":
        return resp_ok("PONG\r\n")
    
    if cmd == "VER?":
        return resp_ok("VER=0.1\r\n")

    # ---- BATTERY ----
    if cmd == "BATT?":
        b = get_battery_data()

        return (
            f"CHARGE={int(b['charge'])}%\r\n"
            f"STATUS={b['status']}\r\n"
            f"VOLTAGE={b['voltage']:.2f}V\r\n"
            f"POWER={b['power']:.2f}W\r\n"
            f"CURRENT={b['current']:.2f}A\r\n"
            f"TEMP={b['temp']:.1f}C\r\n"

            f"ENERGY_NOW={b['energy_now']:.2f}Wh\r\n"
            f"ENERGY_FULL={b['energy_full']:.2f}Wh\r\n"
            f"ENERGY_FULL_DESIGN={b['energy_full_design']:.2f}Wh\r\n"
            f"CYCLES={b['cycles']}\r\n"

            f"PRESENT={int(b['present'])}\r\n"
            f"MANUFACTURER={b['manufacturer']}\r\n"
            f"MODEL={b['model']}\r\n"
            f"SERIAL={b['serial']}\r\n"
            f"TECHNOLOGY={b['technology']}\r\n"

            f"VOLTAGE_MIN_DESIGN={b['voltage_min_design']:.2f}V\r\n"
            f"VOLTAGE_MAX_DESIGN={b['voltage_max_design']:.2f}V\r\n"

            f"SCOPE={b['scope']}\r\n"
            f"TYPE={b['type']}\r\n"

            "OK\r\n"
        )

    # ---- BRIGHTNESS ----
    if cmd == "BRIGHT?":
        return f"BRIGHTNESS={get_brightness()}\r\nOK\r\n"

    if cmd.startswith("BRIGHT="):
        try:
            val = int(cmd.split("=")[1])
            if 0 <= val <= 100:
                set_brightness(val)
                return resp_ok()
            return resp_error("RANGE")
        except:
            return resp_error()

    # ---- NVRAM READ ----
    # NVRAMR=addr,len
    if cmd.startswith("NVRAMR="):
        try:
            args = cmd.split("=")[1]
            addr, length = map(int, args.split(","))
            if addr < 0 or addr + length > 4096:
                return resp_error("RANGE")

            data = nvram_read(addr, length)
            hex_str = " ".join(f"{b:02X}" for b in data)
            return f"{hex_str}\r\nOK\r\n"

        except:
            return resp_error()

    # ---- NVRAM WRITE ----
    # NVRAMW=addr,AA,BB,CC
    if cmd.startswith("NVRAMW="):
        try:
            parts = cmd.split("=")[1].split(",")
            addr = int(parts[0])
            bytes_data = [int(x, 16) for x in parts[1:]]

            if addr < 0 or addr + len(bytes_data) > 4096:
                return resp_error("RANGE")

            nvram_write(addr, bytes_data)

            return resp_ok()

        except:
            return resp_error()
        
    if cmd.startswith("RESET"):
        try:
            # In the future the reset command will perform a full reset of the system
            # For right now, just run the self tests
            return ensure_startup_ready() 
        except:
            return resp_error()

    # ---- UNKNOWN ----
    return resp_error()

# --- Main Loop ---

def main():
    print("Monika MCU for Project Stockwood")
    print("This software is licensed under the GNU Public License, version 3.0.")
    print("\"Just Monika\" -Monika, 2017, colorized")
    print("*" * 16)
    ensure_startup_ready()

    master_fd, slave_fd = pty.openpty()
    slave_name = os.ttyname(slave_fd)

    print(f"Connect to {slave_name}")

    buffer = b""

    while True:
        rlist, _, _ = select.select([master_fd], [], [], 0.1)

        if master_fd in rlist:
            data = os.read(master_fd, 1024)
            if not data:
                continue

            buffer += data

            while b"\r" in buffer:
                line, buffer = buffer.split(b"\r", 1)
                cmd = line.decode(errors="ignore")

                print(f"[RX] {cmd}")
                response = handle_command(cmd)
                print(f"[TX] {response.strip()}")

                os.write(master_fd, response.encode())

if __name__ == "__main__":
    main()