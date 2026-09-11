#!/usr/bin/env python3
"""
USBTreeView-style USB topology viewer for Linux, built with PySide6.

Features:
- Reads Linux USB topology from /sys/bus/usb/devices.
- Displays a hierarchical USB tree: root hubs, hubs, devices, and interfaces.
- Shows device, interface, driver, endpoint, power, speed, descriptor, and udev details.
- Optional lsusb -v descriptor capture when usbutils is installed.
- Optional udevadm info capture when systemd/udev tools are installed.
- Live auto-refresh with change detection.
- Search/filter.
- Copy selected details to clipboard.
- Export full report as text or JSON.
- Designed to run without root, but lsusb -v may show more information with elevated permissions.

Install:
    python3 -m pip install PySide6

Useful optional packages:
    sudo apt install usbutils systemd

Run:
    python3 usb_tree_viewer_linux_pyside6.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from PySide6.QtCore import QTimer, Qt, QSize
from PySide6.QtGui import QAction, QFont, QIcon, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTextEdit,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

SYS_USB = Path("/sys/bus/usb/devices")


DEVICE_ATTRS = [
    "busnum",
    "devnum",
    "devpath",
    "idVendor",
    "idProduct",
    "bcdDevice",
    "bDeviceClass",
    "bDeviceSubClass",
    "bDeviceProtocol",
    "bMaxPacketSize0",
    "bNumConfigurations",
    "bConfigurationValue",
    "bMaxPower",
    "bmAttributes",
    "authorized",
    "avoid_reset_quirk",
    "configuration",
    "manufacturer",
    "product",
    "serial",
    "speed",
    "version",
    "urbnum",
    "ltm_capable",
    "removable",
    "rx_lanes",
    "tx_lanes",
    "maxchild",
]

INTERFACE_ATTRS = [
    "bInterfaceNumber",
    "bAlternateSetting",
    "bNumEndpoints",
    "bInterfaceClass",
    "bInterfaceSubClass",
    "bInterfaceProtocol",
    "interface",
    "modalias",
    "supports_autosuspend",
]

CLASS_NAMES = {
    "00": "Defined at Interface level",
    "01": "Audio",
    "02": "Communications and CDC Control",
    "03": "Human Interface Device",
    "05": "Physical",
    "06": "Image",
    "07": "Printer",
    "08": "Mass Storage",
    "09": "Hub",
    "0a": "CDC Data",
    "0b": "Smart Card",
    "0d": "Content Security",
    "0e": "Video",
    "0f": "Personal Healthcare",
    "10": "Audio/Video",
    "11": "Billboard",
    "12": "USB Type-C Bridge",
    "dc": "Diagnostic",
    "e0": "Wireless Controller",
    "ef": "Miscellaneous",
    "fe": "Application Specific",
    "ff": "Vendor Specific",
}

SPEED_NAMES = {
    "1.5": "Low Speed, 1.5 Mb/s",
    "12": "Full Speed, 12 Mb/s",
    "480": "High Speed, 480 Mb/s",
    "5000": "SuperSpeed, 5 Gb/s",
    "10000": "SuperSpeedPlus, 10 Gb/s",
    "20000": "SuperSpeedPlus, 20 Gb/s",
}


@dataclass
class UsbInterface:
    sys_name: str
    sys_path: str
    attrs: Dict[str, str] = field(default_factory=dict)
    driver: str = ""
    endpoints: List[Dict[str, str]] = field(default_factory=list)

    @property
    def interface_number(self) -> str:
        return self.attrs.get("bInterfaceNumber", "")

    @property
    def title(self) -> str:
        num = self.interface_number or "?"
        cls = describe_class(self.attrs.get("bInterfaceClass", ""))
        label = self.attrs.get("interface", "")
        drv = f" [{self.driver}]" if self.driver else ""
        if label:
            return f"Interface {num}: {label} ({cls}){drv}"
        return f"Interface {num}: {cls}{drv}"


@dataclass
class UsbNode:
    sys_name: str
    sys_path: str
    attrs: Dict[str, str] = field(default_factory=dict)
    driver: str = ""
    parent_name: Optional[str] = None
    children: List["UsbNode"] = field(default_factory=list)
    interfaces: List[UsbInterface] = field(default_factory=list)
    udev_info: str = ""
    lsusb_verbose: str = ""

    @property
    def busnum(self) -> str:
        return pad_num(self.attrs.get("busnum", ""), 3)

    @property
    def devnum(self) -> str:
        return pad_num(self.attrs.get("devnum", ""), 3)

    @property
    def vid(self) -> str:
        return self.attrs.get("idVendor", "")

    @property
    def pid(self) -> str:
        return self.attrs.get("idProduct", "")

    @property
    def is_root_hub(self) -> bool:
        return bool(re.fullmatch(r"usb\d+", self.sys_name))

    @property
    def is_hub(self) -> bool:
        return self.attrs.get("bDeviceClass", "").lower() == "09" or int_or_none(self.attrs.get("maxchild")) not in (None, 0)

    @property
    def display_name(self) -> str:
        product = self.attrs.get("product", "")
        manufacturer = self.attrs.get("manufacturer", "")
        serial = self.attrs.get("serial", "")
        cls = describe_class(self.attrs.get("bDeviceClass", ""))
        speed = self.attrs.get("speed", "")
        speed_text = SPEED_NAMES.get(speed, f"{speed} Mb/s" if speed else "")

        if self.is_root_hub:
            label = product or f"USB Root Hub {self.sys_name.replace('usb', '')}"
        elif product:
            label = product
        elif manufacturer:
            label = manufacturer
        elif self.vid and self.pid:
            label = f"USB Device {self.vid}:{self.pid}"
        else:
            label = self.sys_name

        extras = []
        if self.vid and self.pid:
            extras.append(f"{self.vid}:{self.pid}")
        if cls and cls != "Defined at Interface level":
            extras.append(cls)
        if speed_text:
            extras.append(speed_text)
        if serial and not self.is_root_hub:
            extras.append(f"S/N {serial}")
        suffix = f" — {' | '.join(extras)}" if extras else ""
        return f"{label}{suffix}"


def pad_num(value: str, width: int) -> str:
    try:
        return f"{int(value):0{width}d}"
    except Exception:
        return value


def int_or_none(value: Optional[str]) -> Optional[int]:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except Exception:
        return None


def read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace").strip()
    except Exception:
        return ""


def read_attrs(path: Path, names: Iterable[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in names:
        value = read_text(path / name)
        if value != "":
            out[name] = value
    return out


def symlink_target_name(path: Path) -> str:
    try:
        if path.is_symlink():
            return path.resolve().name
    except Exception:
        pass
    return ""


def describe_class(code: str) -> str:
    c = code.strip().lower()
    if not c:
        return ""
    return CLASS_NAMES.get(c, f"Class 0x{c}")


def run_command(args: List[str], timeout: float = 4.0) -> str:
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.stdout.strip()
    except FileNotFoundError:
        return ""
    except subprocess.TimeoutExpired:
        return "Command timed out."
    except Exception as exc:
        return f"Command failed: {exc}"


def likely_usb_device_dir(path: Path) -> bool:
    return path.is_dir() and (path / "busnum").exists() and (path / "devnum").exists()


def likely_usb_interface_dir(path: Path) -> bool:
    return path.is_dir() and ":" in path.name and (path / "bInterfaceNumber").exists()


def parent_device_name_for(sys_name: str) -> Optional[str]:
    # Linux USB device names look like:
    #   usb1           root hub
    #   1-2            port 2 on bus 1
    #   1-2.3          port 3 behind device 1-2
    #   1-2.3.4        port 4 behind device 1-2.3
    if re.fullmatch(r"usb\d+", sys_name):
        return None
    if "-" not in sys_name:
        return None
    bus, rest = sys_name.split("-", 1)
    if "." in rest:
        return f"{bus}-{rest.rsplit('.', 1)[0]}"
    return f"usb{bus}"


def collect_endpoints(interface_path: Path) -> List[Dict[str, str]]:
    endpoints: List[Dict[str, str]] = []
    for child in sorted(interface_path.iterdir(), key=lambda p: p.name):
        if child.is_dir() and child.name.startswith("ep_"):
            endpoints.append(
                {
                    "name": child.name,
                    "address": read_text(child / "bEndpointAddress"),
                    "attributes": read_text(child / "bmAttributes"),
                    "interval": read_text(child / "bInterval"),
                    "length": read_text(child / "bLength"),
                    "max_packet_size": read_text(child / "wMaxPacketSize"),
                    "type": read_text(child / "type"),
                    "direction": read_text(child / "direction"),
                }
            )
    return endpoints


def collect_usb_topology(include_slow_details: bool = False) -> List[UsbNode]:
    nodes: Dict[str, UsbNode] = {}

    if not SYS_USB.exists():
        return []

    for entry in sorted(SYS_USB.iterdir(), key=lambda p: natural_key(p.name)):
        if likely_usb_device_dir(entry):
            node = UsbNode(
                sys_name=entry.name,
                sys_path=str(entry),
                attrs=read_attrs(entry, DEVICE_ATTRS),
                driver=symlink_target_name(entry / "driver"),
                parent_name=parent_device_name_for(entry.name),
            )
            nodes[node.sys_name] = node

    for entry in sorted(SYS_USB.iterdir(), key=lambda p: natural_key(p.name)):
        if likely_usb_interface_dir(entry):
            parent_name = entry.name.split(":", 1)[0]
            parent = nodes.get(parent_name)
            iface = UsbInterface(
                sys_name=entry.name,
                sys_path=str(entry),
                attrs=read_attrs(entry, INTERFACE_ATTRS),
                driver=symlink_target_name(entry / "driver"),
                endpoints=collect_endpoints(entry),
            )
            if parent:
                parent.interfaces.append(iface)

    roots: List[UsbNode] = []
    for node in nodes.values():
        parent = nodes.get(node.parent_name or "")
        if parent:
            parent.children.append(node)
        else:
            roots.append(node)

    for node in nodes.values():
        node.children.sort(key=lambda n: natural_key(n.sys_name))
        node.interfaces.sort(key=lambda i: natural_key(i.sys_name))

    roots.sort(key=lambda n: natural_key(n.sys_name))

    if include_slow_details:
        add_external_details(nodes.values())

    return roots


def add_external_details(nodes: Iterable[UsbNode]) -> None:
    have_lsusb = shutil.which("lsusb") is not None
    have_udevadm = shutil.which("udevadm") is not None
    for node in nodes:
        if have_udevadm:
            node.udev_info = run_command(["udevadm", "info", "--query=all", "--path", node.sys_path], timeout=3.0)
        if have_lsusb and node.busnum and node.devnum:
            node.lsusb_verbose = run_command(["lsusb", "-v", "-s", f"{node.busnum}:{node.devnum}"], timeout=6.0)


def natural_key(text: str) -> List[Any]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", text)]


def flatten_nodes(nodes: Iterable[UsbNode]) -> List[UsbNode]:
    out: List[UsbNode] = []
    for node in nodes:
        out.append(node)
        out.extend(flatten_nodes(node.children))
    return out


def topology_signature(nodes: List[UsbNode]) -> str:
    simple = []
    for node in flatten_nodes(nodes):
        simple.append(
            {
                "name": node.sys_name,
                "busnum": node.attrs.get("busnum", ""),
                "devnum": node.attrs.get("devnum", ""),
                "vid": node.attrs.get("idVendor", ""),
                "pid": node.attrs.get("idProduct", ""),
                "product": node.attrs.get("product", ""),
                "serial": node.attrs.get("serial", ""),
                "children": [child.sys_name for child in node.children],
                "interfaces": [iface.sys_name for iface in node.interfaces],
            }
        )
    return json.dumps(simple, sort_keys=True)


def endpoint_summary(ep: Dict[str, str]) -> str:
    pieces = [ep.get("name", "endpoint")]
    if ep.get("direction"):
        pieces.append(ep["direction"])
    if ep.get("type"):
        pieces.append(ep["type"])
    if ep.get("address"):
        pieces.append(f"addr {ep['address']}")
    if ep.get("max_packet_size"):
        pieces.append(f"max packet {ep['max_packet_size']}")
    return " | ".join(pieces)


def node_to_dict(node: UsbNode) -> Dict[str, Any]:
    return {
        "sys_name": node.sys_name,
        "sys_path": node.sys_path,
        "driver": node.driver,
        "parent_name": node.parent_name,
        "attrs": node.attrs,
        "interfaces": [asdict(i) for i in node.interfaces],
        "children": [node_to_dict(c) for c in node.children],
        "udev_info": node.udev_info,
        "lsusb_verbose": node.lsusb_verbose,
    }


def report_for_node(node: UsbNode) -> str:
    lines: List[str] = []
    lines.append(node.display_name)
    lines.append("=" * len(node.display_name))
    lines.append("")
    lines.append(f"Sysfs name: {node.sys_name}")
    lines.append(f"Sysfs path: {node.sys_path}")
    if node.driver:
        lines.append(f"Driver: {node.driver}")
    if node.parent_name:
        lines.append(f"Parent: {node.parent_name}")
    lines.append("")

    lines.append("Device attributes")
    lines.append("-----------------")
    if node.attrs:
        for key in sorted(node.attrs):
            value = node.attrs[key]
            if key == "bDeviceClass":
                value = f"{value} ({describe_class(value)})"
            elif key == "speed":
                value = f"{value} ({SPEED_NAMES.get(value, 'Mb/s')})"
            lines.append(f"{key}: {value}")
    else:
        lines.append("No readable device attributes.")
    lines.append("")

    if node.interfaces:
        lines.append("Interfaces")
        lines.append("----------")
        for iface in node.interfaces:
            lines.append(iface.title)
            lines.append(f"  Sysfs path: {iface.sys_path}")
            for key in sorted(iface.attrs):
                value = iface.attrs[key]
                if key == "bInterfaceClass":
                    value = f"{value} ({describe_class(value)})"
                lines.append(f"  {key}: {value}")
            if iface.driver:
                lines.append(f"  driver: {iface.driver}")
            if iface.endpoints:
                lines.append("  Endpoints:")
                for ep in iface.endpoints:
                    lines.append(f"    - {endpoint_summary(ep)}")
                    for key in sorted(k for k in ep if k != "name" and ep[k]):
                        lines.append(f"      {key}: {ep[key]}")
            lines.append("")

    if node.udev_info:
        lines.append("udevadm info")
        lines.append("------------")
        lines.append(node.udev_info)
        lines.append("")

    if node.lsusb_verbose:
        lines.append("lsusb -v")
        lines.append("--------")
        lines.append(node.lsusb_verbose)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def full_text_report(nodes: List[UsbNode]) -> str:
    lines = ["Linux USB Topology Report", f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for node in flatten_nodes(nodes):
        lines.append(report_for_node(node))
        lines.append("\n" + "#" * 80 + "\n")
    return "\n".join(lines)


class UsbTreeWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("USB Tree Viewer for Linux")
        self.resize(1250, 800)
        self.roots: List[UsbNode] = []
        self.current_signature = ""
        self.include_slow_details = True

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["USB topology", "Bus", "Dev", "VID:PID", "Driver", "Speed"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        for idx in range(1, 6):
            self.tree.header().setSectionResizeMode(idx, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self.update_detail_for_selection)
        self.tree.itemDoubleClicked.connect(lambda *_: self.copy_selected_path())

        self.details_tabs = QTabWidget()
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setFont(QFont("monospace"))
        self.raw_text = QTextEdit()
        self.raw_text.setReadOnly(True)
        self.raw_text.setFont(QFont("monospace"))
        self.lsusb_text = QTextEdit()
        self.lsusb_text.setReadOnly(True)
        self.lsusb_text.setFont(QFont("monospace"))
        self.udev_text = QTextEdit()
        self.udev_text.setReadOnly(True)
        self.udev_text.setFont(QFont("monospace"))

        self.details_tabs.addTab(self.summary_text, "Summary")
        self.details_tabs.addTab(self.raw_text, "Raw sysfs")
        self.details_tabs.addTab(self.lsusb_text, "lsusb -v")
        self.details_tabs.addTab(self.udev_text, "udev")

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.details_tabs)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Filter by name, vendor ID, product ID, serial, driver, class, interface...")
        self.search_box.textChanged.connect(self.apply_filter)

        # State flags (moved to menu bar)
        self.auto_refresh_enabled = True
        self.include_slow_details = True

        top = QWidget()
        layout = QVBoxLayout(top)
        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("Search:"))
        control_row.addWidget(self.search_box, 1)
        
        layout.addLayout(control_row)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(top)

        self.make_menus()
        self.setStatusBar(QStatusBar())

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_if_changed)
        self.timer.start(1500)

        self.refresh(force=True)

    def make_menus(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        view_menu = menubar.addMenu("&View")
        tools_menu = menubar.addMenu("&Tools")
        help_menu = menubar.addMenu("&Help")

        refresh_action = QAction("Refresh", self)
        refresh_action.setShortcut(QKeySequence.Refresh)
        refresh_action.triggered.connect(lambda: self.refresh(force=True))
        file_menu.addAction(refresh_action)

        export_txt_action = QAction("Export TXT", self)
        export_txt_action.triggered.connect(self.export_text)
        file_menu.addAction(export_txt_action)

        export_json_action = QAction("Export JSON", self)
        export_json_action.triggered.connect(self.export_json)
        file_menu.addAction(export_json_action)

        file_menu.addSeparator()

        exit_action = QAction("Exit", self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        expand_action = QAction("Expand all", self)
        expand_action.triggered.connect(self.tree.expandAll)
        view_menu.addAction(expand_action)

        collapse_action = QAction("Collapse all", self)
        collapse_action.triggered.connect(self.tree.collapseAll)
        view_menu.addAction(collapse_action)

        
        toggle_auto = QAction("Auto-refresh", self, checkable=True)
        toggle_auto.setChecked(True)
        toggle_auto.triggered.connect(lambda v: setattr(self, 'auto_refresh_enabled', v))
        view_menu.addAction(toggle_auto)

        toggle_lsusb = QAction("Include lsusb/udev details", self, checkable=True)
        toggle_lsusb.setChecked(True)
        def _toggle_lsusb(v):
            self.include_slow_details = v
            self.refresh(force=True)
        toggle_lsusb.triggered.connect(_toggle_lsusb)
        view_menu.addAction(toggle_lsusb)

        copy_action = QAction("Copy details", self)
        copy_action.setShortcut(QKeySequence.Copy)
        copy_action.triggered.connect(self.copy_selected_details)
        tools_menu.addAction(copy_action)

        copy_path_action = QAction("Copy sysfs path", self)
        copy_path_action.triggered.connect(self.copy_selected_path)
        tools_menu.addAction(copy_path_action)

        about_action = QAction("About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)

        about_qt_action = QAction("About Qt", self)
        about_qt_action.triggered.connect(QApplication.instance().aboutQt)
        help_menu.addAction(about_qt_action)

    

    def refresh_if_changed(self) -> None:
        if not self.auto_refresh_enabled:
            return
        roots = collect_usb_topology(include_slow_details=False)
        sig = topology_signature(roots)
        if sig != self.current_signature:
            self.refresh(force=True)

    def refresh(self, force: bool = False) -> None:
        old_selected = self.selected_sys_path()
        self.statusBar().showMessage("Scanning USB topology...")
        QApplication.processEvents()
        roots = collect_usb_topology(include_slow_details=self.include_slow_details)
        sig = topology_signature(roots)
        if not force and sig == self.current_signature:
            self.statusBar().showMessage("No USB changes detected.", 2500)
            return
        self.roots = roots
        self.current_signature = sig
        self.rebuild_tree(old_selected)
        self.statusBar().showMessage(f"Found {len(flatten_nodes(self.roots))} USB devices.", 3500)

    def rebuild_tree(self, restore_path: Optional[str] = None) -> None:
        self.tree.clear()
        for node in self.roots:
            self.add_node_item(None, node)
        self.tree.expandToDepth(1)
        self.apply_filter()
        if restore_path:
            found = self.find_item_by_path(restore_path)
            if found:
                self.tree.setCurrentItem(found)
        if not self.tree.currentItem() and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))

    def add_node_item(self, parent_item: Optional[QTreeWidgetItem], node: UsbNode) -> QTreeWidgetItem:
        item = QTreeWidgetItem(
            [
                node.display_name,
                node.busnum,
                node.devnum,
                f"{node.vid}:{node.pid}" if node.vid and node.pid else "",
                node.driver,
                SPEED_NAMES.get(node.attrs.get("speed", ""), node.attrs.get("speed", "")),
            ]
        )
        item.setData(0, Qt.UserRole, ("device", node.sys_path))
        if node.is_root_hub:
            item.setText(0, "Root Hub: " + item.text(0))
        elif node.is_hub:
            item.setText(0, "Hub: " + item.text(0))

        if parent_item is None:
            self.tree.addTopLevelItem(item)
        else:
            parent_item.addChild(item)

        for iface in node.interfaces:
            iface_item = QTreeWidgetItem([iface.title, "", "", "", iface.driver, ""])
            iface_item.setData(0, Qt.UserRole, ("interface", iface.sys_path))
            item.addChild(iface_item)
            for ep in iface.endpoints:
                ep_item = QTreeWidgetItem(["Endpoint: " + endpoint_summary(ep), "", "", "", "", ""])
                ep_item.setData(0, Qt.UserRole, ("endpoint", iface.sys_path, ep.get("name", "")))
                iface_item.addChild(ep_item)

        for child in node.children:
            self.add_node_item(item, child)
        return item

    def find_item_by_path(self, sys_path: str) -> Optional[QTreeWidgetItem]:
        def walk(item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
            data = item.data(0, Qt.UserRole)
            if data and len(data) >= 2 and data[1] == sys_path:
                return item
            for i in range(item.childCount()):
                found = walk(item.child(i))
                if found:
                    return found
            return None

        for i in range(self.tree.topLevelItemCount()):
            found = walk(self.tree.topLevelItem(i))
            if found:
                return found
        return None

    def selected_sys_path(self) -> Optional[str]:
        item = self.tree.currentItem()
        if not item:
            return None
        data = item.data(0, Qt.UserRole)
        if data and len(data) >= 2:
            return data[1]
        return None

    def selected_object(self) -> Tuple[str, Any]:
        item = self.tree.currentItem()
        if not item:
            return "", None
        data = item.data(0, Qt.UserRole)
        if not data:
            return "", None
        kind = data[0]
        path = data[1]
        ep_name = data[2] if len(data) > 2 else ""
        for node in flatten_nodes(self.roots):
            if kind == "device" and node.sys_path == path:
                return kind, node
            for iface in node.interfaces:
                if kind == "interface" and iface.sys_path == path:
                    return kind, iface
                if kind == "endpoint" and iface.sys_path == path:
                    for ep in iface.endpoints:
                        if ep.get("name") == ep_name:
                            return kind, ep
        return "", None

    def update_detail_for_selection(self) -> None:
        kind, obj = self.selected_object()
        if not obj:
            self.summary_text.clear()
            self.raw_text.clear()
            self.lsusb_text.clear()
            self.udev_text.clear()
            return

        if kind == "device":
            node: UsbNode = obj
            self.summary_text.setPlainText(report_for_node(node))
            self.raw_text.setPlainText(json.dumps(node_to_dict(node), indent=2, sort_keys=True))
            self.lsusb_text.setPlainText(node.lsusb_verbose or "Enable 'Include lsusb/udev details' and refresh, or install usbutils.")
            self.udev_text.setPlainText(node.udev_info or "Enable 'Include lsusb/udev details' and refresh, or install udevadm/systemd tools.")
        elif kind == "interface":
            iface: UsbInterface = obj
            self.summary_text.setPlainText(self.interface_report(iface))
            self.raw_text.setPlainText(json.dumps(asdict(iface), indent=2, sort_keys=True))
            self.lsusb_text.setPlainText("Select the parent device to see lsusb output.")
            self.udev_text.setPlainText(run_command(["udevadm", "info", "--query=all", "--path", iface.sys_path]) if shutil.which("udevadm") else "udevadm not found.")
        elif kind == "endpoint":
            ep: Dict[str, str] = obj
            self.summary_text.setPlainText(json.dumps(ep, indent=2, sort_keys=True))
            self.raw_text.setPlainText(json.dumps(ep, indent=2, sort_keys=True))
            self.lsusb_text.clear()
            self.udev_text.clear()

    def interface_report(self, iface: UsbInterface) -> str:
        lines = [iface.title, "=" * len(iface.title), "", f"Sysfs name: {iface.sys_name}", f"Sysfs path: {iface.sys_path}"]
        if iface.driver:
            lines.append(f"Driver: {iface.driver}")
        lines.append("")
        for key in sorted(iface.attrs):
            value = iface.attrs[key]
            if key == "bInterfaceClass":
                value = f"{value} ({describe_class(value)})"
            lines.append(f"{key}: {value}")
        if iface.endpoints:
            lines.append("")
            lines.append("Endpoints")
            lines.append("---------")
            for ep in iface.endpoints:
                lines.append(endpoint_summary(ep))
                for key in sorted(k for k in ep if k != "name" and ep[k]):
                    lines.append(f"  {key}: {ep[key]}")
        return "\n".join(lines) + "\n"

    def apply_filter(self) -> None:
        needle = self.search_box.text().strip().lower()

        def item_matches(item: QTreeWidgetItem) -> bool:
            text = " ".join(item.text(i) for i in range(item.columnCount())).lower()
            data = item.data(0, Qt.UserRole)
            if data:
                text += " " + " ".join(str(x).lower() for x in data)
            direct = needle in text if needle else True
            child_match = False
            for i in range(item.childCount()):
                if item_matches(item.child(i)):
                    child_match = True
            visible = direct or child_match
            item.setHidden(not visible)
            if child_match and needle:
                item.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            item_matches(self.tree.topLevelItem(i))

    def copy_selected_details(self) -> None:
        text = self.summary_text.toPlainText()
        QApplication.clipboard().setText(text)
        self.statusBar().showMessage("Copied selected details.", 2000)

    def copy_selected_path(self) -> None:
        path = self.selected_sys_path() or ""
        if path:
            QApplication.clipboard().setText(path)
            self.statusBar().showMessage("Copied sysfs path.", 2000)

    def export_text(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export USB report", "usb-report.txt", "Text files (*.txt);;All files (*)")
        if not filename:
            return
        try:
            Path(filename).write_text(full_text_report(self.roots), encoding="utf-8")
            self.statusBar().showMessage(f"Exported {filename}", 3000)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def export_json(self) -> None:
        filename, _ = QFileDialog.getSaveFileName(self, "Export USB topology JSON", "usb-topology.json", "JSON files (*.json);;All files (*)")
        if not filename:
            return
        try:
            data = {
                "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                "devices": [node_to_dict(n) for n in self.roots],
            }
            Path(filename).write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
            self.statusBar().showMessage(f"Exported {filename}", 3000)
        except Exception as exc:
            QMessageBox.critical(self, "Export failed", str(exc))

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            "About USB Tree Viewer for Linux",
            "A PySide6 USB topology viewer using Linux sysfs, with optional lsusb and udevadm details.\n\n"
            "It is inspired by USBTreeView, but uses Linux-native data sources. Some descriptor details depend on kernel permissions and installed tools.",
        )


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("USB Tree Viewer for Linux")
    app.setOrganizationName("Open USB Tools")

    if not SYS_USB.exists():
        QMessageBox.critical(None, "Unsupported system", "/sys/bus/usb/devices was not found. This program needs Linux sysfs USB support.")
        return 1

    window = UsbTreeWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
