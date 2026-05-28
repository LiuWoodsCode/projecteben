import importlib
import os
import pty
import select
import time

MODULE_NAMES = {
    "battery": "batt",
    "temperature": "temp",
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
    if not data and isinstance(result, (tuple, list)) and len(result) >= 4:
        return {
            "charge": as_int(result[0]),
            "charging": bool(result[1]),
            "cycles": as_int(result[2]),
            "voltage": as_float(result[3]),
        }

    battery = data.get("battery", data)
    return {
        "charge": as_int(battery.get("charge", battery.get("percent", battery.get("percentage")))),
        "charging": as_bool(battery.get("charging", battery.get("is_charging", battery.get("isCharging", False)))),
        "cycles": as_int(battery.get("cycles", battery.get("cycle_count", 0))),
        "voltage": as_float(battery.get("voltage", battery.get("vbat", 0.0))),
    }


def get_temperature_data():
    module = get_module(MODULE_NAMES["temperature"])
    result = call_any(
        module,
        ["get_temperature", "read_temperature", "temperature", "get_temps", "get", "read"],
    )

    data = normalize_mapping(result) or {}
    if not data and isinstance(result, (tuple, list)) and len(result) >= 2:
        return {
            "case": as_float(result[0]),
            "batt": as_float(result[1]),
        }

    temp = data.get("temp", data)
    return {
        "case": as_float(temp.get("case", temp.get("case_temp", temp.get("cpu", 0.0)))),
        "batt": as_float(temp.get("batt", temp.get("battery", temp.get("battery_temp", 0.0)))),
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
                temperature = get_temperature_data()
                print("Temp Comm OK!")
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
        return (f"CHARGE={int(b['charge'])}%\r\n"
                f"CHARGING={int(b['charging'])}\r\n"
                f"CYCLES={b['cycles']}\r\n"
                f"VOLTAGE={b['voltage']:.2f}V\r\n"
                "OK\r\n")

    # ---- TEMPERATURE ----
    if cmd == "TEMP?":
        t = get_temperature_data()
        return (f"CASE={t['case']:.1f}C\r\n"
                f"BATT={t['batt']:.1f}C\r\n"
                "OK\r\n")

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