"""PySide6 desktop application for configuring the Pi Zero adapter."""

from __future__ import annotations

import os
import sys
from typing import Any, Callable

import requests
from PySide6.QtCore import QObject, QRunnable, QSettings, Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QStyle,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from api import AdapterApi


def bool_text(value: Any) -> str:
    if isinstance(value, str):
        return "yes" if value.strip().lower() in {"1", "true", "yes", "on", "enabled"} else "no"
    return "yes" if bool(value) else "no"


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disabled"}:
            return False
    return bool(value)


def parse_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


class WorkerSignals(QObject):
    finished = Signal(object)
    error = Signal(str)


class ApiWorker(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.fn() 
        except Exception as error:  # noqa: BLE001
            self.signals.error.emit(str(error))
            return
        self.signals.finished.emit(result)


class MainWindow(QMainWindow):
    CONNECTIVITY_CHECK_INTERVAL_MS = 5 * 60 * 1000

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Project Ironmouse Settings")
        self.resize(1200, 800)

        self.settings = QSettings("Pixel Prowler", "ironmouse-conftool")
        self.thread_pool = QThreadPool(self)
        self.api = AdapterApi(str(self.settings.value("base_url", "http://10.12.194.1:5000")))
        self.current_status: dict[str, Any] = {}
        self.current_networks: list[dict[str, Any]] = []
        self.current_connections: list[dict[str, Any]] = []
        self._active_workers: set[ApiWorker] = set()
        self.editing_connection_id: str | None = None
        self._allow_close = False
        self.tray_icon: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self.tray_networks_menu: QMenu | None = None
        self._last_connectivity_alert: tuple[str, str] | None = None
        self.connectivity_timer = QTimer(self)

        self.base_url_edit = QLineEdit(self.api.base_url)
        self.status_summary = QTextEdit()
        self.status_summary.setReadOnly(True)

        self.refresh_button = QPushButton("Refresh")
        self.scan_button = QPushButton("Scan")
        self.usb_share_button = QPushButton("Ensure USB Sharing")
        self.toggle_wifi_button = QPushButton("Toggle Wi-Fi")

        self.scan_table = QTableWidget(0, 8)
        self.scan_table.setHorizontalHeaderLabels(
            ["In Use", "SSID", "Signal", "Security", "BSSID", "Mode", "Channel", "Rate"]
        )
        self.scan_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.scan_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.scan_table.setSelectionMode(QTableWidget.SingleSelection)
        self.scan_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.connections_table = QTableWidget(0, 6)
        self.connections_table.setHorizontalHeaderLabels(
            ["Name", "UUID", "SSID", "Active Device", "Autoconnect", "State"]
        )
        self.connections_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.connections_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.connections_table.setSelectionMode(QTableWidget.SingleSelection)
        self.connections_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        self.use_selected_network_button = QPushButton("Use Selected Network")
        self.other_network_button = QPushButton("Other Network")
        self.cancel_edit_button = QPushButton("Cancel")

        self.ssid_edit = QLineEdit()
        self.name_edit = QLineEdit()
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.open_network_checkbox = QCheckBox("Open network (no password)")
        self.hidden_checkbox = QCheckBox("Hidden network")
        self.autoconnect_checkbox = QCheckBox("Autoconnect")
        self.autoconnect_checkbox.setChecked(True)
        self.activate_checkbox = QCheckBox("Activate after saving")
        self.activate_checkbox.setChecked(True)
        self.replace_checkbox = QCheckBox("Replace same-name profile")
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(-999, 999)
        self.priority_spin.setValue(0)

        self.save_button = QPushButton("Save Profile")
        self.activate_button = QPushButton("Activate Selected")
        self.deactivate_button = QPushButton("Deactivate Selected")
        self.delete_button = QPushButton("Delete Selected")

        self.tabs = QTabWidget()
        self.adapter_status_tab = QWidget()
        self.add_network_tab = QWidget()
        self.known_connections_tab = QWidget()
        self.profile_dialog = QDialog(self)

        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        self._build_ui()
        self._connect_signals()
        self._setup_tray_icon()
        self._setup_connectivity_monitor()
        self.refresh_all()

    def _build_ui(self) -> None:
        central = QWidget()
        root_layout = QVBoxLayout(central)

        top_bar = QHBoxLayout()
        top_bar.addWidget(QLabel("Adapter URL"))
        top_bar.addWidget(self.base_url_edit, 1)
        top_bar.addWidget(self.refresh_button)
        top_bar.addWidget(self.scan_button)
        top_bar.addWidget(self.usb_share_button)
        top_bar.addWidget(self.toggle_wifi_button)
        root_layout.addLayout(top_bar)

        self._build_status_tab()
        self._build_add_network_tab()
        self._build_known_connections_tab()
        self._build_profile_dialog()

        self.tabs.addTab(self.adapter_status_tab, "Adapter Status")
        self.tabs.addTab(self.add_network_tab, "Add Network")
        self.tabs.addTab(self.known_connections_tab, "Known Connections")
        root_layout.addWidget(self.tabs, 1)

        self.setCentralWidget(central)

    def _build_status_tab(self) -> None:
        status_layout = QVBoxLayout(self.adapter_status_tab)
        status_layout.addWidget(self.status_summary, 1)

    def _build_add_network_tab(self) -> None:
        tab_layout = QVBoxLayout(self.add_network_tab)
        tab_layout.addWidget(self.scan_table, 1)

        pick_row = QHBoxLayout()
        pick_row.addWidget(self.use_selected_network_button)
        pick_row.addWidget(self.other_network_button)
        pick_row.addStretch(1)
        tab_layout.addLayout(pick_row)

    def _build_profile_dialog(self) -> None:
        self.profile_dialog.setModal(True)
        self.profile_dialog.setWindowTitle("Add Network")
        self.profile_dialog.resize(520, 420)

        editor_layout = QVBoxLayout(self.profile_dialog)
        form_layout = QFormLayout()
        form_layout.addRow("SSID", self.ssid_edit)
        form_layout.addRow("Profile name", self.name_edit)
        form_layout.addRow("Password", self.password_edit)
        form_layout.addRow("Priority", self.priority_spin)
        form_layout.addRow("", self.open_network_checkbox)
        form_layout.addRow("", self.hidden_checkbox)
        form_layout.addRow("", self.autoconnect_checkbox)
        form_layout.addRow("", self.activate_checkbox)
        form_layout.addRow("", self.replace_checkbox)

        save_row = QHBoxLayout()
        save_row.addWidget(self.save_button)
        save_row.addWidget(self.cancel_edit_button)
        save_row.addStretch(1)

        editor_layout.addLayout(form_layout)
        editor_layout.addLayout(save_row)
        editor_layout.addStretch(1)

    def _build_known_connections_tab(self) -> None:
        tab_layout = QVBoxLayout(self.known_connections_tab)
        tab_layout.addWidget(self.connections_table, 1)

        button_row = QHBoxLayout()
        button_row.addWidget(self.activate_button)
        button_row.addWidget(self.deactivate_button)
        button_row.addWidget(self.delete_button)
        button_row.addStretch(1)
        tab_layout.addLayout(button_row)

    def _connect_signals(self) -> None:
        self.refresh_button.clicked.connect(self.refresh_all)
        self.scan_button.clicked.connect(self.scan_networks)
        self.usb_share_button.clicked.connect(self.ensure_usb_sharing)
        self.toggle_wifi_button.clicked.connect(self.toggle_wifi)
        self.save_button.clicked.connect(self.save_profile)
        self.cancel_edit_button.clicked.connect(self.cancel_profile_editor)
        self.use_selected_network_button.clicked.connect(self.open_editor_from_selected_network)
        self.other_network_button.clicked.connect(self.open_editor_for_other_network)
        self.open_network_checkbox.toggled.connect(self._handle_open_network_toggled)
        self.activate_button.clicked.connect(self.activate_selected)
        self.deactivate_button.clicked.connect(self.deactivate_selected)
        self.delete_button.clicked.connect(self.delete_selected)
        self.scan_table.itemClicked.connect(self.open_editor_from_selected_network)
        self.scan_table.itemDoubleClicked.connect(self.open_editor_from_selected_network)
        self.connections_table.itemClicked.connect(self.open_editor_from_selected_connection)
        self.connections_table.itemDoubleClicked.connect(self.open_editor_from_selected_connection)

    def _setup_connectivity_monitor(self) -> None:
        self.connectivity_timer.setInterval(self.CONNECTIVITY_CHECK_INTERVAL_MS)
        self.connectivity_timer.timeout.connect(self.check_connectivity)
        self.connectivity_timer.start()
        QTimer.singleShot(10_000, self.check_connectivity)

    def _setup_tray_icon(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return

        tray_icon = self.style().standardIcon(QStyle.SP_ComputerIcon)
        self.tray_icon = QSystemTrayIcon(tray_icon, self)
        self.tray_icon.setToolTip("Project Ironmouse")

        menu = self.tray_icon.contextMenu() or None
        if menu is None:
            menu = QMenu(self)
        self.tray_menu = menu

        show_action = menu.addAction("Show / Hide")
        show_action.triggered.connect(self.toggle_window_visibility)

        menu.addSeparator()

        self.tray_networks_menu = menu.addMenu("Wi-Fi Networks")
        self.tray_networks_menu.aboutToShow.connect(self.refresh_tray_networks)
        self._rebuild_tray_networks_menu()

        menu.addSeparator()

        refresh_action = menu.addAction("Refresh")
        refresh_action.triggered.connect(self.refresh_all)

        scan_action = menu.addAction("Scan Networks")
        scan_action.triggered.connect(self.scan_networks)

        toggle_wifi_action = menu.addAction("Toggle Wi-Fi")
        toggle_wifi_action.triggered.connect(self.toggle_wifi)

        usb_action = menu.addAction("Ensure USB Sharing")
        usb_action.triggered.connect(self.ensure_usb_sharing)

        menu.addSeparator()

        quit_action = menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_from_tray)

        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._handle_tray_activated)
        self.tray_icon.show()

    def _handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.Trigger,
            QSystemTrayIcon.DoubleClick,
            QSystemTrayIcon.MiddleClick,
        }:
            self.toggle_window_visibility()

    def toggle_window_visibility(self) -> None:
        if self.isVisible():
            self.hide()
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_from_tray(self) -> None:
        self._allow_close = True
        if self.tray_icon:
            self.tray_icon.hide()
        self.close()

    def closeEvent(self, event: Any) -> None:  # type: ignore[override]
        if self._allow_close or self.tray_icon is None or not self.tray_icon.isVisible():
            super().closeEvent(event)
            return

        event.ignore()
        self.hide()
        self.status_bar.showMessage("Still running in tray. Use tray menu to quit.", 4000)
        self.tray_icon.showMessage(
            "Project Ironmouse",
            "App minimized to tray. Use the tray menu to restore or quit.",
            QSystemTrayIcon.Information,
            2500,
        )

    def _run_task(
        self,
        fn: Callable[[], Any],
        on_success: Callable[[Any], None],
        *,
        progress_message: str,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self.api.set_base_url(self.base_url_edit.text().strip())
        self.settings.setValue("base_url", self.api.base_url)
        self.status_bar.showMessage(progress_message)
        worker = ApiWorker(fn)
        # Keep worker alive until the queued signal handlers complete on the GUI thread.
        worker.setAutoDelete(False)
        self._active_workers.add(worker)
        worker.signals.finished.connect(
            lambda result, active_worker=worker: self._handle_worker_success(
                active_worker, on_success, result
            )
        )
        worker.signals.error.connect(
            lambda message, active_worker=worker: self._handle_worker_error(
                active_worker,
                message,
                on_error=on_error,
            )
        )
        self.thread_pool.start(worker)

    def _handle_worker_success(
        self,
        worker: ApiWorker,
        on_success: Callable[[Any], None],
        result: Any,
    ) -> None:
        try:
            on_success(result)
        finally:
            self._active_workers.discard(worker)

    def _handle_worker_error(
        self,
        worker: ApiWorker,
        message: str,
        *,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        try:
            if on_error is None:
                self._handle_error(message)
            else:
                on_error(message)
        finally:
            self._active_workers.discard(worker)

    def _handle_error(self, message: str) -> None:
        self.status_bar.showMessage(message, 8000)
        QMessageBox.critical(self, "Adapter Error", message)

    def _notify(self, title: str, message: str) -> None:
        if self.tray_icon is not None and self.tray_icon.isVisible():
            self.tray_icon.showMessage(title, message, QSystemTrayIcon.Information, 4000)
            return
        self.status_bar.showMessage(f"{title}: {message}", 8000)

    def _wifi_is_connected(self, status: dict[str, Any]) -> bool:
        devices = status.get("devices", {})
        wifi_device = devices.get(self.api.wifi_interface, {})
        state_text = f"{wifi_device.get('general_state', '')} {wifi_device.get('general_connection', '')}".lower()
        if "disconnected" in state_text:
            return False
        if "connected" in state_text:
            return True

        for connection in status.get("active_connections", []):
            if str(connection.get("device", "")).strip() == self.api.wifi_interface:
                return True
        return False

    def _test_internet_connectivity(self) -> dict[str, str]:
        url = "https://networkcheck.kde.org/"
        try:
            response = self.api.session.get(url, timeout=5)
        except requests.RequestException as error:
            return {"state": "down", "detail": str(error)}

        body = response.text.strip()
        if body == "OK":
            return {"state": "ok", "detail": "OK"}
        return {"state": "captive", "detail": body[:200]}

    def _connectivity_alert_key(self, wifi_connected: bool, internet_state: str) -> tuple[str, str]:
        if not wifi_connected:
            return ("wifi", "disconnected")
        return ("internet", internet_state)

    def _describe_connectivity_result(self, wifi_connected: bool, internet_result: dict[str, str] | None) -> tuple[str, str]:
        if not wifi_connected:
            return (
                "Wi-Fi disconnected",
                "Wi-Fi is disconnected.",
            )

        if internet_result is None:
            return (
                "Wi-Fi connected",
                "Wi-Fi is connected.",
            )

        state = internet_result.get("state", "down")
        if state == "ok":
            return (
                "Internet check passed",
                "Wi-Fi is connected and the internet test returned OK.",
            )
        if state == "captive":
            return (
                "Captive portal suspected",
                "Wi-Fi is connected, but the internet check returned unexpected content. You may be behind a captive portal.",
            )
        return (
            "Internet check failed",
            "Wi-Fi is connected, but the internet check timed out or failed. Your internet connection may be down.",
        )

    def check_connectivity(self) -> None:
        def load() -> dict[str, Any]:
            status = self.api.status()
            wifi_connected = self._wifi_is_connected(status)
            internet_result = self._test_internet_connectivity() if wifi_connected else None
            return {
                "wifi_connected": wifi_connected,
                "internet_result": internet_result,
            }

        def apply(result: dict[str, Any]) -> None:
            wifi_connected = bool(result.get("wifi_connected", False))
            internet_result = result.get("internet_result")
            internet_state = str(internet_result.get("state", "disconnected")) if isinstance(internet_result, dict) else "disconnected"
            alert_key = self._connectivity_alert_key(wifi_connected, internet_state)
            if self._last_connectivity_alert == alert_key:
                return

            self._last_connectivity_alert = alert_key
            title, message = self._describe_connectivity_result(wifi_connected, internet_result if isinstance(internet_result, dict) else None)
            self._notify(title, message)

        def on_error(message: str) -> None:
            self._notify("Connectivity check failed", message)

        self._run_task(
            load,
            apply,
            progress_message="Checking connectivity...",
            on_error=on_error,
        )

    def selected_connection_id(self) -> str | None:
        row = self.connections_table.currentRow()
        if row < 0:
            return None
        item = self.connections_table.item(row, 1)
        if item is None:
            return None
        return item.text()

    def selected_scan_ssid(self) -> str | None:
        row = self.scan_table.currentRow()
        if row < 0:
            return None
        item = self.scan_table.item(row, 1)
        if item is None:
            return None
        return item.text()

    def refresh_all(self) -> None:
        def load() -> dict[str, Any]:
            return {
                "status": self.api.status(),
                "connections": self.api.list_connections(),
            }

        def apply(data: dict[str, Any]) -> None:
            self.current_status = data["status"]
            self.update_status_summary(data["status"])
            self.populate_connections(data["connections"])
            enabled = bool(data["status"].get("wifi_radio", {}).get("enabled", False))
            self.toggle_wifi_button.setText("Disable Wi-Fi" if enabled else "Enable Wi-Fi")
            self.status_bar.showMessage("Adapter data refreshed", 4000)

        self._run_task(load, apply, progress_message="Refreshing adapter status...")

    def scan_networks(self) -> None:
        def apply(networks: list[dict[str, Any]]) -> None:
            self.populate_scan_table(networks)
            self._rebuild_tray_networks_menu()
            self.status_bar.showMessage(f"Scan complete: {len(networks)} network(s) found", 4000)

        self._run_task(self.api.scan, apply, progress_message="Scanning for Wi-Fi networks...")

    def ensure_usb_sharing(self) -> None:
        def apply(_connection: dict[str, Any]) -> None:
            self.status_bar.showMessage("USB sharing profile is ready", 4000)
            self.refresh_all()

        self._run_task(
            self.api.ensure_usb_sharing,
            apply,
            progress_message="Ensuring usb0 shared profile exists...",
        )

    def toggle_wifi(self) -> None:
        current = bool(self.current_status.get("wifi_radio", {}).get("enabled", False))

        def task() -> dict[str, Any]:
            return self.api.set_wifi_radio(not current)

        def apply(result: dict[str, Any]) -> None:
            enabled = bool(result.get("enabled", False))
            self.current_status.setdefault("wifi_radio", {})["enabled"] = enabled
            self.toggle_wifi_button.setText("Disable Wi-Fi" if enabled else "Enable Wi-Fi")
            self.status_bar.showMessage(
                "Wi-Fi enabled" if enabled else "Wi-Fi disabled",
                4000,
            )
            self.refresh_all()

        self._run_task(task, apply, progress_message="Updating Wi-Fi radio state...")

    def save_profile(self) -> None:
        ssid = self.ssid_edit.text().strip()
        if not ssid:
            QMessageBox.warning(self, "Missing SSID", "Enter an SSID before saving a profile.")
            return

        password = self.password_edit.text()
        if self.open_network_checkbox.isChecked():
            password = ""

        payload = {
            "ssid": ssid,
            "name": self.name_edit.text().strip() or ssid,
            "password": password,
            "hidden": self.hidden_checkbox.isChecked(),
            "autoconnect": self.autoconnect_checkbox.isChecked(),
            "activate": self.activate_checkbox.isChecked(),
            "replace_existing": self.replace_checkbox.isChecked(),
            "priority": self.priority_spin.value(),
        }

        if self.editing_connection_id:
            identifier = self.editing_connection_id

            def apply(_connection: dict[str, Any]) -> None:
                self.status_bar.showMessage("Profile updated", 4000)
                self.reset_profile_form()
                self.close_profile_editor()
                self.refresh_all()

            self._run_task(
                lambda: self.api.update_connection(identifier, payload),
                apply,
                progress_message="Updating Wi-Fi profile...",
            )
            return

        def apply(_connection: dict[str, Any]) -> None:
            self.status_bar.showMessage("Profile saved", 4000)
            self.reset_profile_form()
            self.close_profile_editor()
            self.refresh_all()

        self._run_task(
            lambda: self.api.add_connection(payload),
            apply,
            progress_message="Saving Wi-Fi profile...",
        )

    def activate_selected(self) -> None:
        identifier = self.selected_connection_id()
        if not identifier:
            QMessageBox.information(self, "No Selection", "Select a saved profile first.")
            return

        def apply(_connection: dict[str, Any]) -> None:
            self.status_bar.showMessage("Connection activated", 4000)
            self.refresh_all()

        self._run_task(
            lambda: self.api.activate_connection(identifier),
            apply,
            progress_message="Activating selected profile...",
        )

    def deactivate_selected(self) -> None:
        identifier = self.selected_connection_id()
        if not identifier:
            QMessageBox.information(self, "No Selection", "Select a saved profile first.")
            return

        def apply(_connection: dict[str, Any]) -> None:
            self.status_bar.showMessage("Connection deactivated", 4000)
            self.refresh_all()

        self._run_task(
            lambda: self.api.deactivate_connection(identifier),
            apply,
            progress_message="Deactivating selected profile...",
        )

    def delete_selected(self) -> None:
        identifier = self.selected_connection_id()
        if not identifier:
            QMessageBox.information(self, "No Selection", "Select a saved profile first.")
            return
        name_item = self.connections_table.item(self.connections_table.currentRow(), 0)
        profile_name = name_item.text() if name_item else identifier
        confirm = QMessageBox.question(
            self,
            "Delete Profile",
            f"Delete the saved profile '{profile_name}'?",
        )
        if confirm != QMessageBox.Yes:
            return

        def apply(_result: Any) -> None:
            self.status_bar.showMessage("Profile deleted", 4000)
            self.refresh_all()

        self._run_task(
            lambda: self.api.delete_connection(identifier),
            apply,
            progress_message="Deleting selected profile...",
        )

    def populate_from_scan_selection(self) -> None:
        ssid = self.selected_scan_ssid()
        if not ssid:
            return
        self.open_profile_editor()
        self.populate_profile_from_network(ssid)

    def populate_from_connection_selection(self) -> None:
        row = self.connections_table.currentRow()
        if row < 0:
            return
        name = self.connections_table.item(row, 0)
        ssid = self.connections_table.item(row, 2)
        if ssid and ssid.text():
            self.ssid_edit.setText(ssid.text())
        if name and name.text():
            self.name_edit.setText(name.text())

    def open_profile_editor(self) -> None:
        self.profile_dialog.open()
        self.profile_dialog.raise_()
        self.profile_dialog.activateWindow()

    def close_profile_editor(self) -> None:
        self.profile_dialog.hide()

    def cancel_profile_editor(self) -> None:
        self.reset_profile_form()
        self.close_profile_editor()

    def reset_profile_form(self) -> None:
        self.editing_connection_id = None
        self.ssid_edit.clear()
        self.name_edit.clear()
        self.password_edit.clear()
        self.priority_spin.setValue(0)
        self.open_network_checkbox.setChecked(False)
        self.hidden_checkbox.setChecked(False)
        self.autoconnect_checkbox.setChecked(True)
        self.activate_checkbox.setChecked(True)
        self.replace_checkbox.setChecked(False)
        self.ssid_edit.setReadOnly(False)
        self.save_button.setText("Save Profile")

    def _handle_open_network_toggled(self, checked: bool) -> None:
        self.password_edit.setEnabled(not checked)
        if checked:
            self.password_edit.clear()

    def open_editor_for_other_network(self) -> None:
        self.reset_profile_form()
        self.profile_dialog.setWindowTitle("Add Network")
        self.open_profile_editor()
        self.ssid_edit.setFocus()

    def _network_is_open(self, security: str) -> bool:
        normalized = security.strip().lower()
        return normalized in {"", "--", "none", "open"}

    def _network_ssid(self, network: dict[str, Any]) -> str:
        ssid = str(network.get("ssid", "")).strip()
        return "" if ssid in {"", "--"} else ssid

    def _visible_scan_networks(self, networks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique_networks: dict[str, dict[str, Any]] = {}
        for network in networks:
            ssid = self._network_ssid(network)
            if not ssid:
                continue

            current_signal = parse_int(network.get("signal"), default=0)
            existing = unique_networks.get(ssid)
            if existing is None or current_signal > parse_int(existing.get("signal"), default=0):
                unique_networks[ssid] = network

        return list(unique_networks.values())

    def _rebuild_tray_networks_menu(self) -> None:
        if self.tray_networks_menu is None:
            return

        self.tray_networks_menu.clear()
        if not self.current_networks:
            placeholder = self.tray_networks_menu.addAction("No scan data yet")
            placeholder.setEnabled(False)
            self.tray_networks_menu.addSeparator()
            scan_action = self.tray_networks_menu.addAction("Scan Networks")
            scan_action.triggered.connect(self.scan_networks)
            return

        sorted_networks = sorted(
            self._visible_scan_networks(self.current_networks),
            key=lambda item: parse_int(item.get("signal"), default=0),
            reverse=True,
        )

        for network in sorted_networks:
            ssid = self._network_ssid(network)
            signal = parse_int(network.get("signal"), default=0)
            security = str(network.get("security", ""))
            mode = "open" if self._network_is_open(security) else "secured"
            in_use = bool(str(network.get("in_use", "")).strip())
            active = "* " if in_use else ""
            label = f"{active}{ssid} ({signal}%, {mode})"
            action = self.tray_networks_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, selected_network=network: self.connect_from_tray(selected_network)
            )

        self.tray_networks_menu.addSeparator()
        rescan_action = self.tray_networks_menu.addAction("Rescan")
        rescan_action.triggered.connect(self.scan_networks)

    def refresh_tray_networks(self) -> None:
        def apply(networks: list[dict[str, Any]]) -> None:
            self.current_networks = networks
            self._rebuild_tray_networks_menu()
            self.status_bar.showMessage(f"Tray scan complete: {len(networks)} network(s)", 3000)

        self._run_task(self.api.scan, apply, progress_message="Scanning networks for tray menu...")

    def _find_connection_for_ssid(self, ssid: str) -> dict[str, Any] | None:
        for connection in self.current_connections:
            details = connection.get("details", {})
            if not isinstance(details, dict):
                details = {}
            connection_ssid = str(
                details.get("ssid")
                or connection.get("ssid")
                or connection.get("802_11_wireless_ssid")
                or ""
            )
            if connection_ssid == ssid:
                return connection
        return None

    def connect_from_tray(self, network: dict[str, Any]) -> None:
        ssid = str(network.get("ssid", "")).strip()
        if not ssid:
            return

        existing = self._find_connection_for_ssid(ssid)
        if existing:
            identifier = str(
                existing.get("uuid")
                or existing.get("connection_uuid")
                or existing.get("name")
                or existing.get("connection_id")
                or ""
            )
            if not identifier:
                self.status_bar.showMessage("Unable to determine saved profile identifier", 5000)
                return

            def apply_activate(_connection: dict[str, Any]) -> None:
                self.status_bar.showMessage(f"Connected to {ssid}", 4000)
                self.refresh_all()

            self._run_task(
                lambda: self.api.activate_connection(identifier),
                apply_activate,
                progress_message=f"Connecting to {ssid}...",
            )
            return

        security = str(network.get("security", ""))
        password = ""
        if not self._network_is_open(security):
            password_value, accepted = QInputDialog.getText(
                self,
                "Wi-Fi Password",
                f"Enter password for {ssid}",
                QLineEdit.Password,
            )
            if not accepted:
                return
            password = password_value

        payload = {
            "ssid": ssid,
            "name": ssid,
            "password": password,
            "hidden": False,
            "autoconnect": True,
            "activate": True,
            "replace_existing": True,
            "priority": 0,
        }

        def apply_add(_connection: dict[str, Any]) -> None:
            self.status_bar.showMessage(f"Connected to {ssid}", 4000)
            self.refresh_all()

        self._run_task(
            lambda: self.api.add_connection(payload),
            apply_add,
            progress_message=f"Connecting to {ssid}...",
        )

    def open_editor_from_selected_network(self, *_args: Any) -> None:
        ssid = self.selected_scan_ssid()
        if not ssid:
            QMessageBox.information(self, "No Selection", "Select a Wi-Fi network first.")
            return
        self.reset_profile_form()
        self.profile_dialog.setWindowTitle("Add Network")
        self.open_profile_editor()
        self.populate_profile_from_network(ssid)

    def populate_profile_from_network(self, ssid: str) -> None:
        self.ssid_edit.setText(ssid)
        self.name_edit.setText(ssid)
        security_text = ""
        for network in self.current_networks:
            if str(network.get("ssid", "")) == ssid:
                security_text = str(network.get("security", ""))
                break
        self.open_network_checkbox.setChecked(self._network_is_open(security_text))

    def open_editor_from_selected_connection(self, *_args: Any) -> None:
        identifier = self.selected_connection_id()
        if not identifier:
            QMessageBox.information(self, "No Selection", "Select a known connection first.")
            return

        def apply(connection: dict[str, Any]) -> None:
            self.populate_profile_from_connection(connection)
            self.open_profile_editor()
            self.status_bar.showMessage("Loaded connection for editing", 4000)

        self._run_task(
            lambda: self.api.get_connection(identifier),
            apply,
            progress_message="Loading saved profile...",
        )

    def populate_profile_from_connection(self, connection: dict[str, Any]) -> None:
        self.reset_profile_form()
        details = connection.get("details", {})
        if not isinstance(details, dict):
            details = {}

        ssid = str(
            details.get("ssid")
            or connection.get("ssid")
            or connection.get("802_11_wireless_ssid")
            or ""
        )
        security = str(
            details.get("wifi_sec_key_mgmt")
            or connection.get("802_11_wireless_security_key_mgmt")
            or ""
        ).strip().lower()
        open_network = security in {"", "none"}
        hidden = parse_bool(
            details.get("hidden", connection.get("802_11_wireless_hidden")),
            default=False,
        )
        autoconnect = parse_bool(
            connection.get("autoconnect", connection.get("connection_autoconnect")),
            default=True,
        )
        priority = parse_int(connection.get("connection_autoconnect_priority"), default=0)
        connection_name = str(connection.get("name") or connection.get("connection_id") or "")
        connection_id = str(
            connection.get("uuid")
            or connection.get("connection_uuid")
            or connection.get("name")
            or connection.get("connection_id")
            or ""
        )

        self.editing_connection_id = connection_id
        self.ssid_edit.setText(ssid)
        self.name_edit.setText(connection_name or ssid)
        self.password_edit.clear()
        self.open_network_checkbox.setChecked(open_network)
        self.hidden_checkbox.setChecked(hidden)
        self.autoconnect_checkbox.setChecked(autoconnect)
        self.priority_spin.setValue(priority)
        self.activate_checkbox.setChecked(True)
        self.replace_checkbox.setChecked(True)
        self.save_button.setText("Update Profile")
        self.profile_dialog.setWindowTitle("Edit Known Connection")

    def populate_scan_table(self, networks: list[dict[str, Any]]) -> None:
        visible_networks = self._visible_scan_networks(networks)
        self.current_networks = visible_networks
        self.scan_table.setRowCount(len(visible_networks))
        for row, network in enumerate(visible_networks):
            values = [
                str(network.get("in_use", "")),
                self._network_ssid(network),
                str(network.get("signal", "")),
                str(network.get("security", "")),
                str(network.get("bssid", "")),
                str(network.get("mode", "")),
                str(network.get("chan", "")),
                str(network.get("rate", "")),
            ]
            for column, value in enumerate(values):
                self.scan_table.setItem(row, column, QTableWidgetItem(value))
        self.scan_table.sortItems(2, Qt.DescendingOrder)

    def populate_connections(self, connections: list[dict[str, Any]]) -> None:
        self.current_connections = connections
        self.connections_table.setRowCount(len(connections))
        for row, connection in enumerate(connections):
            details = connection.get("details", {})
            values = [
                str(connection.get("name", "")),
                str(connection.get("uuid", "")),
                str(details.get("ssid", "")),
                str(connection.get("device", "")),
                bool_text(connection.get("autoconnect")),
                str(details.get("general_state", "")),
            ]
            for column, value in enumerate(values):
                self.connections_table.setItem(row, column, QTableWidgetItem(value))

    def update_status_summary(self, status: dict[str, Any]) -> None:
        general = status.get("general", {})
        devices = status.get("devices", {})
        wifi_device = devices.get("wlan0", {})
        usb_device = devices.get("usb0", {})

        lines = [
            f"Hostname: {status.get('hostname', '')}",
            f"Platform: {status.get('platform', '')}",
            f"Raspberry Pi model: {status.get('rpi_model', '')}",
            f"NetworkManager state: {general.get('state', '')}",
            f"Connectivity: {general.get('connectivity', '')}",
            f"Wi-Fi enabled: {bool_text(status.get('wifi_radio', {}).get('enabled', False))}",
            "",
            "wlan0:",
            f"  state: {wifi_device.get('general_state', '')}",
            f"  connection: {wifi_device.get('general_connection', '')}",
            f"  address: {wifi_device.get('ip4_address_1', '')}",
            f"  gateway: {wifi_device.get('ip4_gateway', '')}",
            "",
            "usb0:",
            f"  state: {usb_device.get('general_state', '')}",
            f"  connection: {usb_device.get('general_connection', '')}",
            f"  address: {usb_device.get('ip4_address_1', '')}",
            f"  gateway: {usb_device.get('ip4_gateway', '')}",
            "",
            "Active connections:",
        ]
        for connection in status.get("active_connections", []):
            lines.append(
                f"  - {connection.get('name', '')} ({connection.get('type', '')}) on {connection.get('device', '')}"
            )
        self.status_summary.setPlainText("\n".join(lines))


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
