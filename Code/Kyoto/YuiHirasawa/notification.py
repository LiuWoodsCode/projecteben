#!/usr/bin/env python3
from __future__ import annotations

import html
import os
import urllib.parse

import dbus
import dbus.mainloop.glib
import dbus.service

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

# Optional GI-backed sound playback.
try:
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst

    Gst.init(None)
    GST_AVAILABLE = True
except (ImportError, ValueError):
    Gst = None
    GST_AVAILABLE = False


# -----------------------------
# Notification Data Model
# -----------------------------
class Notification:
    def __init__(
        self,
        app_name,
        app_icon,
        summary,
        body,
        timeout,
        actions,
        hints,
        activation_token=None,
        image_path=None,
        image_data=None,
        resident=False,
        use_action_icons=False,
    ):
        self.app_name = app_name
        self.app_icon = app_icon
        self.summary = summary
        self.body = body
        self.timeout = timeout
        self.actions = actions
        self.hints = hints
        self.activation_token = activation_token
        self.image_path = image_path
        self.image_data = image_data
        self.resident = resident
        self.use_action_icons = use_action_icons


class EmptyDbusError(dbus.DBusException):
    _dbus_error_name = "org.freedesktop.DBus.Error.Failed"

    def __init__(self):
        # Spec asks for an empty D-Bus error message in this case.
        super().__init__("")


# -----------------------------
# Banner UI (GTK 3 / PyGObject)
# -----------------------------
class NotificationBanner(Gtk.Window):
    ICON_SIZE = 40
    WIDTH = 380
    MIN_HEIGHT = 118

    _css_installed = False

    def __init__(self):
        super().__init__(type=Gtk.WindowType.POPUP)

        self.set_decorated(False)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.set_focus_on_map(False)
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_app_paintable(True)
        self.set_size_request(self.WIDTH, self.MIN_HEIGHT)

        screen = self.get_screen()
        if screen is not None:
            visual = screen.get_rgba_visual()
            if visual is not None:
                self.set_visual(visual)

        self._install_css()

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        root.set_border_width(0)
        root.get_style_context().add_class("notification-root")
        self.add(root)

        self.icon = Gtk.Image()
        self.icon.set_size_request(self.ICON_SIZE, self.ICON_SIZE)
        self.icon.set_valign(Gtk.Align.START)
        self.icon.set_no_show_all(True)
        root.pack_start(self.icon, False, False, 0)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        root.pack_start(content, True, True, 0)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        content.pack_start(header, False, False, 0)

        self.appid = Gtk.Label()
        self.appid.set_xalign(0.0)
        self.appid.set_yalign(0.5)
        self.appid.set_line_wrap(True)
        self.appid.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.appid.get_style_context().add_class("notification-appid")
        header.pack_start(self.appid, True, True, 0)

        self.close_button = Gtk.Button(label="×")
        self.close_button.set_relief(Gtk.ReliefStyle.NONE)
        self.close_button.set_can_focus(False)
        self.close_button.set_size_request(24, 24)
        self.close_button.get_style_context().add_class("notification-close")
        self.close_button.connect("clicked", self._handle_dismiss_clicked)
        header.pack_end(self.close_button, False, False, 0)

        self.title = Gtk.Label()
        self.title.set_xalign(0.0)
        self.title.set_yalign(0.5)
        self.title.set_line_wrap(True)
        self.title.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.title.set_selectable(False)
        self.title.get_style_context().add_class("notification-title")
        content.pack_start(self.title, False, False, 0)

        self.message = Gtk.Label()
        self.message.set_xalign(0.0)
        self.message.set_yalign(0.0)
        self.message.set_line_wrap(True)
        self.message.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.message.set_selectable(True)
        self.message.set_track_visited_links(True)
        self.message.get_style_context().add_class("notification-message")
        content.pack_start(self.message, False, False, 0)

        self.actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.actions_box.set_margin_top(2)
        content.pack_start(self.actions_box, False, False, 0)

        self._on_invoke = None
        self._on_action = None
        self._on_dismiss = None

        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("button-press-event", self._on_button_press)

        # Realize child widgets once, but leave the banner itself hidden.
        self.show_all()
        self.icon.hide()
        self.hide()

    @classmethod
    def _install_css(cls):
        if cls._css_installed:
            return

        css = b"""
        .notification-root {
            background-color: rgba(25, 25, 25, 0.92);
            color: #eeeeee;
            border-radius: 8px;
            padding: 8px 12px;
        }

        .notification-root label {
            color: #eeeeee;
        }

        .notification-appid {
            font-size: 10px;
            font-weight: 600;
        }

        .notification-title {
            font-size: 16px;
            font-weight: 600;
        }

        .notification-root button {
            color: #eeeeee;
            background-image: none;
            background-color: rgba(255, 255, 255, 0.10);
            border: 1px solid rgba(255, 255, 255, 0.16);
            border-radius: 6px;
            padding: 3px 8px;
            box-shadow: none;
        }

        .notification-root button:hover {
            background-color: rgba(255, 255, 255, 0.15);
        }

        .notification-close {
            padding: 0;
            min-width: 20px;
            min-height: 20px;
        }
        """

        provider = Gtk.CssProvider()
        provider.load_from_data(css)

        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen,
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

        cls._css_installed = True

    def set_handlers(self, on_invoke, on_action, on_dismiss):
        self._on_invoke = on_invoke
        self._on_action = on_action
        self._on_dismiss = on_dismiss

    def _clear_actions(self):
        for child in list(self.actions_box.get_children()):
            self.actions_box.remove(child)
            child.destroy()

    @staticmethod
    def _file_uri_to_path(uri):
        parsed = urllib.parse.urlparse(uri)
        return urllib.parse.unquote(parsed.path)

    def _scale_pixbuf(self, pixbuf):
        if pixbuf is None:
            return None

        width = pixbuf.get_width()
        height = pixbuf.get_height()
        if width <= 0 or height <= 0:
            return None

        scale = min(self.ICON_SIZE / width, self.ICON_SIZE / height)
        target_w = max(1, int(round(width * scale)))
        target_h = max(1, int(round(height * scale)))

        if target_w == width and target_h == height:
            return pixbuf

        return pixbuf.scale_simple(
            target_w,
            target_h,
            GdkPixbuf.InterpType.BILINEAR,
        )

    def _load_pixbuf(self, icon_ref):
        if not icon_ref:
            return None

        icon_ref = str(icon_ref)

        try:
            if icon_ref.startswith("file://"):
                path = self._file_uri_to_path(icon_ref)
                if os.path.exists(path):
                    return self._scale_pixbuf(
                        GdkPixbuf.Pixbuf.new_from_file(path)
                    )

            elif os.path.exists(icon_ref):
                return self._scale_pixbuf(
                    GdkPixbuf.Pixbuf.new_from_file(icon_ref)
                )

            else:
                theme = Gtk.IconTheme.get_default()
                if theme is not None and theme.has_icon(icon_ref):
                    return theme.load_icon(
                        icon_ref,
                        self.ICON_SIZE,
                        Gtk.IconLookupFlags.FORCE_SIZE,
                    )

        except GLib.Error:
            return None

        return None

    def _pixbuf_from_image_data(self, image_data):
        if not image_data or len(image_data) != 7:
            return None

        width, height, rowstride, has_alpha, bits_per_sample, channels, data = image_data

        try:
            width = int(width)
            height = int(height)
            rowstride = int(rowstride)
            has_alpha = bool(has_alpha)
            bits_per_sample = int(bits_per_sample)
            channels = int(channels)
            raw_data = bytes(data)
        except (TypeError, ValueError):
            return None

        if width <= 0 or height <= 0 or rowstride <= 0:
            return None
        if bits_per_sample != 8:
            return None
        if channels not in (3, 4):
            return None

        # The notification spec supplies raw RGB/RGBA bytes.
        # GdkPixbuf keeps a reference to the GLib.Bytes backing store.
        try:
            byte_store = GLib.Bytes.new(raw_data)
            pixbuf = GdkPixbuf.Pixbuf.new_from_bytes(
                byte_store,
                GdkPixbuf.Colorspace.RGB,
                has_alpha,
                bits_per_sample,
                width,
                height,
                rowstride,
            )
        except (GLib.Error, TypeError, ValueError):
            return None

        return self._scale_pixbuf(pixbuf)

    def _handle_dismiss_clicked(self, _button):
        if callable(self._on_dismiss):
            self._on_dismiss()

    def _handle_action_clicked(self, _button, action_key):
        if callable(self._on_action):
            self._on_action(action_key)

    def _set_body_markup(self, body):
        body = body or ""

        # Freedesktop notification body markup is close enough to Pango
        # markup for the common <b>, <i>, <u>, and <a href=""> cases.
        try:
            self.message.set_markup(body)
        except GLib.Error:
            self.message.set_markup(html.escape(body))

    def set_notification(self, n: Notification):
        self.appid.set_text(n.app_name or "Unknown App")
        self.title.set_text(n.summary or "")
        self._set_body_markup(n.body)

        pixbuf = self._load_pixbuf(n.image_path) if n.image_path else None
        if pixbuf is None and n.image_data is not None:
            pixbuf = self._pixbuf_from_image_data(n.image_data)
        if pixbuf is None:
            pixbuf = self._load_pixbuf(n.app_icon)

        if pixbuf is not None:
            self.icon.set_from_pixbuf(pixbuf)
            self.icon.show()
        else:
            self.icon.clear()
            self.icon.hide()

        self._clear_actions()

        icon_theme = Gtk.IconTheme.get_default()
        for action_key, action_label in n.actions:
            button = Gtk.Button()
            button.set_can_focus(False)
            button.set_relief(Gtk.ReliefStyle.NORMAL)

            used_icon = False
            if n.use_action_icons and icon_theme is not None:
                for icon_name in (action_key, action_label):
                    if not icon_name:
                        continue
                    if icon_theme.has_icon(icon_name):
                        image = Gtk.Image.new_from_icon_name(
                            icon_name,
                            Gtk.IconSize.BUTTON,
                        )
                        button.set_image(image)
                        button.set_always_show_image(True)
                        button.set_tooltip_text(action_label or action_key)
                        used_icon = True
                        break

            if not used_icon:
                button.set_label(action_label or action_key)

            button.connect(
                "clicked",
                self._handle_action_clicked,
                action_key,
            )
            self.actions_box.pack_start(button, False, False, 0)
            button.show_all()

        self._update_size_for_content()

    def _update_size_for_content(self):
        # Ask GTK for the natural height at our fixed width.
        self.queue_resize()

        try:
            minimum, natural = self.get_preferred_height_for_width(self.WIDTH)
            target_height = max(self.MIN_HEIGHT, natural, minimum)
        except Exception:
            minimum, natural = self.get_preferred_height()
            target_height = max(self.MIN_HEIGHT, natural, minimum)

        self.resize(self.WIDTH, target_height)

    def _on_button_press(self, _widget, event):
        # Buttons consume their own clicks before they get here.
        if event.button == 1:
            if callable(self._on_invoke):
                self._on_invoke()
            return True

        if event.button in (2, 3):
            if callable(self._on_dismiss):
                self._on_dismiss()
            return True

        return False


# -----------------------------
# Notification Center (queue)
# -----------------------------
class NotificationCenter:
    MARGIN = 10
    SPACING = 10
    DEFAULT_TIMEOUT_MS = 5000

    REASON_EXPIRED = 1
    REASON_DISMISSED = 2
    REASON_CLOSED = 3

    def __init__(self):
        self.active_notifications = []
        self.notifications_by_id = {}
        self.on_notification_closed = None
        self.on_action_invoked = None

        self.screen_geometry = self._get_workarea()

        self._sound_player = None
        if GST_AVAILABLE:
            self._sound_player = Gst.ElementFactory.make("playbin", "pixelnotify-sound")

    @staticmethod
    def _get_workarea():
        screen = Gdk.Screen.get_default()
        if screen is None:
            return Gdk.Rectangle(x=0, y=0, width=1920, height=1080)

        monitor = screen.get_primary_monitor()
        if monitor < 0:
            monitor = 0

        try:
            return screen.get_monitor_workarea(monitor)
        except Exception:
            return screen.get_monitor_geometry(monitor)

    def _target_x(self):
        return (
            self.screen_geometry.x
            + self.screen_geometry.width
            - NotificationBanner.WIDTH
            - self.MARGIN
        )

    def _target_y(self, index):
        y = self.screen_geometry.y + self.MARGIN
        for item in self.active_notifications[:index]:
            y += item["height"] + self.SPACING
        return y

    def _resolve_timeout_ms(self, n: Notification):
        if n.resident or n.timeout == 0:
            return None

        if n.timeout == -1:
            return self.DEFAULT_TIMEOUT_MS

        if n.timeout < -1:
            return self.DEFAULT_TIMEOUT_MS

        return max(1, int(n.timeout))

    @staticmethod
    def _cancel_source(source_id):
        if source_id:
            try:
                GLib.source_remove(source_id)
            except GLib.Error:
                pass

    def _apply_timer(self, item, n: Notification):
        self._cancel_source(item.get("timer_source"))
        item["timer_source"] = None

        timeout = self._resolve_timeout_ms(n)
        if timeout is None:
            return

        def expire():
            item["timer_source"] = None
            self.hide_notification(item["id"], self.REASON_EXPIRED)
            return GLib.SOURCE_REMOVE

        item["timer_source"] = GLib.timeout_add(timeout, expire)

    def _play_sound(self, n: Notification):
        if not GST_AVAILABLE or self._sound_player is None:
            return

        if bool(n.hints.get("suppress-sound", False)) or bool(
            n.hints.get("suppress_sound", False)
        ):
            return

        sound_file = n.hints.get("sound-file") or n.hints.get("sound_file")
        if not isinstance(sound_file, str) or not sound_file:
            return

        if sound_file.startswith("file://"):
            uri = sound_file
        else:
            path = os.path.abspath(sound_file)
            if not os.path.exists(path):
                return
            try:
                uri = GLib.filename_to_uri(path, None)
            except GLib.Error:
                return

        self._sound_player.set_state(Gst.State.NULL)
        self._sound_player.set_property("uri", uri)
        self._sound_player.set_state(Gst.State.PLAYING)

    @staticmethod
    def _ease_out_cubic(t):
        return 1.0 - pow(1.0 - t, 3)

    @staticmethod
    def _ease_in_cubic(t):
        return t * t * t

    def _animate(
        self,
        item,
        end_x,
        end_y,
        end_w,
        end_h,
        duration_ms,
        easing,
        finished=None,
    ):
        self._cancel_source(item.get("anim_source"))
        item["anim_source"] = None

        banner = item["banner"]
        try:
            start_x, start_y = banner.get_position()
        except Exception:
            start_x, start_y = end_x, end_y

        start_w, start_h = banner.get_size()
        start_us = GLib.get_monotonic_time()
        duration_us = max(1, int(duration_ms)) * 1000

        def tick():
            elapsed = GLib.get_monotonic_time() - start_us
            raw_t = min(1.0, elapsed / duration_us)
            t = easing(raw_t)

            x = round(start_x + (end_x - start_x) * t)
            y = round(start_y + (end_y - start_y) * t)
            w = round(start_w + (end_w - start_w) * t)
            h = round(start_h + (end_h - start_h) * t)

            banner.move(x, y)
            if w != start_w or h != start_h:
                banner.resize(max(1, w), max(1, h))

            if raw_t >= 1.0:
                item["anim_source"] = None
                if callable(finished):
                    finished()
                return GLib.SOURCE_REMOVE

            return GLib.SOURCE_CONTINUE

        item["anim_source"] = GLib.timeout_add(16, tick)

    def show_notification(self, notification_id, n: Notification):
        existing = self.notifications_by_id.get(notification_id)

        if existing is not None:
            existing["closing"] = False
            self._cancel_source(existing.get("anim_source"))
            existing["anim_source"] = None

            current_index = self.active_notifications.index(existing)
            existing["banner"].set_notification(n)
            existing["height"] = max(
                NotificationBanner.MIN_HEIGHT,
                existing["banner"].get_size()[1],
            )

            existing["banner"].move(
                self._target_x(),
                self._target_y(current_index),
            )
            existing["banner"].resize(
                NotificationBanner.WIDTH,
                existing["height"],
            )

            existing["activation_token"] = n.activation_token
            self._apply_timer(existing, n)
            self._play_sound(n)
            self._reflow_notifications()
            return

        banner = NotificationBanner()
        banner.set_handlers(
            on_invoke=lambda nid=notification_id: self.invoke_action(nid, "default"),
            on_action=lambda action_key, nid=notification_id: self.invoke_action(
                nid,
                action_key,
            ),
            on_dismiss=lambda nid=notification_id: self.hide_notification(
                nid,
                self.REASON_DISMISSED,
            ),
        )
        banner.set_notification(n)

        banner_height = max(
            NotificationBanner.MIN_HEIGHT,
            banner.get_size()[1],
        )

        end_x = self._target_x()
        end_y = self._target_y(len(self.active_notifications))
        start_x = self.screen_geometry.x + self.screen_geometry.width + self.MARGIN

        banner.move(start_x, end_y)
        banner.resize(NotificationBanner.WIDTH, banner_height)
        banner.show()

        notification_item = {
            "id": notification_id,
            "banner": banner,
            "height": banner_height,
            "timer_source": None,
            "anim_source": None,
            "closing": False,
            "close_reason": self.REASON_CLOSED,
            "close_generation": 0,
            "activation_token": n.activation_token,
        }

        self.active_notifications.append(notification_item)
        self.notifications_by_id[notification_id] = notification_item

        self._animate(
            notification_item,
            end_x,
            end_y,
            NotificationBanner.WIDTH,
            banner_height,
            200,
            self._ease_out_cubic,
        )

        self._apply_timer(notification_item, n)
        self._play_sound(n)

    def hide_notification(self, notification_id, reason=REASON_CLOSED):
        item = self.notifications_by_id.get(notification_id)
        if item is None or item["closing"]:
            return False

        item["closing"] = True
        item["close_reason"] = int(reason)
        item["close_generation"] = item.get("close_generation", 0) + 1
        generation = item["close_generation"]

        self._cancel_source(item.get("timer_source"))
        item["timer_source"] = None

        banner = item["banner"]
        x, y = banner.get_position()
        width, height = banner.get_size()

        end_x = self.screen_geometry.x + self.screen_geometry.width + self.MARGIN

        self._animate(
            item,
            end_x,
            y,
            width,
            height,
            150,
            self._ease_in_cubic,
            finished=lambda nid=notification_id, gen=generation: self._after_hide(
                nid,
                gen,
            ),
        )

        return True

    def hide_current(self):
        if not self.active_notifications:
            return

        self.hide_notification(
            self.active_notifications[0]["id"],
            self.REASON_DISMISSED,
        )

    def invoke_action(self, notification_id, action_key):
        item = self.notifications_by_id.get(notification_id)
        if item is None or item["closing"]:
            return False

        if callable(self.on_action_invoked):
            self.on_action_invoked(
                notification_id,
                str(action_key),
                item.get("activation_token"),
            )

        # Most notification servers close the notification after invocation.
        self.hide_notification(notification_id, self.REASON_DISMISSED)
        return True

    def _after_hide(self, notification_id, generation):
        item = self.notifications_by_id.get(notification_id)
        if item is None:
            return

        if (
            item.get("close_generation", 0) != generation
            or not item.get("closing", False)
        ):
            return

        item = self.notifications_by_id.pop(notification_id, None)
        if item is None:
            return

        if item in self.active_notifications:
            self.active_notifications.remove(item)

        self._cancel_source(item.get("timer_source"))
        self._cancel_source(item.get("anim_source"))

        item["banner"].hide()
        item["banner"].destroy()

        if callable(self.on_notification_closed):
            self.on_notification_closed(
                notification_id,
                item.get("close_reason", self.REASON_CLOSED),
            )

        self._reflow_notifications()

    def _reflow_notifications(self):
        for index, item in enumerate(self.active_notifications):
            if item.get("closing"):
                continue

            banner = item["banner"]
            width, height = banner.get_size()
            item["height"] = max(NotificationBanner.MIN_HEIGHT, height)

            target_x = self._target_x()
            target_y = self._target_y(index)

            current_x, current_y = banner.get_position()
            if current_x == target_x and current_y == target_y:
                continue

            self._animate(
                item,
                target_x,
                target_y,
                NotificationBanner.WIDTH,
                item["height"],
                150,
                self._ease_out_cubic,
            )


# -----------------------------
# D-Bus Server Implementation
# -----------------------------
class NotificationService(dbus.service.Object):
    DBUS_INTERFACE = "org.freedesktop.Notifications"

    def __init__(self, bus, center):
        super().__init__(bus, "/org/freedesktop/Notifications")
        self.center = center
        self._next_notification_id = 1

        self.center.on_notification_closed = self._emit_notification_closed
        self.center.on_action_invoked = self._emit_action_invoked

    @staticmethod
    def _to_plain(value):
        if isinstance(value, dbus.Boolean):
            return bool(value)
        if isinstance(
            value,
            (
                dbus.Byte,
                dbus.Int16,
                dbus.Int32,
                dbus.Int64,
                dbus.UInt16,
                dbus.UInt32,
                dbus.UInt64,
            ),
        ):
            return int(value)
        if isinstance(value, dbus.Double):
            return float(value)
        if isinstance(value, (dbus.String, dbus.ObjectPath, dbus.Signature)):
            return str(value)
        if isinstance(value, dbus.ByteArray):
            return bytes(value)
        if isinstance(value, dbus.Array):
            return [NotificationService._to_plain(v) for v in value]
        if isinstance(value, dbus.Struct):
            return tuple(NotificationService._to_plain(v) for v in value)
        if isinstance(value, dbus.Dictionary):
            return {
                str(NotificationService._to_plain(k)): NotificationService._to_plain(v)
                for k, v in value.items()
            }
        return value

    @staticmethod
    def _parse_actions(actions):
        action_list = [str(NotificationService._to_plain(v)) for v in actions]
        pairs = []
        for index in range(0, len(action_list) - 1, 2):
            key = action_list[index]
            label = action_list[index + 1]
            if key:
                pairs.append((key, label))
        return pairs

    @staticmethod
    def _extract_activation_token(hints):
        for key in (
            "activation-token",
            "activation_token",
            "x-activation-token",
            "x-canonical-activation-token",
            "x-kde-activation-token",
        ):
            token = hints.get(key)
            if isinstance(token, str) and token:
                return token
        return None

    @staticmethod
    def _extract_image_path(hints):
        for key in ("image-path", "image_path"):
            path = hints.get(key)
            if isinstance(path, str) and path:
                return path
        return None

    @staticmethod
    def _extract_image_data(hints):
        for key in ("image-data", "image_data", "icon_data", "icon-data"):
            value = hints.get(key)
            if isinstance(value, (tuple, list)) and len(value) == 7:
                return tuple(value)
        return None

    def _next_id(self):
        notification_id = self._next_notification_id
        self._next_notification_id += 1
        if self._next_notification_id > 0xFFFFFFFF:
            self._next_notification_id = 1

        # Keep IDs non-zero and avoid collisions with live notifications.
        while notification_id == 0 or notification_id in self.center.notifications_by_id:
            notification_id = self._next_notification_id
            self._next_notification_id += 1
            if self._next_notification_id > 0xFFFFFFFF:
                self._next_notification_id = 1

        return notification_id

    @dbus.service.signal(DBUS_INTERFACE, signature='uu')
    def NotificationClosed(self, notification_id, reason):
        pass

    @dbus.service.signal(DBUS_INTERFACE, signature='us')
    def ActionInvoked(self, notification_id, action_key):
        pass

    @dbus.service.signal(DBUS_INTERFACE, signature='us')
    def ActivationToken(self, notification_id, activation_token):
        pass

    def _emit_notification_closed(self, notification_id, reason):
        self.NotificationClosed(dbus.UInt32(notification_id), dbus.UInt32(reason))

    def _emit_action_invoked(self, notification_id, action_key, activation_token):
        if activation_token:
            self.ActivationToken(dbus.UInt32(notification_id), dbus.String(str(activation_token)))
        self.ActionInvoked(dbus.UInt32(notification_id), dbus.String(str(action_key)))

    @dbus.service.method(
        DBUS_INTERFACE,
        in_signature='',
        out_signature='as'
    )
    def GetCapabilities(self):
        capabilities = [
            "action-icons",
            "actions",
            "body",
            "body-hyperlinks",
            "body-images",
            "body-markup",
            "icon-static",
            "persistence",
        ]

        if GST_AVAILABLE:
            capabilities.append("sound")

        return capabilities

    @dbus.service.method(
        DBUS_INTERFACE,
        in_signature='susssasa{sv}i',
        out_signature='u'
    )
    def Notify(self, app_name, replaces_id, app_icon,
               summary, body, actions, hints, expire_timeout):
        replace_id = int(replaces_id)
        if replace_id == 0:
            notification_id = self._next_id()
        else:
            notification_id = replace_id

        plain_hints = self._to_plain(hints) if hints is not None else {}
        if not isinstance(plain_hints, dict):
            plain_hints = {}

        actions_pairs = self._parse_actions(actions)

        notif = Notification(
            app_name=str(self._to_plain(app_name)),
            app_icon=str(self._to_plain(app_icon)),
            summary=str(self._to_plain(summary)),
            body=str(self._to_plain(body)),
            timeout=int(self._to_plain(expire_timeout)),
            actions=actions_pairs,
            hints=plain_hints,
            activation_token=self._extract_activation_token(plain_hints),
            image_path=self._extract_image_path(plain_hints),
            image_data=self._extract_image_data(plain_hints),
            resident=bool(plain_hints.get("resident", False)),
            use_action_icons=bool(plain_hints.get("action-icons", False)),
        )
        self.center.show_notification(notification_id, notif)
        return dbus.UInt32(notification_id)

    @dbus.service.method(
        DBUS_INTERFACE,
        in_signature='u',
        out_signature=''
    )
    def CloseNotification(self, id):
        if not self.center.hide_notification(int(id), NotificationCenter.REASON_CLOSED):
            raise EmptyDbusError()

    @dbus.service.method(
        DBUS_INTERFACE,
        in_signature='',
        out_signature='ssss'
    )
    def GetServerInformation(self):
        return (
            "PixelNotify",   # name
            "PixelProwler",  # vendor
            "1.1",           # version
            "1.3",           # spec version
        )



# -----------------------------
# Main Entrypoint
# -----------------------------
def main():
    # dbus-python and GTK now share the same GLib main loop directly.
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    session_bus = dbus.SessionBus()

    _bus_name = dbus.service.BusName(
        "org.freedesktop.Notifications",
        session_bus,
    )

    center = NotificationCenter()
    _service = NotificationService(session_bus, center)

    Gtk.main()


if __name__ == "__main__":
    main()
