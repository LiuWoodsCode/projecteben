#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import urllib.parse

import dbus
import dbus.service
import dbus.mainloop.glib

from gi.repository import GLib

from PySide6.QtCore import Qt, QTimer, QRect, QEasingCurve, QPropertyAnimation, QUrl
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QVBoxLayout, QHBoxLayout
from PySide6.QtGui import QIcon, QImage, QPixmap

try:
    from PySide6.QtMultimedia import QSoundEffect
except ImportError:
    QSoundEffect = None


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
# Banner UI (same look concept)
# -----------------------------
class NotificationBanner(QFrame):
    ICON_SIZE = 40  # px (adjust as desired)
    WIDTH = 380
    MIN_HEIGHT = 118

    def __init__(self):
        super().__init__()
        self.setVisible(False)

        # Avoid stealing clicks unless we want to dismiss.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        
        # hide any title bar or borders
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)

        # Make sure we don't show up on the taskbar
        self.setWindowFlag(Qt.Tool, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        
        # and make it so we can't be focused (no capturing keyboard input)
        self.setFocusPolicy(Qt.NoFocus)
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # Work around Pi OS issue where it will (correctly) use the non-focused text color
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(25, 25, 25, 230);
                color: #eee;
                border-radius: 8px;
            }
            QPushButton {
                color: #eee;
                background-color: rgba(255, 255, 255, 26);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 6px;
                padding: 3px 8px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 38);
            }
        """)

        # Root layout: icon (left) + text (right)
        root = QHBoxLayout()
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(10)
        self.setLayout(root)

        self.icon = QLabel()
        self.icon.setFixedSize(self.ICON_SIZE, self.ICON_SIZE)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setVisible(False)
        root.addWidget(self.icon, 0, Qt.AlignTop)

        # Content layout (right)
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(3)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self.appid = QLabel("")
        self.appid.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.appid.setWordWrap(True)
        self.appid.setStyleSheet("font-size: 10px; font-weight: 600;")
        header_layout.addWidget(self.appid, 1)

        self.close_button = QPushButton("x")
        self.close_button.setFixedSize(18, 18)
        self.close_button.setFocusPolicy(Qt.NoFocus)
        self.close_button.clicked.connect(self._handle_dismiss_clicked)
        header_layout.addWidget(self.close_button, 0, Qt.AlignTop)

        self.title = QLabel("")
        self.title.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.title.setWordWrap(True)
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")

        self.message = QLabel("")
        self.message.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.RichText)
        self.message.setOpenExternalLinks(True)
        self.message.setTextInteractionFlags(Qt.TextBrowserInteraction)

        self.actions_layout = QHBoxLayout()
        self.actions_layout.setContentsMargins(0, 2, 0, 0)
        self.actions_layout.setSpacing(6)

        content_layout.addLayout(header_layout)
        content_layout.addWidget(self.title)
        content_layout.addWidget(self.message)
        content_layout.addLayout(self.actions_layout)

        # Put content layout into root
        root.addLayout(content_layout, 1)

        # Keep width fixed but let height expand with content.
        self.setFixedWidth(self.WIDTH)
        self.resize(self.WIDTH, self.MIN_HEIGHT)

        self._dark_mode = False
        self._on_invoke = None
        self._on_action = None
        self._on_dismiss = None

    def set_handlers(self, on_invoke, on_action, on_dismiss):
        self._on_invoke = on_invoke
        self._on_action = on_action
        self._on_dismiss = on_dismiss

    def _clear_actions(self):
        while self.actions_layout.count() > 0:
            item = self.actions_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _load_pixmap(self, icon_ref):
        if not icon_ref:
            return None

        icon_ref = str(icon_ref)
        pixmap = None

        if icon_ref.startswith("file://"):
            path = urllib.parse.unquote(urllib.parse.urlparse(icon_ref).path)
            if os.path.exists(path):
                pixmap = QPixmap(path)
        elif os.path.exists(icon_ref):
            pixmap = QPixmap(icon_ref)
        else:
            theme_icon = QIcon.fromTheme(icon_ref)
            if not theme_icon.isNull():
                pixmap = theme_icon.pixmap(self.ICON_SIZE, self.ICON_SIZE)

        if pixmap is None or pixmap.isNull():
            return None

        return pixmap.scaled(
            self.ICON_SIZE,
            self.ICON_SIZE,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def _pixmap_from_image_data(self, image_data):
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

        format_hint = QImage.Format_RGBA8888 if has_alpha else QImage.Format_RGB888
        image = QImage(raw_data, width, height, rowstride, format_hint).copy()
        if image.isNull():
            return None

        pixmap = QPixmap.fromImage(image)
        if pixmap.isNull():
            return None

        return pixmap.scaled(
            self.ICON_SIZE,
            self.ICON_SIZE,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )

    def _handle_dismiss_clicked(self):
        if callable(self._on_dismiss):
            self._on_dismiss()

    def _handle_action_clicked(self, action_key):
        if callable(self._on_action):
            self._on_action(action_key)

    def set_notification(self, n: Notification):
        self.appid.setText(n.app_name or "Unknown App")
        self.title.setText(n.summary or "")
        self.message.setText(n.body)

        pixmap = self._load_pixmap(n.image_path) if n.image_path else None
        if pixmap is None and n.image_data is not None:
            pixmap = self._pixmap_from_image_data(n.image_data)
        if pixmap is None:
            pixmap = self._load_pixmap(n.app_icon)

        if pixmap is not None:
            self.icon.setPixmap(pixmap)
            self.icon.setVisible(True)
        else:
            self.icon.clear()
            self.icon.setVisible(False)

        self._clear_actions()
        for action_key, action_label in n.actions:
            button = QPushButton(action_label or action_key)
            button.setFocusPolicy(Qt.NoFocus)
            if n.use_action_icons:
                icon = QIcon.fromTheme(action_key)
                if icon.isNull():
                    icon = QIcon.fromTheme(action_label)
                if not icon.isNull():
                    button.setIcon(icon)
                    button.setText("")
                    button.setToolTip(action_label or action_key)
            button.clicked.connect(lambda _checked=False, key=action_key: self._handle_action_clicked(key))
            self.actions_layout.addWidget(button)

        self._update_size_for_content()

    def _update_size_for_content(self):
        layout = self.layout()
        layout.activate()
        target_height = layout.heightForWidth(self.WIDTH)
        if target_height <= 0:
            target_height = layout.sizeHint().height()
        target_height = max(self.MIN_HEIGHT, target_height)
        self.resize(self.WIDTH, target_height)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if callable(self._on_invoke):
                self._on_invoke()
            event.accept()
            return

        if event.button() in (Qt.RightButton, Qt.MiddleButton):
            if callable(self._on_dismiss):
                self._on_dismiss()
            event.accept()
            return

        super().mousePressEvent(event)


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
        self._sound_effect = QSoundEffect() if QSoundEffect is not None else None

        screen = QApplication.primaryScreen()
        self.screen_geometry = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)

    def _target_x(self):
        return self.screen_geometry.right() - NotificationBanner.WIDTH - self.MARGIN

    def _target_y(self, index):
        y = self.screen_geometry.top() + self.MARGIN
        for item in self.active_notifications[:index]:
            y += item["banner"].height() + self.SPACING
        return y

    def _resolve_timeout_ms(self, n: Notification):
        if n.resident or n.timeout == 0:
            return None

        if n.timeout == -1:
            return self.DEFAULT_TIMEOUT_MS

        if n.timeout < -1:
            return self.DEFAULT_TIMEOUT_MS

        return max(1, int(n.timeout))

    def _apply_timer(self, item, n: Notification):
        timer = item["timer"]
        timer.stop()
        timeout = self._resolve_timeout_ms(n)
        if timeout is not None:
            timer.start(timeout)

    def _play_sound(self, n: Notification):
        if self._sound_effect is None:
            return

        if bool(n.hints.get("suppress-sound", False)) or bool(n.hints.get("suppress_sound", False)):
            return

        sound_file = n.hints.get("sound-file") or n.hints.get("sound_file")
        if not isinstance(sound_file, str) or not sound_file:
            return

        if sound_file.startswith("file://"):
            source = QUrl(sound_file)
        else:
            source = QUrl.fromLocalFile(os.path.abspath(sound_file))

        self._sound_effect.setSource(source)
        self._sound_effect.setLoopCount(1)
        self._sound_effect.setVolume(1.0)
        self._sound_effect.play()

    def show_notification(self, notification_id, n: Notification):
        existing = self.notifications_by_id.get(notification_id)
        if existing is not None:
            existing["closing"] = False
            if existing.get("anim") is not None:
                existing["anim"].stop()

            current_index = self.active_notifications.index(existing)
            existing["banner"].set_notification(n)
            updated_height = existing["banner"].height()
            existing_rect = QRect(
                self._target_x(),
                self._target_y(current_index),
                NotificationBanner.WIDTH,
                updated_height,
            )
            existing["banner"].setGeometry(existing_rect)
            existing["activation_token"] = n.activation_token
            self._apply_timer(existing, n)
            self._play_sound(n)
            self._reflow_notifications()
            return

        banner = NotificationBanner()
        banner.set_handlers(
            on_invoke=lambda nid=notification_id: self.invoke_action(nid, "default"),
            on_action=lambda action_key, nid=notification_id: self.invoke_action(nid, action_key),
            on_dismiss=lambda nid=notification_id: self.hide_notification(nid, self.REASON_DISMISSED),
        )
        banner.set_notification(n)

        end_x = self._target_x()
        end_y = self._target_y(len(self.active_notifications))
        banner_height = banner.height()

        start_rect = QRect(self.screen_geometry.right() + self.MARGIN, end_y, banner.WIDTH, banner_height)
        end_rect = QRect(end_x, end_y, banner.WIDTH, banner_height)

        banner.setGeometry(start_rect)
        banner.show()

        enter_anim = QPropertyAnimation(banner, b"geometry", banner)
        enter_anim.setDuration(200)
        enter_anim.setStartValue(start_rect)
        enter_anim.setEndValue(end_rect)
        enter_anim.setEasingCurve(QEasingCurve.OutCubic)
        enter_anim.start()

        timer = QTimer(banner)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda nid=notification_id: self.hide_notification(nid, self.REASON_EXPIRED))

        notification_item = {
            "id": notification_id,
            "banner": banner,
            "timer": timer,
            "anim": enter_anim,
            "closing": False,
            "close_reason": self.REASON_CLOSED,
            "close_generation": 0,
            "activation_token": n.activation_token,
        }
        self.active_notifications.append(notification_item)
        self.notifications_by_id[notification_id] = notification_item
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
        item["timer"].stop()

        geo = item["banner"].geometry()
        end_rect = QRect(self.screen_geometry.right() + self.MARGIN, geo.y(), geo.width(), geo.height())

        exit_anim = QPropertyAnimation(item["banner"], b"geometry", item["banner"])
        exit_anim.setDuration(150)
        exit_anim.setStartValue(geo)
        exit_anim.setEndValue(end_rect)
        exit_anim.setEasingCurve(QEasingCurve.InCubic)
        exit_anim.finished.connect(lambda nid=notification_id, gen=generation: self._after_hide(nid, gen))
        item["anim"] = exit_anim
        exit_anim.start()
        return True

    def hide_current(self):
        if not self.active_notifications:
            return

        self.hide_notification(self.active_notifications[0]["id"], self.REASON_DISMISSED)

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

        # Most servers close the notification after invocation.
        self.hide_notification(notification_id, self.REASON_DISMISSED)
        return True

    def _after_hide(self, notification_id, generation):
        item = self.notifications_by_id.get(notification_id)
        if item is None:
            return

        if item.get("close_generation", 0) != generation or not item.get("closing", False):
            return

        item = self.notifications_by_id.pop(notification_id, None)
        if item is None:
            return

        if item in self.active_notifications:
            self.active_notifications.remove(item)

        item["banner"].hide()
        item["banner"].deleteLater()
        item["timer"].deleteLater()

        if callable(self.on_notification_closed):
            self.on_notification_closed(notification_id, item.get("close_reason", self.REASON_CLOSED))

        self._reflow_notifications()

    def _reflow_notifications(self):
        for index, item in enumerate(self.active_notifications):
            banner = item["banner"]
            current_rect = banner.geometry()
            target_rect = QRect(
                self._target_x(),
                self._target_y(index),
                NotificationBanner.WIDTH,
                banner.height(),
            )

            if current_rect == target_rect:
                continue

            move_anim = QPropertyAnimation(banner, b"geometry", banner)
            move_anim.setDuration(150)
            move_anim.setStartValue(current_rect)
            move_anim.setEndValue(target_rect)
            move_anim.setEasingCurve(QEasingCurve.OutCubic)
            move_anim.start()
            item["anim"] = move_anim


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

        if QSoundEffect is not None:
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
    app = QApplication(sys.argv)

    # D-Bus + GLib integration
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    session_bus = dbus.SessionBus()

    # Request well-known name
    _bus_name = dbus.service.BusName(
        "org.freedesktop.Notifications",
        session_bus
    )

    center = NotificationCenter()
    _service = NotificationService(session_bus, center)

    def pump_glib():
        while GLib.main_context_default().iteration(False):
            pass

    timer = QTimer()
    timer.timeout.connect(pump_glib)
    timer.start(10)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()