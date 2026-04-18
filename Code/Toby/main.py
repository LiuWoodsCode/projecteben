from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Label, ListItem, ListView, Static


@dataclass
class SettingItem:
    name: str
    value: str
    description: str


TAB_DATA: Dict[str, List[SettingItem]] = {
    "Main": [
        SettingItem("UEFI BIOS Version", "R0RET14W (1.14)", "Current installed UEFI firmware version."),
        SettingItem("BIOS Build Date", "2017-10-26", "Firmware build date reported by the system."),
        SettingItem("Embedded Controller", "R0HT14W", "Embedded controller firmware revision."),
        SettingItem("Machine Type Model", "20NR", "System platform identifier."),
        SettingItem("CPU", "Intel Core i7-8650U", "Installed processor model."),
        SettingItem("CPU Speed", "1.90 GHz", "Base processor frequency."),
        SettingItem("Installed Memory", "16 GB", "Total system memory detected."),
        SettingItem("UUID", "e23b804c-36a8-11b2-a85c-1234567890ab", "Platform universal unique identifier."),
    ],
    "Config": [
        SettingItem("USB Support", "Enabled", "Enable or disable USB controller initialization."),
        SettingItem("Thunderbolt", "Enabled", "Controls Thunderbolt device availability."),
        SettingItem("Power Beep", "Disabled", "Emit an audible tone during power state changes."),
        SettingItem("Keyboard Backlight", "Auto", "Automatic keyboard illumination policy."),
        SettingItem("Virtualization", "Enabled", "CPU virtualization extensions."),
        SettingItem("Fan Control", "Balanced", "Thermal and acoustic tuning profile."),
    ],
    "Security": [
        SettingItem("Supervisor Password", "Not Set", "Administrative firmware password state."),
        SettingItem("Secure Boot", "Enabled", "UEFI secure boot enforcement."),
        SettingItem("TPM State", "Enabled", "Trusted Platform Module availability."),
        SettingItem("Intel TXT", "Disabled", "Trusted execution technology setting."),
        SettingItem("I/O Port Access", "Restricted", "Hardware interface access restrictions."),
    ],
    "Startup": [
        SettingItem("Boot Mode", "UEFI Only", "Firmware boot mode."),
        SettingItem("Boot Priority", "NVMe SSD", "Current preferred boot device."),
        SettingItem("Fast Boot", "Enabled", "Reduce initialization time during startup."),
        SettingItem("PXE Boot", "Disabled", "Network boot over integrated NIC."),
        SettingItem("Option ROMs", "Auto", "Legacy option ROM compatibility behavior."),
    ],
    "Restart": [
        SettingItem("Save and Exit", "Enter", "Save configuration changes and restart."),
        SettingItem("Discard and Exit", "Enter", "Ignore changes and restart."),
        SettingItem("Load Setup Defaults", "Enter", "Restore factory-default firmware settings."),
    ],
}


class TabLabel(Label):
    """A clickable-looking tab label with reactive selection styling."""

    selected = reactive(False)

    def __init__(self, text: str, tab_name: str) -> None:
        super().__init__(text)
        self.tab_name = tab_name

    def watch_selected(self, selected: bool) -> None:
        self.set_class(selected, "-selected")


class DetailsPane(Static):
    """Right-side details panel."""

    def show_item(self, tab_name: str, item: SettingItem) -> None:
        content = (
            f"[#5fd7ff]{tab_name}[/#5fd7ff]\n\n"
            f"[bold white]{item.name}[/bold white]\n"
            f"[bright_cyan]{item.value}[/bright_cyan]\n\n"
            f"[#a0a0a0]{item.description}[/#a0a0a0]"
        )
        self.update(content)


class ModernBiosApp(App):
    CSS = """
    Screen {
        background: black;
        color: white;
    }

    Header {
        background: transparent;
        color: #5fd7ff;
        text-style: bold;
    }

    Footer {
        background: transparent;
        color: #808080;
    }

    #app-grid {
        height: 1fr;
        layout: vertical;
    }

    #tabs {
        height: 3;
        padding: 1 2 0 2;
        border-bottom: solid #303030;
    }

    TabLabel {
        margin-right: 2;
        color: #707070;
        text-style: bold;
    }

    TabLabel.-selected {
        color: #5fd7ff;
        text-style: bold underline;
    }

    #body {
        height: 1fr;
    }

    #left-panel {
        width: 42;
        min-width: 28;
        padding: 1 1 1 2;
        border-right: solid #303030;
    }

    #right-panel {
        width: 1fr;
        padding: 2 3;
    }

    #section-title {
        color: #5fd7ff;
        text-style: bold;
        margin-bottom: 1;
    }

    ListView {
        background: transparent;
        border: none;
    }

    ListItem {
        color: #909090;
        background: transparent;
        padding: 0 0 0 0;
    }

    ListItem.--highlight,
    ListItem.-highlight {
        color: white;
        text-style: bold;
    }

    ListItem > Label {
        width: 1fr;
    }

    .setting-name {
        color: white;
    }

    .setting-value {
        color: #5fd7ff;
    }

    #details {
        color: white;
        border: round #303030;
        padding: 1 2;
    }

    #hint {
        color: #707070;
        margin-top: 1;
    }
    """

    BINDINGS = [
        ("left", "previous_tab", "Previous Tab"),
        ("right", "next_tab", "Next Tab"),
        ("up", "cursor_up", "Up"),
        ("down", "cursor_down", "Down"),
        ("enter", "activate", "Activate"),
        ("q", "quit", "Quit"),
    ]

    current_tab_index = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self.tabs = list(TAB_DATA.keys())
        self.tab_widgets: List[TabLabel] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with Vertical(id="app-grid"):
            with Horizontal(id="tabs"):
                for index, tab_name in enumerate(self.tabs):
                    tab = TabLabel(tab_name, tab_name)
                    tab.selected = (index == 0)
                    self.tab_widgets.append(tab)
                    yield tab

            with Horizontal(id="body"):
                with Vertical(id="left-panel"):
                    yield Static("System Configuration", id="section-title")
                    yield ListView(id="setting-list")
                    yield Static(
                        "Arrow keys to navigate. Enter to simulate selecting an item.",
                        id="hint",
                    )

                with Container(id="right-panel"):
                    yield DetailsPane(id="details")

        yield Footer()

    def on_mount(self) -> None:
        self._load_tab()
        self.title = "Modern Firmware Setup"
        self.sub_title = "Terminal Edition"

    def _make_list_item(self, item: SettingItem) -> ListItem:
        label = Label(
            f"[bold white]{item.name}[/bold white]  [#5fd7ff]{item.value}[/#5fd7ff]"
        )
        list_item = ListItem(label)
        list_item.data = item
        return list_item

    def _load_tab(self) -> None:
        tab_name = self.tabs[self.current_tab_index]
        list_view = self.query_one("#setting-list", ListView)
        list_view.clear()

        for item in TAB_DATA[tab_name]:
            list_view.append(self._make_list_item(item))

        for i, widget in enumerate(self.tab_widgets):
            widget.selected = (i == self.current_tab_index)

        if TAB_DATA[tab_name]:
            self.call_after_refresh(self._focus_first_item)

    def _focus_first_item(self) -> None:
        list_view = self.query_one("#setting-list", ListView)
        if len(list_view.children) > 0:
            list_view.index = 0
            self._update_details()

    def _update_details(self) -> None:
        tab_name = self.tabs[self.current_tab_index]
        list_view = self.query_one("#setting-list", ListView)
        details = self.query_one("#details", DetailsPane)

        if list_view.index is None:
            return

        try:
            current = list_view.children[list_view.index]
            item = current.data
        except (IndexError, AttributeError):
            return

        details.show_item(tab_name, item)

    def action_next_tab(self) -> None:
        self.current_tab_index = (self.current_tab_index + 1) % len(self.tabs)
        self._load_tab()

    def action_previous_tab(self) -> None:
        self.current_tab_index = (self.current_tab_index - 1) % len(self.tabs)
        self._load_tab()

    def action_cursor_up(self) -> None:
        list_view = self.query_one("#setting-list", ListView)
        list_view.action_cursor_up()
        self._update_details()

    def action_cursor_down(self) -> None:
        list_view = self.query_one("#setting-list", ListView)
        list_view.action_cursor_down()
        self._update_details()

    def action_activate(self) -> None:
        tab_name = self.tabs[self.current_tab_index]
        list_view = self.query_one("#setting-list", ListView)
        details = self.query_one("#details", DetailsPane)

        if list_view.index is None:
            return

        try:
            current = list_view.children[list_view.index]
            item = current.data
        except (IndexError, AttributeError):
            return

        details.update(
            f"[#5fd7ff]{tab_name}[/#5fd7ff]\n\n"
            f"[bold white]{item.name}[/bold white]\n"
            f"[bright_cyan]{item.value}[/bright_cyan]\n\n"
            f"[#a0a0a0]{item.description}[/#a0a0a0]\n\n"
            f"[green]Selected.[/green] In a real firmware UI, this would open an editor or confirmation dialog."
        )

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._update_details()


if __name__ == "__main__":
    ModernBiosApp().run()