from __future__ import annotations

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


class Taskbar(Gtk.Window):
    HEIGHT = 48
    ICON_SIZE = 32

    def __init__(self):
        super().__init__(title="Taskbar")
        self._wayland = WaylandWindowCollector()
        self._groups: dict[str, list[WindowInfo]] = {}
        self._buttons: dict[str, Gtk.Button] = {}
        self._desktop_icon_cache: dict[tuple[str, str], tuple[str, str]] = {}

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_default_size(1, self.HEIGHT)
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
        self._task_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._task_box.set_hexpand(True)
        self.add(self._task_box)
        GLib.timeout_add(100, self._refresh_tick)

    def _install_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
        window { background: #202020; color: white; font-size: 12px; }
        button.taskbar-button { background: #303030; border: 1px solid #505050; border-radius: 0;
            color: white; min-height: 28px; min-width: 34px; padding: 1px 6px; }
        button.taskbar-button:hover { background: #404040; }
        button.taskbar-button.active { background: #34445a; border-color: #7fb5ff; }
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
