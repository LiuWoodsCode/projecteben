"""D-Bus backend for StatusNotifier tray items."""

import asyncio
import os
import sys
import threading
from dataclasses import dataclass

from gi.repository import GLib

TRAY_ICON_SIZE = 32


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
    menu_path: str = ""


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
    DBUSMENU = "com.canonical.dbusmenu"

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
        from dbus_next import BusType, Message, MessageType, Variant
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
        self._Variant = Variant
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
        rules.append(f"type='signal',interface='{self.DBUSMENU}'")
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
                "AttentionIconPixmap", "ToolTip", "Menu",
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
        menu_path = str(values.get("Menu") or "")
        if not menu_path.startswith("/"):
            menu_path = ""
        item_is_menu = bool(values.get("ItemIsMenu", bool(menu_path)))
        first_load = key not in self._items
        self._items[key] = TrayItem(
            key=key,
            service=service,
            path=path,
            interface=interface,
            title=title,
            tooltip=tooltip or title,
            status=status,
            item_is_menu=item_is_menu,
            icon_name=icon_name,
            icon_theme_path=icon_theme_path,
            icon_width=width,
            icon_height=height,
            icon_rgba=rgba,
            menu_path=menu_path,
        )
        if first_load:
            icon_source = icon_name or (f"{width}x{height} pixmap" if rgba else "no icon")
            print(
                f"taskbar: tray item loaded: {key} "
                f"(status={status}, icon={icon_source}, menu={menu_path or 'none'})",
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
                    candidates.append(
                        (abs(max(width, height) - TRAY_ICON_SIZE), width, height, argb)
                    )
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

    def request_menu(self, key: str, callback) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._request_menu(key, callback))
        )

    def menu_event(self, key: str, item_id: int, timestamp: int) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(
                self._menu_event(key, item_id, timestamp)
            )
        )

    async def _request_menu(self, key: str, callback) -> None:
        item = self._items.get(key)
        if item is None or not item.menu_path:
            return
        reply = await self._bus.call(self._Message(
            destination=item.service,
            path=item.menu_path,
            interface=self.DBUSMENU,
            member="AboutToShow",
            signature="i",
            body=[0],
        ))
        reply = await self._bus.call(self._Message(
            destination=item.service,
            path=item.menu_path,
            interface=self.DBUSMENU,
            member="GetLayout",
            signature="iias",
            body=[0, -1, []],
        ))
        if reply.message_type == self._MessageType.ERROR or len(reply.body) < 2:
            print(
                f"taskbar: could not read tray menu for {key}: "
                f"{getattr(reply, 'error_name', 'D-Bus error')}",
                file=sys.stderr,
            )
            return
        layout = self._menu_layout(self._unwrap(reply.body[1]))
        GLib.idle_add(callback, key, layout)

    async def _menu_event(self, key: str, item_id: int, timestamp: int) -> None:
        item = self._items.get(key)
        if item is None or not item.menu_path:
            return
        reply = await self._bus.call(self._Message(
            destination=item.service,
            path=item.menu_path,
            interface=self.DBUSMENU,
            member="Event",
            signature="isvu",
            body=[
                int(item_id), "clicked", self._Variant("s", ""),
                int(timestamp) & 0xffffffff,
            ],
        ))
        if reply.message_type == self._MessageType.ERROR:
            print(
                f"taskbar: tray menu event failed for {key}: "
                f"{getattr(reply, 'error_name', 'D-Bus error')}",
                file=sys.stderr,
            )

    @classmethod
    def _menu_layout(cls, value):
        try:
            item_id, properties, children = value
        except (TypeError, ValueError):
            return None
        return {
            "id": int(item_id),
            "properties": cls._unwrap(properties),
            "children": [
                child for child in (cls._menu_layout(cls._unwrap(raw)) for raw in children)
                if child is not None
            ],
        }

    async def _invoke(self, key: str, member: str, x: int, y: int) -> None:
        endpoint = self._endpoints.get(key)
        if endpoint is None:
            return
        service, path = endpoint
        item = self._items.get(key)
        if item is None:
            return
        reply = await self._bus.call(self._Message(
            destination=service,
            path=path,
            interface=item.interface,
            member=member,
            signature="ii",
            body=[int(x), int(y)],
        ))
        if reply.message_type == self._MessageType.ERROR:
            print(
                f"taskbar: tray {member} failed for {key}: "
                f"{getattr(reply, 'error_name', 'D-Bus error')}",
                file=sys.stderr,
            )

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

