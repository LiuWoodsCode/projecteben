from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sys
from typing import Callable

from advanced_activity_led import ActivityLedWindow
from advanced_antenna import AntennaConfigurationWindow
from advanced_audio import AudioSettingsWindow
from advanced_boot_initramfs import BootInitramfsWindow
from advanced_boot_kernel import BootKernelWindow
from advanced_boot_misc import BootMiscWindow
from advanced_boot_videocore import BootVideoCoreWindow
from advanced_bt_wlan import BtWlanAddressWindow
from advanced_fan_curve import FanCurveEditorWindow
from advanced_hw_features import HardwareFeaturesWindow
from advanced_overclock import OverclockConfigWindow
from advanced_power_button import PowerButtonSettingsWindow
from validator import ConfigSyntaxHighlighter, ValidationMessage, validate_config

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QCloseEvent,
    QFont,
    QKeySequence,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
    QDockWidget,
)


APP_NAME = "Raspberry Pi Config Studio"
DOCS_PATH = Path(__file__).with_name("configtxt-docs.txt")


@dataclass(frozen=True)
class Snippet:
    title: str
    content: str
    description: str


SNIPPET_GROUPS: dict[str, list[Snippet]] = {
    "Basics": [
        Snippet(
            "Enable Audio",
            "dtparam=audio=on\n",
            "Loads the onboard audio driver.",
        ),
        Snippet(
            "Camera Auto Detect",
            "camera_auto_detect=1\n",
            "Automatically enables supported camera overlays.",
        ),
        Snippet(
            "Display Auto Detect",
            "display_auto_detect=1\n",
            "Automatically enables supported DSI display overlays.",
        ),
        Snippet(
            "VC4 KMS Graphics",
            "dtoverlay=vc4-kms-v3d\n",
            "Turns on the modern DRM graphics stack.",
        ),
    ],
    "Display": [
        Snippet(
            "Force HDMI Hotplug",
            "hdmi_force_hotplug=1\n",
            "Forces HDMI output even if a display is not detected at boot.",
        ),
        Snippet(
            "Safe HDMI Mode",
            "hdmi_safe=1\n",
            "Applies a conservative HDMI mode for troubleshooting.",
        ),
        Snippet(
            "Rotate Display 180",
            "display_rotate=2\n",
            "Rotates the primary display by 180 degrees.",
        ),
        Snippet(
            "Disable Overscan",
            "disable_overscan=1\n",
            "Disables overscan compensation.",
        ),
    ],
    "Performance": [
        Snippet(
            "GPU Memory 128 MB",
            "gpu_mem=128\n",
            "Allocates 128 MB of RAM to the GPU.",
        ),
        Snippet(
            "GPU Memory 256 MB",
            "gpu_mem=256\n",
            "Allocates 256 MB of RAM to the GPU.",
        ),
        Snippet(
            "ARM Frequency 2000",
            "arm_freq=2000\n",
            "Sets a fixed CPU frequency for supported models.",
        ),
        Snippet(
            "Force Turbo",
            "force_turbo=1\n",
            "Disables dynamic frequency scaling on supported platforms.",
        ),
    ],
    "Boot": [
        Snippet(
            "Boot Delay",
            "boot_delay=1\n",
            "Waits one second before continuing boot.",
        ),
        Snippet(
            "UART Enabled",
            "enable_uart=1\n",
            "Enables the serial console and UART hardware.",
        ),
        Snippet(
            "Custom Include",
            "include extraconfig.txt\n",
            "Includes another config file during boot.",
        ),
    ],
    "Conditionals": [
        Snippet(
            "All Devices",
            "[all]\n",
            "Applies settings to every Raspberry Pi boot target.",
        ),
        Snippet(
            "Pi 5 Only",
            "[pi5]\n",
            "Targets Raspberry Pi 5 hardware only.",
        ),
        Snippet(
            "Compute Module 4",
            "[cm4]\n",
            "Targets Compute Module 4 devices.",
        ),
        Snippet(
            "Tryboot",
            "[tryboot]\n",
            "Applies only when booting in tryboot mode.",
        ),
        Snippet(
            "No Match",
            "[none]\n",
            "Explicitly disables all settings until another filter appears.",
        ),
    ],
}


class CustomSettingDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Insert Custom Setting")
        self.setModal(True)

        self.key_edit = QLineEdit()
        self.value_edit = QLineEdit()
        self.comment_edit = QLineEdit()
        self.comment_edit.setPlaceholderText("Optional comment placed above the setting")

        form = QFormLayout()
        form.addRow("Property", self.key_edit)
        form.addRow("Value", self.value_edit)
        form.addRow("Comment", self.comment_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def snippet_text(self) -> str:
        key = self.key_edit.text().strip()
        value = self.value_edit.text().strip()
        comment = self.comment_edit.text().strip()

        lines: list[str] = []
        if comment:
            lines.append(f"# {comment}")
        lines.append(f"{key}={value}")
        return "\n".join(lines) + "\n"

    def accept(self) -> None:
        if not self.key_edit.text().strip():
            QMessageBox.warning(self, APP_NAME, "Enter a property name before inserting.")
            return
        super().accept()


class ValidationDock(QDockWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Validation", parent)
        self.list_widget = QListWidget()
        self.setWidget(self.list_widget)

    def set_messages(self, messages: list[ValidationMessage]) -> None:
        self.list_widget.clear()
        if not messages:
            item = QListWidgetItem("No validation issues found.")
            item.setData(Qt.UserRole, None)
            self.list_widget.addItem(item)
            return

        for message in messages:
            label = f"Line {message.line_number}: {message.severity.upper()} - {message.text}"
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, message.line_number)
            self.list_widget.addItem(item)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.current_path: Path | None = None
        self.find_text: str = ""
        self.advanced_windows: dict[str, QMainWindow] = {}

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText(
            "# Start with a blank Raspberry Pi config.txt file\n"
            "# Use the menu bar to insert common directives, conditionals, and notes."
        )
        self.editor.setTabStopDistance(24)
        self.editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.editor.document().modificationChanged.connect(self.on_modification_changed)
        self.editor.textChanged.connect(self.refresh_validation)

        mono = QFont("Menlo")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(12)
        self.editor.setFont(mono)
        self.highlighter = ConfigSyntaxHighlighter(self.editor.document())

        self.validation_dock = ValidationDock(self)
        self.validation_dock.list_widget.itemActivated.connect(self.jump_to_issue)

        self.docs_dock = QDockWidget("Reference Notes", self)
        self.docs_view = QPlainTextEdit()
        self.docs_view.setReadOnly(True)
        self.docs_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.docs_view.setPlainText(self.load_docs_text())
        self.docs_dock.setWidget(self.docs_view)

        splitter = QSplitter()
        splitter.addWidget(self.editor)
        splitter.setStretchFactor(0, 1)
        self.setCentralWidget(splitter)

        self.addDockWidget(Qt.RightDockWidgetArea, self.validation_dock)
        self.addDockWidget(Qt.BottomDockWidgetArea, self.docs_dock)

        self.setStatusBar(QStatusBar())
        self.build_menus()
        self.build_toolbar()
        self.refresh_validation()
        self.update_window_title()

        self.resize(1200, 760)
        self.setUnifiedTitleAndToolBarOnMac(True)

    def build_toolbar(self) -> None:
        toolbar = QToolBar("Quick Actions", self)
        toolbar.setMovable(False)
        toolbar.setFloatable(False)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        toolbar.addAction(self.new_action)
        toolbar.addAction(self.open_action)
        toolbar.addAction(self.save_action)
        toolbar.addSeparator()
        toolbar.addAction(self.validate_action)
        toolbar.addAction(self.insert_custom_action)

    def build_menus(self) -> None:
        menu_bar = self.menuBar()
        menu_bar.setNativeMenuBar(True)

        file_menu = menu_bar.addMenu("&File")
        self.new_action = QAction("&New", self)
        self.new_action.setShortcut(QKeySequence.New)
        self.new_action.triggered.connect(self.new_document)
        file_menu.addAction(self.new_action)

        self.open_action = QAction("&Open...", self)
        self.open_action.setShortcut(QKeySequence.Open)
        self.open_action.triggered.connect(self.open_document)
        file_menu.addAction(self.open_action)

        self.save_action = QAction("&Save", self)
        self.save_action.setShortcut(QKeySequence.Save)
        self.save_action.triggered.connect(self.save_document)
        file_menu.addAction(self.save_action)

        self.save_as_action = QAction("Save &As...", self)
        self.save_as_action.setShortcut(QKeySequence.SaveAs)
        self.save_as_action.triggered.connect(self.save_document_as)
        file_menu.addAction(self.save_as_action)

        file_menu.addSeparator()

        self.reload_action = QAction("&Revert to Saved", self)
        self.reload_action.triggered.connect(self.reload_document)
        file_menu.addAction(self.reload_action)

        file_menu.addSeparator()

        self.exit_action = QAction("Quit", self)
        self.exit_action.setShortcut(QKeySequence.Quit)
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        edit_menu = menu_bar.addMenu("&Edit")
        undo_action = QAction("&Undo", self)
        undo_action.setShortcut(QKeySequence.Undo)
        undo_action.triggered.connect(self.editor.undo)
        edit_menu.addAction(undo_action)

        redo_action = QAction("&Redo", self)
        redo_action.setShortcut(QKeySequence.Redo)
        redo_action.triggered.connect(self.editor.redo)
        edit_menu.addAction(redo_action)
        edit_menu.addSeparator()

        cut_action = QAction("Cu&t", self)
        cut_action.setShortcut(QKeySequence.Cut)
        cut_action.triggered.connect(self.editor.cut)
        edit_menu.addAction(cut_action)

        copy_action = QAction("&Copy", self)
        copy_action.setShortcut(QKeySequence.Copy)
        copy_action.triggered.connect(self.editor.copy)
        edit_menu.addAction(copy_action)

        paste_action = QAction("&Paste", self)
        paste_action.setShortcut(QKeySequence.Paste)
        paste_action.triggered.connect(self.editor.paste)
        edit_menu.addAction(paste_action)

        edit_menu.addSeparator()

        find_action = QAction("&Find...", self)
        find_action.setShortcut(QKeySequence.Find)
        find_action.triggered.connect(self.find_text_dialog)
        edit_menu.addAction(find_action)

        find_next_action = QAction("Find &Next", self)
        find_next_action.setShortcut(QKeySequence.FindNext)
        find_next_action.triggered.connect(self.find_next)
        edit_menu.addAction(find_next_action)

        edit_menu.addSeparator()

        comment_action = QAction("Comment Selection", self)
        comment_action.triggered.connect(self.comment_selection)
        edit_menu.addAction(comment_action)

        uncomment_action = QAction("Uncomment Selection", self)
        uncomment_action.triggered.connect(self.uncomment_selection)
        edit_menu.addAction(uncomment_action)

        insert_menu = menu_bar.addMenu("&Insert")
        for group_name, snippets in SNIPPET_GROUPS.items():
            submenu = insert_menu.addMenu(group_name)
            for snippet in snippets:
                action = QAction(snippet.title, self)
                action.setStatusTip(snippet.description)
                action.triggered.connect(
                    lambda checked=False, snippet_text=snippet.content: self.insert_snippet(snippet_text)
                )
                submenu.addAction(action)

        insert_menu.addSeparator()
        self.insert_custom_action = QAction("Custom Setting...", self)
        self.insert_custom_action.triggered.connect(self.insert_custom_setting)
        insert_menu.addAction(self.insert_custom_action)

        advanced_menu = insert_menu.addMenu("Advanced")

        activity_led_action = QAction("Activity LED Behavior", self)
        activity_led_action.triggered.connect(
            lambda: self.show_advanced_window("activity_led", ActivityLedWindow)
        )
        advanced_menu.addAction(activity_led_action)

        antenna_action = QAction("Antenna Configuration", self)
        antenna_action.triggered.connect(
            lambda: self.show_advanced_window("antenna", AntennaConfigurationWindow)
        )
        advanced_menu.addAction(antenna_action)

        fan_curve_action = QAction("Fan Curve Editor", self)
        fan_curve_action.triggered.connect(
            lambda: self.show_advanced_window("fan_curve", FanCurveEditorWindow)
        )
        advanced_menu.addAction(fan_curve_action)

        hw_features_action = QAction("Enable/Disable HW Features", self)
        hw_features_action.triggered.connect(
            lambda: self.show_advanced_window("hw_features", HardwareFeaturesWindow)
        )
        advanced_menu.addAction(hw_features_action)

        bt_wlan_action = QAction("BT/WLAN Address", self)
        bt_wlan_action.triggered.connect(
            lambda: self.show_advanced_window("bt_wlan", BtWlanAddressWindow)
        )
        advanced_menu.addAction(bt_wlan_action)

        power_button_action = QAction("Power Button Settings", self)
        power_button_action.triggered.connect(
            lambda: self.show_advanced_window("power_button", PowerButtonSettingsWindow)
        )
        advanced_menu.addAction(power_button_action)

        overclock_action = QAction("Overclock Config", self)
        overclock_action.triggered.connect(
            lambda: self.show_advanced_window("overclock", OverclockConfigWindow)
        )
        advanced_menu.addAction(overclock_action)

        audio_action = QAction("Audio Settings", self)
        audio_action.triggered.connect(
            lambda: self.show_advanced_window("audio", AudioSettingsWindow)
        )
        advanced_menu.addAction(audio_action)

        boot_menu = advanced_menu.addMenu("Boot")

        boot_vc_action = QAction("VideoCore", self)
        boot_vc_action.triggered.connect(
            lambda: self.show_advanced_window("boot_videocore", BootVideoCoreWindow)
        )
        boot_menu.addAction(boot_vc_action)

        boot_initramfs_action = QAction("Initramfs", self)
        boot_initramfs_action.triggered.connect(
            lambda: self.show_advanced_window("boot_initramfs", BootInitramfsWindow)
        )
        boot_menu.addAction(boot_initramfs_action)

        boot_kernel_action = QAction("Kernel", self)
        boot_kernel_action.triggered.connect(
            lambda: self.show_advanced_window("boot_kernel", BootKernelWindow)
        )
        boot_menu.addAction(boot_kernel_action)

        boot_misc_action = QAction("Misc", self)
        boot_misc_action.triggered.connect(
            lambda: self.show_advanced_window("boot_misc", BootMiscWindow)
        )
        boot_menu.addAction(boot_misc_action)

        tools_menu = menu_bar.addMenu("&Tools")
        self.validate_action = QAction("Validate Document", self)
        self.validate_action.setShortcut("Ctrl+Shift+V")
        self.validate_action.triggered.connect(self.run_validation_report)
        tools_menu.addAction(self.validate_action)

        clean_action = QAction("Remove Trailing Whitespace", self)
        clean_action.triggered.connect(self.clean_trailing_whitespace)
        tools_menu.addAction(clean_action)

        normalize_action = QAction("Normalize Blank Lines", self)
        normalize_action.triggered.connect(self.normalize_blank_lines)
        tools_menu.addAction(normalize_action)

        view_menu = menu_bar.addMenu("&View")
        self.validation_dock.toggleViewAction().setText("Validation Panel")
        self.docs_dock.toggleViewAction().setText("Reference Notes")
        view_menu.addAction(self.validation_dock.toggleViewAction())
        view_menu.addAction(self.docs_dock.toggleViewAction())

        font_menu = view_menu.addMenu("Editor Font Size")
        group = QActionGroup(self)
        for size in (11, 12, 13, 14, 16):
            action = QAction(str(size), self, checkable=True)
            if size == 12:
                action.setChecked(True)
            action.triggered.connect(lambda checked=False, point_size=size: self.set_editor_font_size(point_size))
            group.addAction(action)
            font_menu.addAction(action)

        help_menu = menu_bar.addMenu("&Help")
        docs_action = QAction("Show Local config.txt Notes", self)
        docs_action.triggered.connect(lambda: self.docs_dock.setVisible(True))
        help_menu.addAction(docs_action)

        about_action = QAction(f"About {APP_NAME}", self)
        about_action.triggered.connect(self.show_about_dialog)
        help_menu.addAction(about_action)

    def load_docs_text(self) -> str:
        if DOCS_PATH.exists():
            return DOCS_PATH.read_text(encoding="utf-8", errors="replace")
        return "configtxt-docs.txt was not found next to the application."

    def set_editor_font_size(self, size: int) -> None:
        font = self.editor.font()
        font.setPointSize(size)
        self.editor.setFont(font)
        self.statusBar().showMessage(f"Editor font set to {size} pt", 2500)

    def insert_snippet(self, snippet_text: str) -> None:
        cursor = self.editor.textCursor()
        if not cursor.atBlockStart() and cursor.position() != 0:
            cursor.insertText("\n")
        cursor.insertText(snippet_text)
        self.statusBar().showMessage("Inserted config snippet", 2500)

    def show_advanced_window(self, key: str, window_class: type[QMainWindow]) -> None:
        window = self.advanced_windows.get(key)
        if window is None:
            window = window_class(self)
            if hasattr(window, "snippet_ready"):
                window.snippet_ready.connect(self.insert_snippet)
            self.advanced_windows[key] = window

        window.show()
        window.raise_()
        window.activateWindow()

    @Slot()
    def insert_custom_setting(self) -> None:
        dialog = CustomSettingDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self.insert_snippet(dialog.snippet_text())

    @Slot()
    def new_document(self) -> None:
        if not self.maybe_save():
            return
        self.editor.clear()
        self.current_path = None
        self.editor.document().setModified(False)
        self.update_window_title()
        self.statusBar().showMessage("Started a new blank config.txt", 2500)

    @Slot()
    def open_document(self) -> None:
        if not self.maybe_save():
            return

        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "Open config.txt",
            str(Path.home()),
            "Config files (*.txt *.conf);;All files (*)",
        )
        if not path_str:
            return

        path = Path(path_str)
        self.editor.setPlainText(path.read_text(encoding="utf-8", errors="replace"))
        self.current_path = path
        self.editor.document().setModified(False)
        self.update_window_title()
        self.statusBar().showMessage(f"Loaded {path.name}", 2500)

    @Slot()
    def save_document(self) -> bool:
        if self.current_path is None:
            return self.save_document_as()

        self.current_path.write_text(self.editor.toPlainText(), encoding="utf-8")
        self.editor.document().setModified(False)
        self.update_window_title()
        self.statusBar().showMessage(f"Saved {self.current_path.name}", 2500)
        return True

    @Slot()
    def save_document_as(self) -> bool:
        suggested = "config.txt" if self.current_path is None else self.current_path.name
        path_str, _ = QFileDialog.getSaveFileName(
            self,
            "Save config.txt",
            str(Path.home() / suggested),
            "Config files (*.txt *.conf);;All files (*)",
        )
        if not path_str:
            return False

        self.current_path = Path(path_str)
        return self.save_document()

    @Slot()
    def reload_document(self) -> None:
        if self.current_path is None:
            QMessageBox.information(self, APP_NAME, "There is no saved file to reload yet.")
            return
        if self.editor.document().isModified():
            response = QMessageBox.question(
                self,
                APP_NAME,
                "Discard unsaved changes and reload the last saved copy?",
            )
            if response != QMessageBox.Yes:
                return
        self.editor.setPlainText(self.current_path.read_text(encoding="utf-8", errors="replace"))
        self.editor.document().setModified(False)
        self.statusBar().showMessage(f"Reloaded {self.current_path.name}", 2500)

    def maybe_save(self) -> bool:
        if not self.editor.document().isModified():
            return True

        response = QMessageBox.question(
            self,
            APP_NAME,
            "Save your changes before continuing?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if response == QMessageBox.Save:
            return self.save_document()
        return response == QMessageBox.Discard

    def update_window_title(self) -> None:
        name = self.current_path.name if self.current_path else "Untitled config.txt"
        modified = "*" if self.editor.document().isModified() else ""
        self.setWindowTitle(f"{modified}{name} - {APP_NAME}")

    @Slot(bool)
    def on_modification_changed(self, _modified: bool) -> None:
        self.update_window_title()

    @Slot()
    def refresh_validation(self) -> None:
        self.validation_dock.set_messages(validate_config(self.editor.toPlainText()))

    @Slot()
    def run_validation_report(self) -> None:
        messages = validate_config(self.editor.toPlainText())
        self.validation_dock.setVisible(True)
        self.validation_dock.set_messages(messages)
        if messages:
            self.statusBar().showMessage(f"Validation finished with {len(messages)} issue(s)", 3500)
        else:
            self.statusBar().showMessage("Validation finished with no issues", 3500)

    @Slot(QListWidgetItem)
    def jump_to_issue(self, item: QListWidgetItem) -> None:
        line_number = item.data(Qt.UserRole)
        if not isinstance(line_number, int):
            return

        block = self.editor.document().findBlockByLineNumber(line_number - 1)
        cursor = QTextCursor(block)
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    @Slot()
    def find_text_dialog(self) -> None:
        value, accepted = QInputDialog.getText(
            self,
            "Find",
            "Find text:",
            text=self.find_text,
        )
        if accepted and value:
            self.find_text = value
            self.find_next()

    @Slot()
    def find_next(self) -> None:
        if not self.find_text:
            self.find_text_dialog()
            return
        if not self.editor.find(self.find_text):
            cursor = self.editor.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.editor.setTextCursor(cursor)
            self.editor.find(self.find_text)

    @Slot()
    def comment_selection(self) -> None:
        self.transform_selected_lines(lambda line: line if line.startswith("#") else f"#{line}")

    @Slot()
    def uncomment_selection(self) -> None:
        def remove_comment(line: str) -> str:
            if line.startswith("# "):
                return line[2:]
            if line.startswith("#"):
                return line[1:]
            return line

        self.transform_selected_lines(remove_comment)

    def transform_selected_lines(self, transform: Callable[[str], str]) -> None:
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            cursor.select(QTextCursor.LineUnderCursor)

        start = cursor.selectionStart()
        end = cursor.selectionEnd()

        cursor.beginEditBlock()
        cursor.setPosition(start)
        start_block = cursor.blockNumber()
        cursor.setPosition(end)
        end_block = cursor.blockNumber()

        doc = self.editor.document()
        for block_number in range(start_block, end_block + 1):
            block = doc.findBlockByNumber(block_number)
            line_cursor = QTextCursor(block)
            line_cursor.select(QTextCursor.LineUnderCursor)
            line_cursor.insertText(transform(block.text()))
        cursor.endEditBlock()

    @Slot()
    def clean_trailing_whitespace(self) -> None:
        cleaned = "\n".join(line.rstrip() for line in self.editor.toPlainText().splitlines())
        if self.editor.toPlainText().endswith("\n"):
            cleaned += "\n"
        self.editor.setPlainText(cleaned)
        self.statusBar().showMessage("Removed trailing whitespace", 2500)

    @Slot()
    def normalize_blank_lines(self) -> None:
        text = self.editor.toPlainText()
        normalized = re.sub(r"\n{3,}", "\n\n", text)
        self.editor.setPlainText(normalized)
        self.statusBar().showMessage("Normalized repeated blank lines", 2500)

    @Slot()
    def show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            APP_NAME,
            (
                f"{APP_NAME}\n\n"
                "A menu-driven PySide6 editor for building or editing Raspberry Pi config.txt files.\n"
                "Start blank, load existing files, insert common directives, and validate line structure."
            ),
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.maybe_save():
            event.accept()
        else:
            event.ignore()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Project Eben Utils")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
