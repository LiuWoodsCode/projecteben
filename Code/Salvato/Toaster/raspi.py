#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import shutil
import sys
from enum import Enum

from dbus_next import BusType, Variant
from dbus_next.aio import MessageBus


NOTIFICATION_BUS = "org.freedesktop.Notifications"
NOTIFICATION_PATH = "/org/freedesktop/Notifications"
DEFAULT_APP_NAME = "Raspberry Pi"
DEFAULT_ICON = "dialog-warning"
DEFAULT_TIMEOUT_MS = 0


class PiEvent(str, Enum):
	BOOT_PSU_3A = "boot-psu-3a"
	UNDERVOLTAGE = "undervoltage"
	THERMAL_THROTTLED = "thermal-throttled"


@dataclasses.dataclass(frozen=True)
class NotificationSpec:
	summary: str
	body: str
	icon: str = DEFAULT_ICON
	urgency: int = 1
	timeout_ms: int = DEFAULT_TIMEOUT_MS


@dataclasses.dataclass(frozen=True)
class ThrottleState:
	raw: int
	undervoltage_now: bool
	thermal_throttled_now: bool


EVENTS: dict[PiEvent, NotificationSpec] = {
	# Todo: Add button to RPi doc about this
	PiEvent.BOOT_PSU_3A: NotificationSpec(
		summary="Power supply warning",
		body=(
			"This power supply is not capable of supplying 5A. "
			"Power to peripherals will be restricted."
		),
	),
	PiEvent.UNDERVOLTAGE: NotificationSpec(
		summary="Undervoltage detected",
		body="The system is experiencing an undervoltage condition. Please check your power supply.",
	),
	PiEvent.THERMAL_THROTTLED: NotificationSpec(
		summary="Thermal throttled",
		body="The processor is being throttled because it is too hot.",
	),
}


class NotificationSender:
	def __init__(self):
		self._bus: MessageBus | None = None
		self._interface = None

	async def connect(self) -> None:
		self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
		introspection = await self._bus.introspect(NOTIFICATION_BUS, NOTIFICATION_PATH)
		proxy = self._bus.get_proxy_object(NOTIFICATION_BUS, NOTIFICATION_PATH, introspection)
		self._interface = proxy.get_interface(NOTIFICATION_BUS)

	async def notify(self, spec: NotificationSpec) -> int:
		if self._interface is None:
			raise RuntimeError("notification bus is not connected")

		hints = {
			"urgency": Variant("y", max(0, min(2, int(spec.urgency)))),
			"category": Variant("s", "system"),
		}

		notification_id = await self._interface.call_notify(
			DEFAULT_APP_NAME,
			0,
			spec.icon,
			spec.summary,
			spec.body,
			[],
			hints,
			int(spec.timeout_ms),
		)
		return int(notification_id)


def parse_throttled_output(output: str) -> ThrottleState:
	text = output.strip()
	if not text:
		raise ValueError("empty get_throttled output")

	if "=" in text:
		text = text.split("=", 1)[1].strip()

	raw = int(text, 16)
	undervoltage_now = bool(raw & 0x1)
	thermal_throttled_now = bool(raw & 0x4 or raw & 0x8)
	return ThrottleState(raw=raw, undervoltage_now=undervoltage_now, thermal_throttled_now=thermal_throttled_now)


async def read_throttled_state() -> ThrottleState:
	if shutil.which("vcgencmd") is None:
		raise RuntimeError("vcgencmd is not available on this system")

	proc = await asyncio.create_subprocess_exec(
		"vcgencmd",
		"get_throttled",
		stdout=asyncio.subprocess.PIPE,
		stderr=asyncio.subprocess.PIPE,
	)
	stdout, stderr = await proc.communicate()

	if proc.returncode != 0:
		detail = stderr.decode("utf-8", errors="replace").strip()
		raise RuntimeError(detail or "vcgencmd get_throttled failed")

	return parse_throttled_output(stdout.decode("utf-8", errors="replace"))


def event_to_spec(event: PiEvent) -> NotificationSpec:
	return EVENTS[event]


async def emit_event(sender: NotificationSender, event: PiEvent) -> int:
	return await sender.notify(event_to_spec(event))


async def run_once(event: PiEvent) -> None:
	sender = NotificationSender()
	await sender.connect()
	await emit_event(sender, event)


async def run_monitor(interval_seconds: float, boot_warning: bool) -> None:
	if shutil.which("vcgencmd") is None:
		raise RuntimeError("vcgencmd is not available on this system")

	sender = NotificationSender()
	await sender.connect()

	if boot_warning:
		await emit_event(sender, PiEvent.BOOT_PSU_3A)

	previous_state: ThrottleState | None = None
	sent_undervoltage = False
	sent_thermal = False

	while True:
		state = await read_throttled_state()

		if previous_state is None:
			previous_state = state
			sent_undervoltage = state.undervoltage_now
			sent_thermal = state.thermal_throttled_now
			await asyncio.sleep(interval_seconds)
			continue

		if state.undervoltage_now and not sent_undervoltage:
			await emit_event(sender, PiEvent.UNDERVOLTAGE)
			sent_undervoltage = True
		elif not state.undervoltage_now:
			sent_undervoltage = False

		if state.thermal_throttled_now and not sent_thermal:
			await emit_event(sender, PiEvent.THERMAL_THROTTLED)
			sent_thermal = True
		elif not state.thermal_throttled_now:
			sent_thermal = False

		previous_state = state
		await asyncio.sleep(interval_seconds)


def build_parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(
		description="Send Raspberry Pi-style notifications over org.freedesktop.Notifications.",
	)
	subparsers = parser.add_subparsers(dest="command", required=True)

	for event in PiEvent:
		subparsers.add_parser(event.value, help=EVENTS[event].summary)

	monitor = subparsers.add_parser("monitor", help="Poll vcgencmd and emit undervoltage/thermal notifications")
	monitor.add_argument("--interval", type=float, default=1.0, help="Polling interval in seconds")
	monitor.add_argument(
		"--boot-warning",
		action="store_true",
		help="Send the Pi 5 / 5V-3A power warning once at startup",
	)

	return parser


async def main_async(argv: list[str]) -> int:
	parser = build_parser()
	args = parser.parse_args(argv)

	if args.command == "monitor":
		await run_monitor(args.interval, args.boot_warning)
		return 0

	event = PiEvent(args.command)
	await run_once(event)
	return 0


def main() -> int:
	try:
		return asyncio.run(main_async(sys.argv[1:]))
	except KeyboardInterrupt:
		return 130
	except Exception as exc:
		print(f"raspi.py: {exc}", file=sys.stderr)
		return 1


if __name__ == "__main__":
	raise SystemExit(main())
