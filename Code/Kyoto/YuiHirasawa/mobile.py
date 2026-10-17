"""Mobile shell layout and interactions."""

import math
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell, Pango

from backend import (
    ApplicationCatalog,
    KeyboardVisibilityMonitor,
    WaylandWindowCollector,
    WindowInfo,
    open_audio_settings,
)
from shell_widgets import SystemTray, _fixed_icon_image
from settings import wallpaper_enabled


class ApplicationLauncher(Gtk.Window, ApplicationCatalog):
    WALLPAPER_PATH = (
        Path(__file__).resolve().parent / "assets/icons/backgrounds/zohran.png"
    )

    def __init__(self):
        super().__init__(title="Applications")
        self._locked_open = False
        self._wallpaper_enabled: bool | None = None
        self._wallpaper_source = GdkPixbuf.Pixbuf.new_from_file(
            str(self.WALLPAPER_PATH)
        )
        self._scaled_wallpaper: GdkPixbuf.Pixbuf | None = None
        self._scaled_wallpaper_size: tuple[int, int] | None = None
        self.set_decorated(False)
        self.set_resizable(True)
        self.get_style_context().add_class("application-launcher")
        self.connect("key-press-event", self._on_key_press)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_exclusive_zone(self, 0)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.ON_DEMAND)
        GtkLayerShell.set_namespace(self, "application-launcher")

        overlay = Gtk.Overlay()
        self._wallpaper_image = Gtk.Image()
        self._wallpaper_image.set_hexpand(True)
        self._wallpaper_image.set_vexpand(True)
        self._wallpaper_image.set_halign(Gtk.Align.FILL)
        self._wallpaper_image.set_valign(Gtk.Align.FILL)
        self._wallpaper_image.connect(
            "size-allocate", self._on_wallpaper_size_allocate
        )
        overlay.add(self._wallpaper_image)
        overlay.add_overlay(self._build_content())
        self.add(overlay)

        self._sync_wallpaper()
        GLib.timeout_add_seconds(1, self._sync_wallpaper)

    def _sync_wallpaper(self) -> bool:
        enabled = wallpaper_enabled()
        if enabled != self._wallpaper_enabled:
            self._wallpaper_enabled = enabled
            style = self.get_style_context()
            if enabled:
                style.add_class("wallpaper-enabled")
                self._update_wallpaper_image(
                    self._wallpaper_image.get_allocated_width(),
                    self._wallpaper_image.get_allocated_height(),
                )
            else:
                style.remove_class("wallpaper-enabled")
                self._wallpaper_image.clear()
                self._scaled_wallpaper_size = None
        return GLib.SOURCE_CONTINUE

    def _on_wallpaper_size_allocate(
            self, _image: Gtk.Image, allocation: Gdk.Rectangle) -> None:
        if not self._wallpaper_enabled:
            return
        self._update_wallpaper_image(allocation.width, allocation.height)

    def _update_wallpaper_image(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            return

        source_width = self._wallpaper_source.get_width()
        source_height = self._wallpaper_source.get_height()
        scale = max(width / source_width, height / source_height)
        viewport_size = (width, height)
        if viewport_size != self._scaled_wallpaper_size:
            scaled_width = max(width, math.ceil(source_width * scale))
            scaled_height = max(height, math.ceil(source_height * scale))
            cover = self._wallpaper_source.scale_simple(
                scaled_width, scaled_height, GdkPixbuf.InterpType.BILINEAR
            )
            crop_x = (scaled_width - width) // 2
            crop_y = (scaled_height - height) // 2
            self._scaled_wallpaper = GdkPixbuf.Pixbuf.new_subpixbuf(
                cover, crop_x, crop_y, width, height
            )
            self._scaled_wallpaper_size = viewport_size
            self._wallpaper_image.set_from_pixbuf(self._scaled_wallpaper)

    def _build_content(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.get_style_context().add_class("launcher")

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        app_grid = Gtk.FlowBox()
        app_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        app_grid.set_homogeneous(True)
        app_grid.set_min_children_per_line(4)
        app_grid.set_max_children_per_line(4)
        app_grid.set_column_spacing(8)
        app_grid.set_row_spacing(8)
        app_grid.set_valign(Gtk.Align.START)
        app_grid.get_style_context().add_class("application-grid")
        for app in self._applications():
            app_grid.add(self._app_tile(app))
        scroller.add(app_grid)
        outer.pack_start(scroller, True, True, 0)
        return outer

    def _app_tile(self, app: Gio.DesktopAppInfo) -> Gtk.Widget:
        button = Gtk.Button()
        button.get_style_context().add_class("application-button")
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        content.set_halign(Gtk.Align.CENTER)
        content.set_valign(Gtk.Align.CENTER)
        icon = app.get_icon()
        image = Gtk.Image.new_from_gicon(icon, Gtk.IconSize.DIALOG) if icon else \
            Gtk.Image.new_from_icon_name("application-x-executable", Gtk.IconSize.DIALOG)
        content.pack_start(image, False, False, 0)
        label = Gtk.Label(label=app.get_display_name())
        label.set_justify(Gtk.Justification.CENTER)
        label.set_line_wrap(True)
        label.set_lines(2)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        content.pack_start(label, False, False, 0)
        button.add(content)
        button.connect("clicked", self._launch_app, app)
        return button

    def _launch_app(self, _button: Gtk.Button, app: Gio.DesktopAppInfo) -> None:
        try:
            app.launch([], None)
            if not self._locked_open:
                self.hide()
        except GLib.Error as exc:
            self._show_error(f"Could not launch {app.get_display_name()}", exc.message)

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
            if not self._locked_open:
                self.hide()
            return True
        return False

    def set_locked_open(self, locked: bool) -> None:
        self._locked_open = locked
        if locked:
            self.show_all()
            self.present()

    def toggle(self) -> None:
        if self._locked_open:
            return
        if self.get_visible():
            self.hide()
        else:
            self.show_all()
            self.present()


class StatusBar(Gtk.Window):
    HEIGHT = 16
    STATUS_ICON_SIZE = 16
    STATUS_PLACEHOLDERS = (
        ("network", "Network", "network-wireless-signal-excellent-symbolic"),
        ("bt", "Bluetooth", "bluetooth-active-symbolic"),
        ("audio", "Audio", "audio-volume-high-symbolic"),
        ("pwr", "Battery", "battery-full-symbolic"),
    )

    def __init__(self):
        super().__init__(title="Status Bar")
        self.set_decorated(False)

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.TOP, True)
        GtkLayerShell.set_exclusive_zone(self, self.HEIGHT)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_namespace(self, "wlroots-statusbar")

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._clock = Gtk.Label()
        self._clock.get_style_context().add_class("clock")
        bar.pack_start(self._clock, False, False, 0)

        status_icons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        for icon_type, tooltip, icon_path in self.STATUS_PLACEHOLDERS:
            status_icons.pack_start(
                self._status_placeholder_button(icon_type, tooltip, icon_path),
                False,
                False,
                0,
            )
        bar.pack_end(status_icons, False, False, 0)
        self.add(bar)

        self.set_size_request(-1, self.HEIGHT)
        self.resize(1, 1)
        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)

    def _status_placeholder_button(
            self, icon_type: str, tooltip: str, icon_name: str) -> Gtk.Button:
        button = Gtk.Button()
        button.get_style_context().add_class("system-status-button")
        image = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        image.set_pixel_size(self.STATUS_ICON_SIZE)
        button.add(image)
        button.set_tooltip_text(tooltip)
        button.connect("clicked", self._show_status_placeholder, icon_type)
        return button

    def _show_status_placeholder(
            self, _button: Gtk.Button, icon_type: str) -> None:
        if icon_type == "audio":
            try:
                open_audio_settings()
            except OSError as exc:
                dialog = Gtk.MessageDialog(
                    transient_for=self,
                    modal=True,
                    message_type=Gtk.MessageType.ERROR,
                    buttons=Gtk.ButtonsType.CLOSE,
                    text="Could not open pavucontrol.",
                )
                dialog.format_secondary_text(str(exc))
                dialog.run()
                dialog.destroy()
            return
        if icon_type == "network":
            text = f"{icon_type} is not currently implemented, for now use either the Ranboo API's ironmouse endpoints or nmcli."
        if icon_type == "bt":
            text = f"{icon_type} is not currently implemented, for now use bluetoothctl"
        if icon_type == "pwr":
            text = f"{icon_type} is not currently implemented, the ranboo battery sharing APIs currently aren't implemented and because of the way that eben is designed, upower is not too helpful (except for peripherals and iDevices)"

        dialog = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=text,
        )
        dialog.run()
        dialog.destroy()

    def _update_clock(self) -> bool:
        now = GLib.DateTime.new_now_local()
        self._clock.set_text(now.format("%H:%M"))
        self._clock.set_tooltip_text(now.format("%A, %B %e, %Y"))
        return True


class RunningWindow(Gtk.Window, ApplicationCatalog):
    ICON_SIZE = 32

    def __init__(self, on_running_changed: Callable[[bool], None]):
        super().__init__(title="Running Applications")
        self._wayland = WaylandWindowCollector()
        self._windows: dict[str, WindowInfo] = {}
        self._buttons: dict[str, Gtk.Button] = {}
        self._long_press_gestures: dict[str, Gtk.GestureLongPress] = {}
        self._button_signatures: dict[str, tuple[str, str]] = {}
        self._suppress_clicks: set[str] = set()
        self._pending_long_presses: set[str] = set()
        self._active_window_menu: Gtk.Menu | None = None
        self._desktop_icon_cache: dict[tuple[str, str], tuple[str, str]] = {}
        self._has_running: bool | None = None
        self._on_running_changed = on_running_changed

        self.set_default_size(480, 420)
        self.connect("delete-event", self._on_delete)

        self._install_css()
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_hexpand(True)
        scroller.set_vexpand(True)
        self._task_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._task_box.set_hexpand(True)
        self._task_box.set_valign(Gtk.Align.START)
        scroller.add(self._task_box)
        content.pack_start(scroller, True, True, 0)

        self._tray = SystemTray()
        self._tray.set_halign(Gtk.Align.END)
        content.pack_end(self._tray, False, False, 0)
        self.add(content)

        GLib.timeout_add(100, self._refresh_tick)

    def _on_delete(self, *_args) -> bool:
        self.hide()
        return True

    def toggle(self) -> None:
        if self.get_visible():
            self.hide()
        else:
            self.show_all()
            self.present()

    def _install_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
        button.taskbar-button { min-height: 44px; padding: 4px 8px; }
        button.taskbar-button label.window-title { font-weight: bold; }
        button.taskbar-button label.application-name { font-size: 0.85em; opacity: 0.72; }
        button.tray-button { min-width: 32px; min-height: 12px; padding: 0; }
        button.system-status-button { min-width: 20px; min-height: 0; padding: 0; border-width: 0; }
        .clock { min-width: 48px; min-height: 0; padding: 0 6px 0 1px; }
        button.launch-button { min-height: 28px; padding: 1px 16px; }
        .launcher { padding: 7px; }
        window.application-launcher.wallpaper-enabled .launcher,
        window.application-launcher.wallpaper-enabled scrolledwindow,
        window.application-launcher.wallpaper-enabled viewport,
        window.application-launcher.wallpaper-enabled flowbox {
            background-color: transparent;
            background-image: none;
        }
        .application-grid { padding: 8px; }
        .application-grid flowboxchild { padding: 0; }
        button.application-button { min-height: 92px; padding: 8px 5px; }
        """)
        Gtk.StyleContext.add_provider_for_screen(self.get_screen(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _refresh_tick(self) -> bool:
        windows = [
            window for window in self._wayland.windows()
            if window.title != self.get_title()
        ]
        windows.sort(key=lambda window: (
            (window.app_id or window.title).casefold(),
            window.title.casefold(),
            window.key,
        ))
        self._set_windows(windows)
        return True

    def _set_windows(self, windows: list[WindowInfo]) -> None:
        windows_by_key = {window.key: window for window in windows}
        old, new = set(self._buttons), set(windows_by_key)
        for key in old - new:
            self._task_box.remove(self._buttons.pop(key))
            self._long_press_gestures.pop(key, None)
            self._button_signatures.pop(key, None)
            self._suppress_clicks.discard(key)
            self._pending_long_presses.discard(key)
        for window in windows:
            key = window.key
            if key in self._buttons:
                continue
            button = Gtk.Button()
            button.get_style_context().add_class("taskbar-button")
            button.connect("clicked", self._on_clicked, key)
            button.connect("button-press-event", self._on_button_press, key)
            gesture = Gtk.GestureLongPress.new(button)
            gesture.set_touch_only(False)
            gesture.set_button(Gdk.BUTTON_PRIMARY)
            gesture.connect("pressed", self._on_long_press, key)
            gesture.connect("end", self._on_long_press_end, key)
            self._buttons[key] = button
            self._long_press_gestures[key] = gesture
            self._task_box.pack_start(button, False, False, 0)
        self._windows = windows_by_key
        for position, window in enumerate(windows):
            button = self._buttons[window.key]
            self._task_box.reorder_child(button, position)
            self._update_button(button, window)
        self._task_box.show_all()
        has_running = bool(windows)
        if has_running != self._has_running:
            self._has_running = has_running
            self._on_running_changed(has_running)

    def _update_button(self, button: Gtk.Button, window: WindowInfo) -> None:
        signature = (window.title, window.app_id)
        if self._button_signatures.get(window.key) != signature:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            icon = self._window_icon_image(window)
            if icon is not None:
                row.pack_start(icon, False, False, 0)

            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            names.set_hexpand(True)
            title = Gtk.Label(label=window.title or "Window")
            title.set_xalign(0)
            title.set_ellipsize(Pango.EllipsizeMode.END)
            title.get_style_context().add_class("window-title")
            names.pack_start(title, False, False, 0)
            if window.app_id:
                application = Gtk.Label(label=window.app_id)
                application.set_xalign(0)
                application.set_ellipsize(Pango.EllipsizeMode.END)
                application.get_style_context().add_class("application-name")
                names.pack_start(application, False, False, 0)
            row.pack_start(names, True, True, 0)

            old = button.get_child()
            if old is not None:
                button.remove(old)
            button.add(row)
            button.set_tooltip_text(window.title)
            self._button_signatures[window.key] = signature
            button.show_all()
        style = button.get_style_context()
        (style.add_class if window.active else style.remove_class)("active")

    def _on_clicked(self, _button, key: str) -> None:
        if key in self._suppress_clicks:
            self._suppress_clicks.discard(key)
            return
        window = self._windows.get(key)
        if window is not None:
            self._wayland.manage_window(window.window_id, "activate")

    def _on_button_press(self, button, event, key: str) -> bool:
        if event.button != Gdk.BUTTON_SECONDARY:
            return False
        window = self._windows.get(key)
        if window is not None:
            self._show_window_menu(window, button, event)
        return True

    def _on_long_press(
            self, _gesture: Gtk.GestureLongPress, _x: float, _y: float,
            key: str) -> None:
        window = self._windows.get(key)
        button = self._buttons.get(key)
        if window is None or button is None:
            return
        self._suppress_clicks.add(key)
        self._pending_long_presses.add(key)

    def _on_long_press_end(
            self, _gesture: Gtk.GestureLongPress, _sequence,
            key: str) -> None:
        if key in self._pending_long_presses:
            self._pending_long_presses.discard(key)
            # Open only after the release event has finished. Otherwise the
            # release which completes the long press also dismisses the menu.
            GLib.idle_add(self._open_long_press_menu, key)
        # Let GtkButton process the same release first; if it emits "clicked",
        # _on_clicked consumes the suppression before this idle cleanup runs.
        GLib.idle_add(self._clear_suppressed_click, key)

    def _open_long_press_menu(self, key: str) -> bool:
        window = self._windows.get(key)
        button = self._buttons.get(key)
        if window is not None and button is not None:
            self._show_window_menu(window, button)
        return GLib.SOURCE_REMOVE

    def _clear_suppressed_click(self, key: str) -> bool:
        self._suppress_clicks.discard(key)
        return GLib.SOURCE_REMOVE

    def _show_window_menu(
            self, window: WindowInfo, button: Gtk.Button,
            event=None) -> None:
        menu = Gtk.Menu()
        title = Gtk.MenuItem(label=window.title)
        title.set_sensitive(False)
        menu.append(title)
        menu.append(Gtk.SeparatorMenuItem())
        for label, action in (("Activate", "activate"), ("Minimize", "minimize"),
                              ("Restore", "restore"), ("Maximize", "maximize"),
                              ("Unmaximize", "unmaximize"), ("Close", "close")):
            item = Gtk.MenuItem(label=label)
            item.connect(
                "activate",
                lambda _item, window_id=window.window_id, selected_action=action:
                    self._wayland.manage_window(window_id, selected_action),
            )
            menu.append(item)
        menu.show_all()
        self._active_window_menu = menu
        menu.connect("deactivate", self._on_window_menu_closed)
        if event is not None:
            menu.popup_at_pointer(event)
        else:
            menu.popup_at_widget(
                button,
                Gdk.Gravity.SOUTH_WEST,
                Gdk.Gravity.NORTH_WEST,
                None,
            )

    def _on_window_menu_closed(self, _menu: Gtk.Menu) -> None:
        self._active_window_menu = None

    def _window_icon_image(self, window: WindowInfo):
        kind, value = self._desktop_icon_spec(window.app_id, window.title)
        if kind == "path":
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(value, self.ICON_SIZE, self.ICON_SIZE, True)
                return _fixed_icon_image(pixbuf, self.ICON_SIZE)
            except Exception:
                pass
        elif kind == "theme":
            try:
                pixbuf = Gtk.IconTheme.get_default().load_icon(value, self.ICON_SIZE, Gtk.IconLookupFlags.FORCE_SIZE)
                return _fixed_icon_image(pixbuf, self.ICON_SIZE)
            except Exception:
                pass
        try:
            pixbuf = Gtk.IconTheme.get_default().load_icon(
                "application-x-executable",
                self.ICON_SIZE,
                Gtk.IconLookupFlags.FORCE_SIZE,
            )
            return _fixed_icon_image(pixbuf, self.ICON_SIZE)
        except Exception:
            image = Gtk.Image()
            image.set_size_request(self.ICON_SIZE, self.ICON_SIZE)
            return image


class Taskbar(Gtk.Window):
    HEIGHT = 28
    OSK_HEIGHT = 25

    def __init__(self):
        super().__init__(title="Taskbar")
        self._no_running_programs = True
        self._osk_visible = False
        self._launcher = ApplicationLauncher()
        self._running = RunningWindow(self._on_running_changed)

        self.set_decorated(False)
        self.connect("destroy", lambda *_: Gtk.main_quit())

        GtkLayerShell.init_for_window(self)
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.LEFT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.RIGHT, True)
        GtkLayerShell.set_anchor(self, GtkLayerShell.Edge.BOTTOM, True)
        GtkLayerShell.set_exclusive_zone(self, self.HEIGHT)
        GtkLayerShell.set_keyboard_mode(self, GtkLayerShell.KeyboardMode.NONE)
        GtkLayerShell.set_namespace(self, "wlroots-taskbar")

        compact_css = Gtk.CssProvider()
        compact_css.load_from_data(b"""
        window.taskbar-compact button.launch-button {
            font-size: 8px;
            min-height: 0;
            padding-top: 0;
            padding-bottom: 0;
        }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            compact_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )

        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._bar = bar
        for label, callback in (
                ("Launch", self._launcher.toggle),
                ("Running", self._running.toggle)):
            button = Gtk.Button(label=label)
            button.get_style_context().add_class("launch-button")
            button.set_tooltip_text(label)
            button.connect("clicked", lambda _button, action=callback: action())
            bar.pack_start(button, True, True, 0)
        self.add(bar)

        self.set_size_request(-1, self.HEIGHT)
        self.resize(1, 1)
        self._keyboard_monitor = KeyboardVisibilityMonitor(self._set_osk_visible)
        GLib.idle_add(self._show_initial_launcher)

    def _set_osk_visible(self, visible: bool) -> None:
        if visible == self._osk_visible:
            return
        self._osk_visible = visible
        # Keep only the compact strip reserved while Squeekboard is visible,
        # placing its controls immediately below the keyboard.
        GtkLayerShell.set_exclusive_zone(
            self,
            self.OSK_HEIGHT if visible else self.HEIGHT,
        )
        GtkLayerShell.set_layer(self, GtkLayerShell.Layer.OVERLAY)
        height = self.OSK_HEIGHT if visible else self.HEIGHT
        self.set_size_request(-1, height)
        self._bar.set_size_request(-1, height)
        style = self.get_style_context()
        if visible:
            style.add_class("taskbar-compact")
        else:
            style.remove_class("taskbar-compact")
        self.queue_resize()

    def _show_initial_launcher(self) -> bool:
        if self._no_running_programs:
            self._launcher.set_locked_open(True)
        return GLib.SOURCE_REMOVE

    def _on_running_changed(self, has_running: bool) -> None:
        had_no_running_programs = self._no_running_programs
        self._no_running_programs = not has_running
        self._launcher.set_locked_open(self._no_running_programs)
        if has_running and had_no_running_programs:
            self._launcher.hide()


def main() -> int:
    taskbar = Taskbar()
    statusbar = StatusBar()
    taskbar.show_all()
    statusbar.show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
