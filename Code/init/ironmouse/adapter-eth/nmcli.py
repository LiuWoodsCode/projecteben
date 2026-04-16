"""Helpers for controlling NetworkManager through nmcli."""

from __future__ import annotations

import json
import platform
import re
import shlex
import socket
import subprocess
from dataclasses import dataclass
from typing import Any


class NmcliError(RuntimeError):
    """Raised when nmcli returns an error."""


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _merge_record_value(record: dict[str, Any], key: str, value: str) -> None:
    if key not in record:
        record[key] = value
        return
    existing = record[key]
    if isinstance(existing, list):
        existing.append(value)
        return
    record[key] = [existing, value]


def parse_multiline_records(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] = {}
    first_key: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            if current:
                records.append(current)
                current = {}
                first_key = None
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = _normalize_key(key)

        # Some nmcli multiline list commands don't emit blank lines between
        # records. In those cases, the first selected field repeats for each
        # row and can be used as a record boundary.
        if current and first_key and normalized_key == first_key:
            records.append(current)
            current = {}
            first_key = None

        if not current:
            first_key = normalized_key

        _merge_record_value(current, normalized_key, value.strip())
    if current:
        records.append(current)
    return records


@dataclass(slots=True)
class NetworkManagerService:
    wifi_interface: str = "wlan0"
    usb_interface: str = "usb0"
    usb_profile: str = "USB Gadget LAN"

    def _run(self, *args: str, multiline: bool = True) -> str:
        command = ["nmcli"]
        if multiline:
            command.extend(["--mode", "multiline"])
        command.extend(args)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            error = completed.stderr.strip() or completed.stdout.strip() or "nmcli failed"
            quoted = " ".join(shlex.quote(part) for part in command)
            raise NmcliError(f"{error} ({quoted})")
        return completed.stdout

    def _get_connection_by_identifier(self, identifier: str) -> dict[str, Any]:
        identifier = identifier.strip()
        for connection in self.list_connections():
            if identifier in {
                str(connection.get("uuid", "")),
                str(connection.get("name", "")),
            }:
                return connection
        raise NmcliError(f"Connection '{identifier}' was not found")

    def _connection_target(self, identifier: str) -> list[str]:
        connection = self._get_connection_by_identifier(identifier)
        return ["uuid", str(connection["uuid"])]

    def _show_connection(self, identifier: str) -> dict[str, Any]:
        target = self._connection_target(identifier)
        output = self._run(
            "--fields",
            "connection.id,connection.uuid,connection.type,connection.autoconnect,"
            "connection.autoconnect-priority,802-11-wireless.ssid,802-11-wireless.hidden,"
            "802-11-wireless-security.key-mgmt,GENERAL.STATE,GENERAL.DEVICES,IPV4.METHOD,"
            "IPV6.METHOD",
            "connection",
            "show",
            *target,
        )
        records = parse_multiline_records(output)
        if not records:
            raise NmcliError(f"Connection '{identifier}' did not return any data")
        details = records[0]
        ssid = details.get("802_11_wireless_ssid")
        if isinstance(ssid, str):
            details["ssid"] = ssid
        return details

    def general_status(self) -> dict[str, Any]:
        output = self._run(
            "--fields",
            "STATE,CONNECTIVITY,WIFI-HW,WIFI,WWAN-HW,WWAN",
            "general",
            "status",
        )
        records = parse_multiline_records(output)
        return records[0] if records else {}

    def device_status(self, interface: str) -> dict[str, Any]:
        try:
            output = self._run(
                "--fields",
                "GENERAL.DEVICE,GENERAL.TYPE,GENERAL.STATE,GENERAL.CONNECTION,"
                "WIRED-PROPERTIES.CARRIER,IP4.ADDRESS,IP4.GATEWAY,IP4.DNS,IP6.ADDRESS",
                "device",
                "show",
                interface,
            )
        except NmcliError as error:
            return {"interface": interface, "error": str(error)}
        records = parse_multiline_records(output)
        if not records:
            return {"interface": interface}
        device = records[0]
        device["interface"] = interface
        return device

    def list_connections(self) -> list[dict[str, Any]]:
        output = self._run(
            "--fields",
            "NAME,UUID,TYPE,DEVICE,AUTOCONNECT,TIMESTAMP-REAL",
            "connection",
            "show",
        )
        return parse_multiline_records(output)

    def list_wifi_connections(self) -> list[dict[str, Any]]:
        wifi_types = {"802-11-wireless", "wifi"}
        connections = []
        for connection in self.list_connections():
            if str(connection.get("type", "")).lower() not in wifi_types:
                continue
            connection = dict(connection)
            connection["details"] = self._show_connection(str(connection["uuid"]))
            connections.append(connection)
        return connections

    def list_active_connections(self) -> list[dict[str, Any]]:
        output = self._run(
            "--fields",
            "NAME,UUID,TYPE,DEVICE",
            "connection",
            "show",
            "--active",
        )
        return parse_multiline_records(output)

    def scan_wifi(self) -> list[dict[str, Any]]:
        self._run("device", "wifi", "rescan", "ifname", self.wifi_interface, multiline=False)
        output = self._run(
            "--fields",
            "IN-USE,SSID,BSSID,MODE,CHAN,RATE,SIGNAL,BARS,SECURITY",
            "device",
            "wifi",
            "list",
            "ifname",
            self.wifi_interface,
        )
        return parse_multiline_records(output)

    def add_connection(
        self,
        *,
        ssid: str,
        password: str | None = None,
        hidden: bool = False,
        autoconnect: bool = True,
        connection_name: str | None = None,
        activate: bool = False,
        priority: int | None = None,
        replace_existing: bool = False,
    ) -> dict[str, Any]:
        if not ssid.strip():
            raise NmcliError("SSID is required")

        connection_name = connection_name.strip() if connection_name else ssid.strip()

        if replace_existing:
            for connection in self.list_connections():
                if connection_name in {
                    str(connection.get("name", "")),
                    str(connection.get("uuid", "")),
                }:
                    self.delete_connection(str(connection["uuid"]))
                    break

        command = [
            "connection",
            "add",
            "type",
            "wifi",
            "ifname",
            self.wifi_interface,
            "con-name",
            connection_name,
            "ssid",
            ssid,
            "connection.autoconnect",
            "yes" if autoconnect else "no",
            "802-11-wireless.hidden",
            "yes" if hidden else "no",
            "ipv4.method",
            "auto",
            "ipv6.method",
            "auto",
        ]

        if priority is not None:
            command.extend(["connection.autoconnect-priority", str(priority)])

        if password:
            command.extend(
                [
                    "wifi-sec.key-mgmt",
                    "wpa-psk",
                    "wifi-sec.psk",
                    password,
                ]
            )

        self._run(*command, multiline=False)

        if activate:
            self.activate_connection(connection_name)

        return self._show_connection(connection_name)

    def update_connection(self, identifier: str, updates: dict[str, Any]) -> dict[str, Any]:
        target = self._connection_target(identifier)
        properties: list[str] = []
        if "password" in updates:
            password = str(updates["password"] or "")
            if password:
                properties.extend(["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password])
        if "hidden" in updates:
            properties.extend(
                ["802-11-wireless.hidden", "yes" if bool(updates["hidden"]) else "no"]
            )
        if "autoconnect" in updates:
            properties.extend(
                ["connection.autoconnect", "yes" if bool(updates["autoconnect"]) else "no"]
            )
        if "priority" in updates and updates["priority"] is not None:
            properties.extend(
                ["connection.autoconnect-priority", str(int(updates["priority"]))]
            )
        if "name" in updates and str(updates["name"]).strip():
            properties.extend(["connection.id", str(updates["name"]).strip()])
        if not properties:
            raise NmcliError("No supported connection properties were supplied")
        self._run("connection", "modify", *target, *properties, multiline=False)
        return self._show_connection(target[1])

    def delete_connection(self, identifier: str) -> None:
        target = self._connection_target(identifier)
        self._run("connection", "delete", *target, multiline=False)

    def activate_connection(self, identifier: str) -> dict[str, Any]:
        target = self._connection_target(identifier)
        self._run("connection", "up", *target, multiline=False)
        return self._show_connection(identifier)

    def deactivate_connection(self, identifier: str) -> dict[str, Any]:
        target = self._connection_target(identifier)
        self._run("connection", "down", *target, multiline=False)
        return self._show_connection(identifier)

    def get_connection(self, identifier: str) -> dict[str, Any]:
        return self._show_connection(identifier)

    def get_wifi_radio(self) -> dict[str, Any]:
        enabled = self._run("radio", "wifi", multiline=False).strip().lower() == "enabled"
        return {"enabled": enabled}

    def set_wifi_radio(self, enabled: bool) -> dict[str, Any]:
        self._run("radio", "wifi", "on" if enabled else "off", multiline=False)
        return self.get_wifi_radio()

    def ensure_usb_sharing(self) -> dict[str, Any]:
        try:
            existing = self._show_connection(self.usb_profile)
        except NmcliError:
            self._run(
                "connection",
                "add",
                "type",
                "ethernet",
                "ifname",
                self.usb_interface,
                "con-name",
                self.usb_profile,
                "connection.autoconnect",
                "yes",
                "ipv4.method",
                "shared",
                "ipv6.method",
                "ignore",
                multiline=False,
            )
            existing = self._show_connection(self.usb_profile)
        else:
            self._run(
                "connection",
                "modify",
                "uuid",
                str(existing["connection_uuid"]),
                "connection.autoconnect",
                "yes",
                "ipv4.method",
                "shared",
                "ipv6.method",
                "ignore",
                multiline=False,
            )
            existing = self._show_connection(self.usb_profile)
        return existing

    def rpi_model(self) -> str:
        candidates = (
            "/proc/device-tree/model",
            "/sys/firmware/devicetree/base/model",
        )
        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as file:
                    value = file.read().replace("\x00", "").strip()
            except OSError:
                continue
            if value:
                return value

        try:
            with open("/proc/cpuinfo", "r", encoding="utf-8", errors="ignore") as file:
                for line in file:
                    if line.lower().startswith("model") and ":" in line:
                        value = line.split(":", 1)[1].strip()
                        if value:
                            return value
        except OSError:
            pass

        return "unknown"

    def status(self) -> dict[str, Any]:
        return {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "rpi_model": self.rpi_model(),
            "general": self.general_status(),
            "wifi_radio": self.get_wifi_radio(),
            "devices": {
                self.wifi_interface: self.device_status(self.wifi_interface),
                self.usb_interface: self.device_status(self.usb_interface),
            },
            "active_connections": self.list_active_connections(),
            "saved_wifi_connections": self.list_wifi_connections(),
        }

    def to_json(self) -> str:
        return json.dumps(self.status(), indent=2, sort_keys=True)
