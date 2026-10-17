"""Shared compositor, application discovery, and session services for both shells."""

import os
import re
import select
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from queue import SimpleQueue
from typing import Any, Callable

from gi.repository import Gio, GLib

WLR_PROTOCOL_XML = """
<protocol name="wlr_foreign_toplevel_management_unstable_v1">
  <interface name="zwlr_foreign_toplevel_manager_v1" version="3">
    <request name="stop" type="destructor"/>
    <event name="toplevel">
      <arg name="toplevel" type="new_id" interface="zwlr_foreign_toplevel_handle_v1"/>
    </event>
    <event name="finished"/>
  </interface>
  <interface name="zwlr_foreign_toplevel_handle_v1" version="3">
    <enum name="state">
      <entry name="maximized" value="0"/>
      <entry name="minimized" value="1"/>
      <entry name="activated" value="2"/>
      <entry name="fullscreen" value="3"/>
    </enum>
    <request name="set_maximized"/>
    <request name="unset_maximized"/>
    <request name="set_minimized"/>
    <request name="unset_minimized"/>
    <request name="activate">
      <arg name="seat" type="object" interface="wl_seat"/>
    </request>
    <request name="close"/>
    <request name="set_rectangle">
      <arg name="surface" type="object" interface="wl_surface" allow-null="true"/>
      <arg name="x" type="int"/>
      <arg name="y" type="int"/>
      <arg name="width" type="int"/>
      <arg name="height" type="int"/>
    </request>
    <request name="destroy" type="destructor"/>
    <event name="title">
      <arg name="title" type="string"/>
    </event>
    <event name="app_id">
      <arg name="app_id" type="string"/>
    </event>
    <event name="output_enter">
      <arg name="output" type="object" interface="wl_output"/>
    </event>
    <event name="output_leave">
      <arg name="output" type="object" interface="wl_output"/>
    </event>
    <event name="state">
      <arg name="state" type="array"/>
    </event>
    <event name="done"/>
    <event name="closed"/>
    <event name="parent">
      <arg name="parent" type="object" interface="zwlr_foreign_toplevel_handle_v1" allow-null="true"/>
    </event>
  </interface>
</protocol>
"""


def _text(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


@dataclass
class WindowInfo:
    key: str
    title: str
    app_id: str = ""
    window_id: str = ""
    active: bool = False
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def group_key(self) -> str:
        return self.app_id or self.key


class WaylandWindowCollector:
    """Direct wlroots foreign-toplevel client. No D-Bus or shell service."""

    def __init__(self):
        self._windows: list[WindowInfo] = []
        self._handles: dict[str, Any] = {}
        self._globals: dict[str, tuple[int, int]] = {}
        self._seat = None
        self._commands: SimpleQueue[tuple[str, str]] = SimpleQueue()
        self._thread = threading.Thread(target=self._run, name="TaskbarWaylandWindows", daemon=True)
        self._thread.start()

    def windows(self) -> list[WindowInfo]:
        return list(self._windows)

    def manage_window(self, window_id: str, action: str) -> bool:
        if not window_id or window_id not in self._handles:
            return False
        if action == "activate" and self._seat is None:
            return False
        self._commands.put((window_id, action))
        return True

    def _run(self) -> None:
        try:
            from pywayland.client import Display
            from pywayland.protocol.wayland import WlSeat
        except Exception as exc:
            print(f"taskbar: pywayland unavailable: {exc}", file=sys.stderr)
            return

        while True:
            self._globals.clear()
            self._seat = None
            display = None
            try:
                display = Display()
                display.connect()
                self._collect(display, WlSeat)
            except Exception as exc:
                print(f"taskbar: Wayland collector failed: {exc}", file=sys.stderr)
            finally:
                self._handles.clear()
                self._windows = []
                if display is not None:
                    try:
                        display.disconnect()
                    except Exception as exc:
                        print(f"taskbar: Wayland disconnect failed: {exc}", file=sys.stderr)
            time.sleep(5)

    def _collect(self, display, seat_interface) -> None:
        registry = display.get_registry()
        registry.dispatcher["global"] = self._on_global
        registry.dispatcher["global_remove"] = self._on_global_remove
        display.roundtrip()

        if not self._bind_wlr(registry):
            return

        # The seat is only needed to activate a window. A failed seat bind
        # must not prevent the toplevel manager from listing windows.
        seat = self._globals.get("wl_seat")
        if seat:
            try:
                name, version = seat
                self._seat = registry.bind(name, seat_interface, min(version, seat_interface.version))
            except Exception as exc:
                print(f"taskbar: unable to bind Wayland seat: {exc}", file=sys.stderr)

        display.roundtrip()
        while True:
            self._drain_commands()
            display.flush()
            readable, _, _ = select.select([display.get_fd()], [], [], 0.1)
            display.dispatch(block=bool(readable))

    def _on_global(self, _registry, name: int, interface: str, version: int) -> None:
        self._globals[_text(interface)] = (int(name), int(version))

    def _on_global_remove(self, _registry, name: int) -> None:
        for interface, (global_name, _version) in list(self._globals.items()):
            if global_name == int(name):
                self._globals.pop(interface, None)

    def _bind_wlr(self, registry) -> bool:
        item = self._globals.get("zwlr_foreign_toplevel_manager_v1")
        if item is None:
            print("taskbar: compositor does not expose zwlr_foreign_toplevel_manager_v1", file=sys.stderr)
            return False
        cls = self._import_protocol_class() or self._generate_wlr_protocol_class()
        if cls is None:
            print("taskbar: unable to load foreign-toplevel protocol", file=sys.stderr)
            return False
        name, version = item
        manager = registry.bind(name, cls, min(version, 3))
        manager.dispatcher["toplevel"] = self._on_toplevel
        manager.dispatcher["finished"] = lambda *_args: None
        self._manager = manager
        return True

    def _import_protocol_class(self):
        for module_name in (
            "pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1",
            "pywayland.protocol.wlr_foreign_toplevel_management_unstable_v1.zwlr_foreign_toplevel_manager_v1",
            "taskbar_wayland_protocols.wlr_foreign_toplevel_management_unstable_v1",
        ):
            try:
                value = getattr(import_module(module_name), "ZwlrForeignToplevelManagerV1", None)
                if value is not None:
                    return value
            except Exception:
                pass
        return None

    def _generate_wlr_protocol_class(self):
        try:
            from pywayland.scanner.protocol import Protocol
        except Exception:
            return None
        package_name = "taskbar_wayland_protocols"
        # A failed import can leave the package cached after its temporary
        # directory has gone away. Clear it before each generation attempt.
        for name in list(sys.modules):
            if name == package_name or name.startswith(f"{package_name}."):
                del sys.modules[name]
        try:
            with tempfile.TemporaryDirectory(prefix="taskbar_pywayland_") as directory:
                root = Path(directory)
                package = root / "taskbar_wayland_protocols"
                xml_path = root / "wlr-foreign-toplevel-management-unstable-v1.xml"
                package.mkdir()
                (package / "__init__.py").write_text("", encoding="utf-8")
                wayland = package / "wayland"
                wayland.mkdir()
                (wayland / "__init__.py").write_text(
                    "from pywayland.protocol.wayland import WlOutput, WlSeat, WlSurface\n"
                    "WlOutputProxy = WlOutput.proxy_class\n"
                    "WlSeatProxy = WlSeat.proxy_class\n"
                    "WlSurfaceProxy = WlSurface.proxy_class\n",
                    encoding="utf-8",
                )
                xml_path.write_text(textwrap.dedent(WLR_PROTOCOL_XML).strip(), encoding="utf-8")
                Protocol.parse_file(str(xml_path)).output(str(package), {
                    "zwlr_foreign_toplevel_manager_v1": "wlr_foreign_toplevel_management_unstable_v1",
                    "zwlr_foreign_toplevel_handle_v1": "wlr_foreign_toplevel_management_unstable_v1",
                    "wl_output": "wayland", "wl_seat": "wayland", "wl_surface": "wayland",
                })
                sys.path.insert(0, str(root))
                try:
                    module = import_module("taskbar_wayland_protocols.wlr_foreign_toplevel_management_unstable_v1")
                    return getattr(module, "ZwlrForeignToplevelManagerV1", None)
                finally:
                    sys.path.remove(str(root))
        except Exception as exc:
            for name in list(sys.modules):
                if name == package_name or name.startswith(f"{package_name}."):
                    del sys.modules[name]
            print(f"taskbar: failed to generate wlr protocol: {exc}", file=sys.stderr)
            return None

    def _on_toplevel(self, _manager, handle) -> None:
        data = {"window_id": str(id(handle)), "title": "Window", "app_id": "", "active": False}

        def update(**values):
            data.update(values)
            self._upsert(data)

        handle.dispatcher["title"] = lambda _h, title: update(title=_text(title) or "Window")
        handle.dispatcher["app_id"] = lambda _h, app_id: update(app_id=_text(app_id))
        handle.dispatcher["state"] = lambda _h, state: update(active=self._is_active(state))
        handle.dispatcher["closed"] = lambda _h: self._remove(data["window_id"])
        handle.dispatcher["done"] = lambda _h: self._upsert(data)
        self._handles[data["window_id"]] = handle
        self._upsert(data)

    @staticmethod
    def _is_active(state) -> bool:
        try:
            return 2 in bytes(state)
        except Exception:
            return 2 in (state if isinstance(state, (list, tuple)) else [])

    def _upsert(self, data: dict[str, Any]) -> None:
        window = WindowInfo(
            key=data["window_id"], title=str(data.get("title") or "Window"),
            app_id=str(data.get("app_id") or ""), window_id=data["window_id"],
            active=bool(data.get("active")), raw=dict(data),
        )
        self._windows = [w for w in self._windows if w.key != window.key] + [window]

    def _remove(self, key: str) -> None:
        self._handles.pop(key, None)
        self._windows = [w for w in self._windows if w.key != key]

    def _drain_commands(self) -> None:
        while not self._commands.empty():
            window_id, action = self._commands.get()
            handle = self._handles.get(window_id)
            if handle is None:
                continue
            try:
                if action == "activate" and self._seat is not None:
                    handle.activate(self._seat)
                elif action == "minimize": handle.set_minimized()
                elif action == "restore": handle.unset_minimized()
                elif action == "maximize": handle.set_maximized()
                elif action == "unmaximize": handle.unset_maximized()
                elif action == "close": handle.close()
            except Exception as exc:
                print(f"taskbar: {action} failed: {exc}", file=sys.stderr)


class ApplicationCatalog:
    """Desktop application metadata shared by launchers and window lists."""

    def _desktop_icon_spec(self, app_id: str, title: str) -> tuple[str, str]:
        key = (app_id, title)
        if key in self._desktop_icon_cache:
            return self._desktop_icon_cache[key]
        identities = {self._normalize(app_id), self._normalize(title)} - {""}
        for desktop_file in self._desktop_files():
            try:
                kf = GLib.KeyFile(); kf.load_from_file(str(desktop_file), GLib.KeyFileFlags.NONE)
                names = {self._normalize(desktop_file.stem)}
                for field in ("StartupWMClass", "Name", "Exec"):
                    try: names.add(self._normalize(kf.get_string("Desktop Entry", field)))
                    except Exception: pass
                if not identities.intersection(names - {""}):
                    continue
                icon = kf.get_string("Desktop Entry", "Icon")
                result = ("path", os.path.expanduser(icon)) if icon.startswith(("/", "~")) else ("theme", icon)
                self._desktop_icon_cache[key] = result
                return result
            except Exception:
                pass
        self._desktop_icon_cache[key] = ("theme", app_id) if app_id else ("", "")
        return self._desktop_icon_cache[key]

    @staticmethod
    def _normalize(value: Any) -> str:
        value = Path(str(value or "").strip().lower()).name
        if value.endswith(".desktop"):
            value = value[:-8]
        return re.sub(r"[^a-z0-9_-]+", "", value)

    @staticmethod
    def _desktop_files() -> list[Path]:
        roots = [Path.home() / ".local/share"]
        roots += [Path(p) for p in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if p]
        result = []
        for root in roots:
            try: result.extend((root / "applications").rglob("*.desktop"))
            except Exception: pass
        return result

    @staticmethod
    def _applications() -> list[Gio.DesktopAppInfo]:
        apps: list[Gio.DesktopAppInfo] = []
        seen: set[str] = set()
        for desktop_file in ApplicationCatalog._desktop_files():
            desktop_id = desktop_file.name
            if desktop_id in seen:
                continue
            try:
                app = Gio.DesktopAppInfo.new_from_filename(str(desktop_file))
                if app is None or not app.should_show():
                    continue
                seen.add(desktop_id)
                apps.append(app)
            except Exception:
                continue
        return sorted(apps, key=lambda app: app.get_display_name().casefold())


def open_audio_settings() -> None:
    subprocess.Popen(
        ["pavucontrol"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True,
    )


def run_session_action(command: tuple[str, ...]) -> None:
    Gio.Subprocess.new(command, Gio.SubprocessFlags.NONE)


class KeyboardVisibilityMonitor:
    """Report Squeekboard visibility without depending on shell widgets."""

    OSK_BUS_NAME = "sm.puri.OSK0"
    OSK_OBJECT_PATH = "/sm/puri/OSK0"
    OSK_INTERFACE = "sm.puri.OSK0"

    def __init__(self, on_visibility_changed: Callable[[bool], None]):
        self._osk_proxy = None
        self._on_visibility_changed = on_visibility_changed
        self._watch_osk_visibility()

    def _watch_osk_visibility(self) -> None:
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            None,
            self.OSK_BUS_NAME,
            self.OSK_OBJECT_PATH,
            self.OSK_INTERFACE,
            None,
            self._on_osk_proxy_ready,
        )

    def _on_osk_proxy_ready(self, _source, result) -> None:
        try:
            self._osk_proxy = Gio.DBusProxy.new_for_bus_finish(result)
        except GLib.Error as exc:
            print(f"taskbar: unable to monitor Squeekboard: {exc}", file=sys.stderr)
            return
        self._osk_proxy.connect("g-properties-changed", self._on_osk_properties_changed)
        self._osk_proxy.connect("notify::g-name-owner", self._on_osk_name_owner_changed)
        self._sync_osk_visibility()

    def _on_osk_properties_changed(self, _proxy, changed, _invalidated) -> None:
        values = changed.unpack()
        if "Visible" in values:
            self._on_visibility_changed(bool(values["Visible"]))

    def _on_osk_name_owner_changed(self, _proxy, _property) -> None:
        self._sync_osk_visibility()

    def _sync_osk_visibility(self) -> None:
        if self._osk_proxy is None or self._osk_proxy.get_name_owner() is None:
            self._on_visibility_changed(False)
            return
        visible = self._osk_proxy.get_cached_property("Visible")
        self._on_visibility_changed(bool(visible.unpack()) if visible is not None else False)
