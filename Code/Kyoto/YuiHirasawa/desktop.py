"""Desktop shell layout and interactions."""

import getpass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("GtkLayerShell", "0.1")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, GtkLayerShell

from backend import (
    ApplicationCatalog,
    WaylandWindowCollector,
    WindowInfo,
    open_audio_settings,
    run_session_action,
)
from shell_widgets import SystemTray, _fixed_icon_image


class ApplicationLauncher(Gtk.Window, ApplicationCatalog):
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
        menu.append(Gtk.SeparatorMenuItem())
        kill_shell = Gtk.MenuItem(label="Kill Shell")
        kill_shell.connect("activate", lambda _item: Gtk.main_quit())
        menu.append(kill_shell)
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
            run_session_action(command)
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


class Taskbar(Gtk.Window, ApplicationCatalog):
    HEIGHT = 36
    ICON_SIZE = 32
    STATUS_ICON_SIZE = 20
    STATUS_PLACEHOLDERS = (
        ("network", "Network", "network-wireless-signal-excellent-symbolic"),
        ("bt", "Bluetooth", "bluetooth-active-symbolic"),
        ("audio", "Audio", "audio-volume-high-symbolic"),
        ("pwr", "Battery", "battery-full-symbolic"),
    )

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
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=1)
        self._tray = SystemTray()
        status_box.pack_start(self._tray, False, False, 0)
        placeholder_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for icon_type, tooltip, icon_path in self.STATUS_PLACEHOLDERS:
            placeholder_box.pack_start(
                self._status_placeholder_button(icon_type, tooltip, icon_path),
                False,
                False,
                0,
            )
        status_box.pack_start(placeholder_box, False, False, 0)
        self._clock = Gtk.Label()
        self._clock.get_style_context().add_class("clock")
        status_box.pack_start(self._clock, False, False, 0)
        bar.pack_end(status_box, False, False, 0)
        self.add(bar)

        # gtk-layer-shell's GTK 3 API sizes the surface from the widget's size
        # request.  resize(1, 1) forces it to discard the previous allocation;
        # the left/right anchors still make the compositor provide full width.
        self.set_size_request(-1, self.HEIGHT)
        self.resize(1, 1)
        self._update_clock()
        GLib.timeout_add_seconds(1, self._update_clock)
        GLib.timeout_add(100, self._refresh_tick)

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
        if icon_type == "audio":
            text = f"{icon_type} is not currently implemented, wait how did we get here???"
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

    def _install_css(self):
        provider = Gtk.CssProvider()
        provider.load_from_data(b"""
        window { font-size: 12px; }

        button.taskbar-button {
            border-radius: 0;
            min-height: 28px;
            min-width: 34px;
            padding: 1px 6px;
        }

        button.tray-button {
            background: transparent;
            border: 0;
            border-radius: 0;
            min-width: 32px;
            min-height: 32px;
            padding: 0;
        }

        button.system-status-button {
            background: transparent;
            border: 0;
            border-radius: 0;
            min-width: 24px;
            min-height: 32px;
            padding: 0;
        }

        .clock {
            min-width: 48px;
            padding: 0 6px 0 1px;
            font-size: 16px;
        }

        button.launch-button {
            border-radius: 0;
            min-height: 28px;
            padding: 1px 16px;
            font-weight: bold;
        }

        .launcher {
            padding: 7px;
        }

        .launcher-user {
            font-size: 14px;
            font-weight: bold;
        }

        .application-list row {
            min-height: 0;
            padding: 0;
        }

        button.application-button {
            border: 0;
            border-radius: 0;
            min-height: 22px;
            padding: 1px 5px;
        }
        """)
        Gtk.StyleContext.add_provider_for_screen(
            self.get_screen(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

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


def main() -> int:
    Taskbar().show_all()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
