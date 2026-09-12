import asyncio
import getpass
import os
import re
import select
import sys
import tempfile
import textwrap
import threading
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from queue import SimpleQueue
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell

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

        try:
            display = Display()
            display.connect()
            registry = display.get_registry()
            registry.dispatcher["global"] = self._on_global
            registry.dispatcher["global_remove"] = self._on_global_remove
            display.roundtrip()

            seat = self._globals.get("wl_seat")
            if seat:
                name, version = seat
                self._seat = registry.bind(name, WlSeat, min(version, 9))

            if not self._bind_wlr(registry):
                print("taskbar: compositor does not expose zwlr_foreign_toplevel_manager_v1", file=sys.stderr)
                display.disconnect()
                return

            display.roundtrip()
            while True:
                self._drain_commands()
                display.flush()
                readable, _, _ = select.select([display.get_fd()], [], [], 0.1)
                display.dispatch(block=bool(readable))
        except Exception as exc:
            print(f"taskbar: Wayland collector failed: {exc}", file=sys.stderr)

    def _on_global(self, _registry, name: int, interface: str, version: int) -> None:
        self._globals[_text(interface)] = (int(name), int(version))

    def _on_global_remove(self, _registry, name: int) -> None:
        for interface, (global_name, _version) in list(self._globals.items()):
            if global_name == int(name):
                self._globals.pop(interface, None)

    def _bind_wlr(self, registry) -> bool:
        item = self._globals.get("zwlr_foreign_toplevel_manager_v1")
        if item is None:
            return False
        cls = self._import_protocol_class() or self._generate_wlr_protocol_class()
        if cls is None:
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
        root = Path(tempfile.gettempdir()) / "taskbar_pywayland_protocols"
        package = root / "taskbar_wayland_protocols"
        xml_path = root / "wlr-foreign-toplevel-management-unstable-v1.xml"
        try:
            package.mkdir(parents=True, exist_ok=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            wayland = package / "wayland"
            wayland.mkdir(exist_ok=True)
            (wayland / "__init__.py").write_text(
                "from pywayland.protocol.wayland import WlOutput, WlSeat, WlSurface\n", encoding="utf-8"
            )
            xml_path.write_text(textwrap.dedent(WLR_PROTOCOL_XML).strip(), encoding="utf-8")
            Protocol.parse_file(str(xml_path)).output(str(package), {
                "zwlr_foreign_toplevel_manager_v1": "wlr_foreign_toplevel_management_unstable_v1",
                "zwlr_foreign_toplevel_handle_v1": "wlr_foreign_toplevel_management_unstable_v1",
                "wl_output": "wayland", "wl_seat": "wayland", "wl_surface": "wayland",
            })
            if str(root) not in sys.path:
                sys.path.insert(0, str(root))
            return getattr(import_module("taskbar_wayland_protocols.wlr_foreign_toplevel_management_unstable_v1"),
                           "ZwlrForeignToplevelManagerV1", None)
        except Exception as exc:
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


@dataclass(frozen=True)
class TrayItem:
    key: str
    service: str
    path: str
    interface: str
    title: str
    tooltip: str
    status: str
    item_is_menu: bool = False
    icon_name: str = ""
    icon_theme_path: str = ""
    icon_width: int = 0
    icon_height: int = 0
    icon_rgba: bytes = b""


class StatusNotifierHost:
    """StatusNotifierWatcher and host backed by dbus-next's asyncio API."""

    WATCHERS = (
        "org.kde.StatusNotifierWatcher",
        "org.freedesktop.StatusNotifierWatcher",
    )
    ITEMS = (
        "org.kde.StatusNotifierItem",
        "org.freedesktop.StatusNotifierItem",
    )
    PROPERTIES = "org.freedesktop.DBus.Properties"

    def __init__(self, changed_callback):
        self._changed_callback = changed_callback
        self._loop = None
        self._bus = None
        self._items: dict[str, TrayItem] = {}
        self._endpoints: dict[str, tuple[str, str]] = {}
        self._watcher_interfaces = {}
        self._owns_watchers: dict[str, bool] = {}
        self._host_names = {
            watcher: watcher.replace("Watcher", f"Host-{os.getpid()}-1")
            for watcher in self.WATCHERS
        }
        self._hosts = set(self._host_names.values())
        threading.Thread(target=self._run, name="StatusNotifierHost", daemon=True).start()

    def _run(self) -> None:
        try:
            asyncio.run(self._async_main())
        except Exception as exc:
            print(f"taskbar: system tray unavailable: {exc}", file=sys.stderr)

    async def _async_main(self) -> None:
        from dbus_next import BusType, Message, MessageType
        from dbus_next.constants import PropertyAccess, RequestNameReply
        from dbus_next.service import ServiceInterface, dbus_property, method, signal
        from dbus_next.aio import MessageBus

        host = self

        class WatcherInterface(ServiceInterface):
            def __init__(self, interface_name):
                super().__init__(interface_name)

            @method()
            def RegisterStatusNotifierItem(self, service: "s"):
                # The raw message handler performs this registration.  It has
                # access to the D-Bus sender, which is required when clients
                # pass an object path instead of their bus name.
                return

            @method()
            def RegisterStatusNotifierHost(self, service: "s"):
                host._register_host(service)

            @dbus_property(access=PropertyAccess.READ)
            def RegisteredStatusNotifierItems(self) -> "as":
                return list(host._endpoints)

            @dbus_property(access=PropertyAccess.READ)
            def IsStatusNotifierHostRegistered(self) -> "b":
                return bool(host._hosts)

            @dbus_property(access=PropertyAccess.READ)
            def ProtocolVersion(self) -> "i":
                return 0

            @signal()
            def StatusNotifierItemRegistered(self, service: "s") -> "s":
                return service

            @signal()
            def StatusNotifierItemUnregistered(self, service: "s") -> "s":
                return service

            @signal()
            def StatusNotifierHostRegistered(self):
                return

            @signal()
            def StatusNotifierHostUnregistered(self):
                return

        self._Message = Message
        self._MessageType = MessageType
        self._loop = asyncio.get_running_loop()
        self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
        self._bus.add_message_handler(self._message_handler)
        for host_name in self._host_names.values():
            await self._bus.request_name(host_name)
        for watcher in self.WATCHERS:
            interface = WatcherInterface(watcher)
            self._watcher_interfaces[watcher] = interface
            self._bus.export("/StatusNotifierWatcher", interface)
            name_reply = await self._bus.request_name(watcher)
            self._owns_watchers[watcher] = name_reply in (
                RequestNameReply.PRIMARY_OWNER, RequestNameReply.ALREADY_OWNER,
            )

        rules = ["type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged'"]
        rules.append(f"type='signal',interface='{self.PROPERTIES}',member='PropertiesChanged'")
        rules.extend(f"type='signal',interface='{name}'" for name in (*self.ITEMS, *self.WATCHERS))
        for rule in rules:
            await self._bus.call(Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[rule],
            ))
        for watcher in self.WATCHERS:
            if self._owns_watchers[watcher]:
                self._watcher_interfaces[watcher].StatusNotifierHostRegistered()
                print(f"taskbar: tray watcher ready: {watcher}", file=sys.stderr)
            else:
                print(f"taskbar: using existing tray watcher: {watcher}", file=sys.stderr)
                await self._attach_to_existing_watcher(watcher)
        await asyncio.get_running_loop().create_future()

    async def _attach_to_existing_watcher(self, watcher: str) -> None:
        await self._bus.call(self._Message(
            destination=watcher,
            path="/StatusNotifierWatcher",
            interface=watcher,
            member="RegisterStatusNotifierHost",
            signature="s",
            body=[self._host_names[watcher]],
        ))
        reply = await self._bus.call(self._Message(
            destination=watcher,
            path="/StatusNotifierWatcher",
            interface=self.PROPERTIES,
            member="Get",
            signature="ss",
            body=[watcher, "RegisteredStatusNotifierItems"],
        ))
        if reply.message_type != self._MessageType.ERROR and reply.body:
            for registration in self._unwrap(reply.body[0]):
                self._register_item(str(registration))

    def _message_handler(self, message):
        if message.message_type == self._MessageType.METHOD_CALL and \
                message.interface in self.WATCHERS and message.member == "RegisterStatusNotifierItem":
            if message.body and self._owns_watchers.get(message.interface, False):
                # Match wf-panel-pi's watcher lifecycle: capture the sender,
                # publish the endpoint and emit ItemRegistered synchronously,
                # before dbus-next dispatches the exported method and replies.
                self._register_item(str(message.body[0]), str(message.sender or ""))
            return None

        if message.message_type != self._MessageType.SIGNAL:
            return None
        if message.interface == "org.freedesktop.DBus" and message.member == "NameOwnerChanged":
            name, old_owner, new_owner = map(str, message.body)
            if old_owner and not new_owner:
                asyncio.create_task(self._remove_services({name, old_owner}))
        elif message.interface == "org.freedesktop.DBus" and message.member == "NameAcquired":
            if message.body and str(message.body[0]) in self.WATCHERS:
                watcher = str(message.body[0])
                self._owns_watchers[watcher] = True
                interface = self._watcher_interfaces.get(watcher)
                if interface is not None:
                    interface.StatusNotifierHostRegistered()
        elif message.interface == self.PROPERTIES and message.body and \
                str(message.body[0]) in self.ITEMS:
            for key, (_service, path) in tuple(self._endpoints.items()):
                if not message.path or message.path == path:
                    asyncio.create_task(self._refresh_item(key))
        elif message.interface in self.ITEMS:
            for key, (_service, path) in tuple(self._endpoints.items()):
                if not message.path or message.path == path:
                    asyncio.create_task(self._refresh_item(key))
        elif message.interface in self.WATCHERS and \
                not self._owns_watchers.get(message.interface, False) and message.body:
            registration = str(message.body[0])
            if message.member == "StatusNotifierItemRegistered":
                self._register_item(registration)
            elif message.member == "StatusNotifierItemUnregistered":
                matching = {
                    key for key, (service, path) in self._endpoints.items()
                    if registration in (key, service, f"{service}{path}")
                }
                asyncio.create_task(self._remove_keys(matching))
        return None

    def _register_item(self, registration: str, sender: str = "") -> None:
        if registration.startswith("/"):
            service = sender
            path = registration
        elif "/" in registration:
            service, raw_path = registration.split("/", 1)
            path = f"/{raw_path}"
        else:
            service = registration
            path = "/StatusNotifierItem"
        if not service:
            return
        key = f"{service}{path}"
        is_new = key not in self._endpoints
        self._endpoints[key] = (service, path)
        if is_new:
            print(f"taskbar: tray item registered: {key}", file=sys.stderr)
            self._emit_item_change("registered", key)
        asyncio.create_task(self._refresh_item(key))

    async def _refresh_item(self, key: str) -> None:
        endpoint = self._endpoints.get(key)
        if endpoint is None:
            return
        service, path = endpoint
        interface = ""
        reply = None
        for candidate in self.ITEMS:
            candidate_reply = await self._bus.call(self._Message(
                destination=service,
                path=path,
                interface=self.PROPERTIES,
                member="GetAll",
                signature="s",
                body=[candidate],
            ))
            if candidate_reply.message_type != self._MessageType.ERROR and candidate_reply.body:
                interface, reply = candidate, candidate_reply
                break
        if reply is not None:
            values = {name: self._unwrap(value) for name, value in reply.body[0].items()}
        else:
            # AppIndicator implementations are not perfectly uniform about
            # Properties.GetAll. wf-panel-pi reads properties individually;
            # retain that compatible path as a fallback.
            values = {}
            property_names = (
                "Id", "Title", "Status", "ItemIsMenu", "IconName",
                "IconThemePath", "IconPixmap", "AttentionIconName",
                "AttentionIconPixmap", "ToolTip",
            )
            for candidate in self.ITEMS:
                item_id = await self._get_property(service, path, candidate, "Id")
                if item_id is None:
                    continue
                interface = candidate
                values["Id"] = item_id
                for property_name in property_names[1:]:
                    value = await self._get_property(
                        service, path, candidate, property_name
                    )
                    if value is not None:
                        values[property_name] = value
                break
        if not interface:
            print(
                f"taskbar: tray item {service}{path} has no supported StatusNotifierItem interface",
                file=sys.stderr,
            )
            return
        status = str(values.get("Status") or "Active")
        attention = status == "NeedsAttention"
        icon_name = str(values.get("AttentionIconName" if attention else "IconName") or "")
        icon_theme_path = str(values.get("IconThemePath") or "")
        pixmaps = values.get("AttentionIconPixmap" if attention else "IconPixmap") or []
        width, height, rgba = self._best_pixmap(pixmaps)
        tooltip = self._tooltip(values.get("ToolTip"))
        title = str(values.get("Title") or values.get("Id") or service)
        first_load = key not in self._items
        self._items[key] = TrayItem(
            key, service, path, interface, title, tooltip or title, status,
            bool(values.get("ItemIsMenu", False)), icon_name, icon_theme_path, width, height, rgba,
        )
        if first_load:
            icon_source = icon_name or (f"{width}x{height} pixmap" if rgba else "no icon")
            print(
                f"taskbar: tray item loaded: {key} "
                f"(status={status}, icon={icon_source})",
                file=sys.stderr,
            )
        self._notify_changed()

    async def _get_property(
            self, service: str, path: str, interface: str, property_name: str):
        reply = await self._bus.call(self._Message(
            destination=service,
            path=path,
            interface=self.PROPERTIES,
            member="Get",
            signature="ss",
            body=[interface, property_name],
        ))
        if reply.message_type == self._MessageType.ERROR or not reply.body:
            return None
        return self._unwrap(reply.body[0])

    async def _remove_services(self, services: set[str]) -> None:
        removed = {key for key, (service, _path) in self._endpoints.items() if service in services}
        await self._remove_keys(removed)

    async def _remove_keys(self, removed: set[str]) -> None:
        for key in removed:
            self._endpoints.pop(key, None)
            self._items.pop(key, None)
            self._emit_item_change("unregistered", key)
        if removed:
            self._notify_changed()

    def _register_host(self, service: str) -> None:
        if service in self._hosts:
            return
        self._hosts.add(service)
        for watcher, interface in self._watcher_interfaces.items():
            if self._owns_watchers.get(watcher, False):
                interface.emit_properties_changed({"IsStatusNotifierHostRegistered": True})
                interface.StatusNotifierHostRegistered()

    def _emit_item_change(self, change: str, key: str) -> None:
        for watcher, interface in self._watcher_interfaces.items():
            if not self._owns_watchers.get(watcher, False):
                continue
            interface.emit_properties_changed(
                {"RegisteredStatusNotifierItems": list(self._endpoints)}
            )
            if change == "registered":
                interface.StatusNotifierItemRegistered(key)
            else:
                interface.StatusNotifierItemUnregistered(key)

    @classmethod
    def _unwrap(cls, value):
        if hasattr(value, "value"):
            return cls._unwrap(value.value)
        if isinstance(value, dict):
            return {key: cls._unwrap(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._unwrap(item) for item in value]
        return value

    @staticmethod
    def _tooltip(value) -> str:
        try:
            title, description = str(value[2]), str(value[3])
            return "\n".join(part for part in (title, description) if part)
        except Exception:
            return ""

    @staticmethod
    def _best_pixmap(pixmaps) -> tuple[int, int, bytes]:
        candidates = []
        for pixmap in pixmaps:
            try:
                width, height, argb = int(pixmap[0]), int(pixmap[1]), bytes(pixmap[2])
                if width > 0 and height > 0 and len(argb) == width * height * 4:
                    candidates.append((abs(max(width, height) - 22), width, height, argb))
            except Exception:
                pass
        if not candidates:
            return 0, 0, b""
        _distance, width, height, argb = min(candidates, key=lambda item: item[0])
        rgba = bytearray(len(argb))
        for offset in range(0, len(argb), 4):
            alpha, red, green, blue = argb[offset:offset + 4]
            rgba[offset:offset + 4] = bytes((red, green, blue, alpha))
        return width, height, bytes(rgba)

    def _notify_changed(self) -> None:
        # wf-panel-pi keeps registered items present even while their Status is
        # Passive.  This is important for apps such as Solaar, which marks its
        # indicator Passive whenever no receiver/device is currently present.
        GLib.idle_add(self._changed_callback, tuple(self._items.values()))

    def invoke(self, key: str, member: str, x: int = 0, y: int = 0) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._invoke(key, member, x, y))
        )

    def scroll(self, key: str, delta: int, orientation: str) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._scroll(key, delta, orientation))
        )

    async def _invoke(self, key: str, member: str, x: int, y: int) -> None:
        endpoint = self._endpoints.get(key)
        if endpoint is None:
            return
        service, path = endpoint
        item = self._items.get(key)
        if item is None:
            return
        await self._bus.call(self._Message(
            destination=service,
            path=path,
            interface=item.interface,
            member=member,
            signature="ii",
            body=[int(x), int(y)],
        ))

    async def _scroll(self, key: str, delta: int, orientation: str) -> None:
        endpoint = self._endpoints.get(key)
        item = self._items.get(key)
        if endpoint is None or item is None:
            return
        service, path = endpoint
        await self._bus.call(self._Message(
            destination=service,
            path=path,
            interface=item.interface,
            member="Scroll",
            signature="is",
            body=[int(delta), orientation],
        ))


class SystemTray(Gtk.Box):
    ICON_SIZE = 22

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self._buttons: dict[str, Gtk.Button] = {}
        self._items: dict[str, TrayItem] = {}
        self._host = StatusNotifierHost(self._set_items)

    def _set_items(self, items: tuple[TrayItem, ...]) -> bool:
        self._items = {item.key: item for item in items}
        current = set(self._buttons)
        incoming = set(self._items)
        for key in current - incoming:
            self.remove(self._buttons.pop(key))
        for item in items:
            button = self._buttons.get(item.key)
            if button is None:
                button = Gtk.Button()
                button.get_style_context().add_class("tray-button")
                button.connect("clicked", self._activate, item.key)
                button.connect("button-press-event", self._button_press, item.key)
                button.connect("scroll-event", self._scroll, item.key)
                self._buttons[item.key] = button
                self.pack_start(button, False, False, 0)
            old = button.get_child()
            if old is not None:
                button.remove(old)
            button.add(self._image(item))
            button.set_tooltip_text(item.tooltip)
        self.show_all()
        return GLib.SOURCE_REMOVE

    def _image(self, item: TrayItem) -> Gtk.Image:
        theme = Gtk.IconTheme.get_default()
        if item.icon_theme_path and item.icon_theme_path not in theme.get_search_path():
            theme.append_search_path(item.icon_theme_path)
            theme.rescan_if_needed()
        if item.icon_name and item.icon_theme_path:
            base = Path(item.icon_theme_path) / item.icon_name
            candidates = (
                [base]
                if base.suffix
                else [base.with_suffix(ext) for ext in (".png", ".svg", ".xpm")]
            )
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                try:
                    pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        str(candidate), self.ICON_SIZE, self.ICON_SIZE, True
                    )
                    return Gtk.Image.new_from_pixbuf(pixbuf)
                except GLib.Error:
                    pass
        if item.icon_name and theme.has_icon(item.icon_name):
            return Gtk.Image.new_from_icon_name(item.icon_name, Gtk.IconSize.MENU)
        if item.icon_name and os.path.isfile(item.icon_name):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    item.icon_name, self.ICON_SIZE, self.ICON_SIZE, True
                )
                return Gtk.Image.new_from_pixbuf(pixbuf)
            except GLib.Error:
                pass
        if item.icon_rgba:
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                GLib.Bytes.new(item.icon_rgba), GdkPixbuf.Colorspace.RGB, True, 8,
                item.icon_width, item.icon_height, item.icon_width * 4,
            )
            scaled = pixbuf.scale_simple(self.ICON_SIZE, self.ICON_SIZE, GdkPixbuf.InterpType.BILINEAR)
            return Gtk.Image.new_from_pixbuf(scaled)
        return Gtk.Image.new_from_icon_name("image-missing", Gtk.IconSize.MENU)

    def _activate(self, _button: Gtk.Button, key: str) -> None:
        item = self._items.get(key)
        self._host.invoke(key, "ContextMenu" if item and item.item_is_menu else "Activate")

    def _button_press(self, _button: Gtk.Button, event, key: str) -> bool:
        if event.button == Gdk.BUTTON_SECONDARY:
            self._host.invoke(key, "ContextMenu", int(event.x_root), int(event.y_root))
            return True
        if event.button == Gdk.BUTTON_MIDDLE:
            self._host.invoke(key, "SecondaryActivate", int(event.x_root), int(event.y_root))
            return True
        return False

    def _scroll(self, _button: Gtk.Button, event, key: str) -> bool:
        if event.direction == Gdk.ScrollDirection.UP:
            delta, orientation = 1, "vertical"
        elif event.direction == Gdk.ScrollDirection.DOWN:
            delta, orientation = -1, "vertical"
        elif event.direction == Gdk.ScrollDirection.LEFT:
            delta, orientation = 1, "horizontal"
        elif event.direction == Gdk.ScrollDirection.RIGHT:
            delta, orientation = -1, "horizontal"
        else:
            return False
        self._host.scroll(key, delta, orientation)
        return True


class ApplicationLauncher(Gtk.Window):
    WIDTH = 340
    HEIGHT = 440

    def __init__(self):
        super().__init__(title="Applications")
        self.set_decorated(False)
        self.set_resizable(False)
        self.connect("key-press-event", self._on_key_press)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        # The taskbar's exclusive zone already makes this edge sit immediately
        # above the bar.  An additional margin would leave a taskbar-sized gap.
        GtkLayerShell.set_margin(self, GtkLayerShell.Edge.BOTTOM, 0)
        GtkLayerShell.set_exclusive_zone(self, 0)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_namespace(self, "application-launcher")

        self.set_size_request(self.WIDTH, self.HEIGHT)
        self.add(self._build_content())

    def _build_content(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.get_style_context().add_class("launcher")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        user = Gtk.Label(label=getpass.getuser(), xalign=0)
        user.get_style_context().add_class("launcher-user")
        user.set_hexpand(True)
        header.pack_start(user, True, True, 0)

        power = Gtk.MenuButton()
        power.set_tooltip_text("Power options")
        power.add(Gtk.Image.new_from_icon_name("system-shutdown-symbolic", Gtk.IconSize.BUTTON))
        power.set_popup(self._power_menu())
        header.pack_end(power, False, False, 0)
        outer.pack_start(header, False, False, 0)

        separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        outer.pack_start(separator, False, False, 0)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        app_list = Gtk.ListBox()
        app_list.set_selection_mode(Gtk.SelectionMode.NONE)
        app_list.get_style_context().add_class("application-list")
        for app in self._applications():
            app_list.add(self._app_row(app))
        scroller.add(app_list)
        outer.pack_start(scroller, True, True, 0)
        return outer

    def _app_row(self, app: Gio.DesktopAppInfo) -> Gtk.Widget:
        row = Gtk.ListBoxRow()
        button = Gtk.Button()
        button.get_style_context().add_class("application-button")
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        icon = app.get_icon()
        image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.MENU) if icon else \
            Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.MENU)
        content.pack_start(image, False, False, 0)
        content.pack_start(Gtk.Label(label=app.get_display_name(), xalign=0), True, True, 0)
        button.add(content)
        button.connect("clicked", self._launch_app, app)
        row.add(button)
        return row

    @staticmethod
    def _applications() -> list[Gio.DesktopAppInfo]:
        apps: list[Gio.DesktopAppInfo] = []
        seen: set[str] = set()
        for desktop_file in Taskbar._desktop_files():
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

    def _launch_app(self, _button: Gtk.Button, app: Gio.DesktopAppInfo) -> None:
        try:
            app.launch([], None)
            self.hide()
        except GLib.Error as exc:
            self._show_error(f"Could not launch {app.get_display_name()}", exc.message)

    def _power_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        actions = (
            ("Lock", ("loginctl", "lock-session"), False),
            ("Suspend", ("systemctl", "suspend"), True),
            ("Restart", ("systemctl", "reboot"), True),
            ("Power Off", ("systemctl", "poweroff"), True),
        )
        for label, command, confirm in actions:
            item = Gtk.MenuItem(label=label)
            item.connect("activate", self._power_action, label, command, confirm)
            menu.append(item)
        menu.show_all()
        return menu

    def _power_action(self, _item: Gtk.MenuItem, label: str, command: tuple[str, ...], confirm: bool) -> None:
        if confirm:
            dialog = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.CANCEL,
                text=f"{label}?",
            )
            dialog.format_secondary_text("Any unsaved work may be lost.")
            dialog.add_button(label, Gtk.ResponseType.OK)
            response = dialog.run()
            dialog.destroy()
            if response != Gtk.ResponseType.OK:
                return
        try:
            Gio.Subprocess.new(command, Gio.SubprocessFlags.NONE)
            self.hide()
        except GLib.Error as exc:
            self._show_error(f"Could not {label.lower()}", exc.message)

    def _show_error(self, title: str, detail: str) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.CLOSE,
            text=title,
        )
        dialog.format_secondary_text(detail)
        dialog.run()
        dialog.destroy()

    def _on_key_press(self, _window: Gtk.Window, event: Gdk.EventKey) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def toggle(self) -> None:
        if self.get_visible():
            self.hide()
        else:
            self.show_all()
            self.present()


class Taskbar(Gtk.Window):
    HEIGHT = 36
    ICON_SIZE = 32

    def __init__(self):
        super().__init__(title="Taskbar")
        self._wayland = WaylandWindowCollector()
        self._groups: dict[str, list[WindowInfo]] = {}
        self._buttons: dict[str, Gtk.Button] = {}
        self._desktop_icon_cache: dict[tuple[str, str], tuple[str, str]] = {}
        self._launcher = ApplicationLauncher()

        self.set_decorated(False)
        self.connect("destroy", lambda *_: Gtk.main_quit())

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_exclusive_zone(self, self.HEIGHT)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_namespace(self, "wlroots-taskbar")

        self._install_css()
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        launch_button = Gtk.Button(label="Launch")
        launch_button.get_style_context().add_class("launch-button")
        launch_button.connect("clicked", lambda *_: self._launcher.toggle())
        bar.pack_start(launch_button, False, False, 0)
        self._task_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._task_box.set_hexpand(True)
        bar.pack_start(self._task_box, True, True, 0)
        self._clock = Gtk.Label()
        self._clock.get_style_context().add_class("clock")
        bar.pack_end(self._clock, False, False, 0)
        self._tray = SystemTray()
        bar.pack_end(self._tray, False, False, 0)
        self.add(bar)

        # gtk-layer-shell's GTK 3 API sizes the surface from the widget's size
        # request.  resize(1, 1) forces it to discard the previous allocation;
        # the left/right anchors still make the compositor provide full width.
        self.set_size_request(-1, self.HEIGHT)
        self.resize(1, 1)
        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)
        GLib.timeout_add(100, self._refresh_tick)

    def _update_clock(self) -> bool:
        now = GLib.DateTime.new_now_local()
        self._clock.set_text(now.format("%H:%M"))
        self._clock.set_tooltip_text(now.format("%A, %B %e, %Y"))
        return True

    def _install_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
        window { background: #202020; color: white; font-size: 12px; }
        button.taskbar-button { background: #303030; border: 1px solid #505050; border-radius: 0;
            color: white; min-height: 28px; min-width: 34px; padding: 1px 6px; }
        button.taskbar-button:hover { background: #404040; }
        button.taskbar-button.active { background: #34445a; border-color: #7fb5ff; }
        button.tray-button { background: transparent; border: 0; border-radius: 0;
            min-width: 24px; min-height: 24px; padding: 1px; }
        button.tray-button:hover { background: #404040; }
        .clock { color: white; min-width: 56px; padding: 0 8px 0 6px; }
        button.launch-button { background: #3a3a3a; border: 1px solid #606060; border-radius: 0;
            color: white; min-height: 28px; padding: 1px 16px; font-weight: bold; }
        button.launch-button:hover { background: #4a4a4a; }
        .launcher { background: #202020; padding: 7px; }
        .launcher-user { color: white; font-size: 14px; font-weight: bold; }
        .application-list { background: #202020; }
        .application-list row { min-height: 0; padding: 0; }
        button.application-button { background: transparent; border: 0; border-radius: 0;
            color: white; min-height: 22px; padding: 1px 5px; }
        button.application-button:hover { background: #3b4654; }
        """)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _refresh_tick(self) -> bool:
        groups: dict[str, list[WindowInfo]] = {}
        for window in self._wayland.windows():
            groups.setdefault(window.group_key, []).append(window)
        self._set_groups(groups)
        return True

    def _set_groups(self, groups: dict[str, list[WindowInfo]]) -> None:
        old, new = set(self._buttons), set(groups)
        for key in old - new:
            self._task_box.remove(self._buttons.pop(key))
        for key in new - old:
            button = Gtk.Button()
            button.get_style_context().add_class("taskbar-button")
            button.connect("clicked", self._on_clicked, key)
            button.connect("button-press-event", self._on_button_press, key)
            self._buttons[key] = button
            self._task_box.pack_start(button, False, False, 0)
        self._groups = groups
        for key, button in self._buttons.items():
            self._update_button(button, groups[key])
        self.show_all()

    def _update_button(self, button: Gtk.Button, windows: list[WindowInfo]) -> None:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        icon = self._window_icon_image(windows[0])
        if icon is not None:
            box.pack_start(icon, False, False, 0)
        old = button.get_child()
        if old is not None:
            button.remove(old)
        button.add(box)
        label = windows[0].title if len(windows) == 1 else f"{windows[0].app_id or windows[0].title} ({len(windows)})"
        button.set_tooltip_text("\n".join([label] + [w.title for w in windows if w.title != label]))
        style = button.get_style_context()
        (style.add_class if any(w.active for w in windows) else style.remove_class)("active")

    def _on_clicked(self, _button, key: str) -> None:
        windows = self._groups.get(key, [])
        if len(windows) == 1:
            self._wayland.manage_window(windows[0].window_id, "activate")
        elif windows:
            self._show_menu(windows, False)

    def _on_button_press(self, _button, event, key: str) -> bool:
        if event.button != Gdk.BUTTON_SECONDARY:
            return False
        self._show_menu(self._groups.get(key, []), True)
        return True

    def _show_menu(self, windows: list[WindowInfo], management: bool) -> None:
        menu = Gtk.Menu()
        for window in windows:
            title = Gtk.MenuItem(label=window.title)
            title.connect("activate", lambda _i, w=window: self._wayland.manage_window(w.window_id, "activate"))
            menu.append(title)
            if management:
                for label, action in (("Minimize", "minimize"), ("Restore", "restore"),
                                      ("Maximize", "maximize"), ("Unmaximize", "unmaximize"), ("Close", "close")):
                    item = Gtk.MenuItem(label=f"  {label}")
                    item.connect("activate", lambda _i, w=window, a=action: self._wayland.manage_window(w.window_id, a))
                    menu.append(item)
                menu.append(Gtk.SeparatorMenuItem())
        menu.show_all()
        menu.popup_at_pointer(None)

    def _window_icon_image(self, window: WindowInfo):
        kind, value = self._desktop_icon_spec(window.app_id, window.title)
        if kind == "path":
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(value, self.ICON_SIZE, self.ICON_SIZE, True)
                return Gtk.Image.new_from_pixbuf(pixbuf)
            except Exception:
                pass
        elif kind == "theme":
            try:
                pixbuf = Gtk.IconTheme.get_default().load_icon(value, self.ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
                return Gtk.Image.new_from_pixbuf(pixbuf)
            except Exception:
                pass
        return Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)

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


def main() -> int:
    Taskbar().show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
