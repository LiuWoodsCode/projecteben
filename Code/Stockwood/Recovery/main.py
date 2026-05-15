#!/usr/bin/env python3
"""
android_recovery_mock.py

Terminal mock of stock Android Recovery.

Controls:
  Up / Down      move selection
  Enter / Space  activate selection

Run:
  python android_recovery_mock.py
  python android_recovery_mock.py --reason corrupt
"""

from __future__ import annotations

import argparse
import curses
import os
import signal
from dataclasses import dataclass
from typing import Callable



@dataclass
class MenuItem:
    label: str
    action: Callable[[], None]


class AndroidRecoveryMock:
    def __init__(self, stdscr: "curses._CursesWindow", reason: str | None):
        self.stdscr = stdscr
        self.reason = reason
        self.page = "main"
        self.selected = 0
        self.running = True

        self.items: list[MenuItem] = [
            MenuItem("Exit to system", self.exit_to_system),
            MenuItem("Test page", self.show_test_page),
        ]

        with open("/proc/version", "r") as f:
            version = f.read().strip()

        parts = version.split()

        kernel_name = parts[0]          # Linux
        kernel_word = parts[1]          # version
        kernel_version = parts[2]       # 6.8.0-51-generic

        build_user_host = version.split("(")[1].split(")")[0]
        compiler_info = version.split("(", 2)[2].split(")")[0]

        build_info = version.split(")")[-1].strip()

        path = "/etc/NoelleStockwood"

        if os.path.isdir(path):
            is_stockwood = True
            try:
                with open("/etc/NoelleStockwood/revision", "r") as f:
                    stockwoodrev = f.read().replace("\x00", "").strip()
            except:
                stockwoodrev = "unknown"
            try:
                with open("/etc/NoelleStockwood/model", "r") as f:
                    stockwoodmodel = f.read().replace("\x00", "").strip()
            except:
                stockwoodmodel = "unknown"
        else:
            is_stockwood = False
            stockwoodrev = "not applicable"
            stockwoodmodel = "not applicable"

        if os.path.exists("/sys/firmware/devicetree/base/model"):
            with open("/sys/firmware/devicetree/base/model", "r") as f:
                model = f.read().replace("\x00", "").strip()

        elif os.path.exists("/sys/class/dmi/id/product_name"):
            with open("/sys/class/dmi/id/product_name", "r") as f:
                model = f.read().strip()

        else:
            self.model = "Unknown Device"

        serial = "Unknown"
        revision = "Unknown"

        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("Serial"):
                    serial = line.split(":")[1].strip()
                elif line.startswith("Revision"):
                    revision = line.split(":")[1].strip()

        print(f"Model: {model}")
        print(f"Serial: {serial}")
        print(f"Revision: {revision}")

        kernel1 = f"{kernel_name} {kernel_version} built by {build_user_host}"
        if is_stockwood:
            self.header_lines = [
                "Project Stockwood Recovery",
                kernel1,
                "Connect a USB keyboard.",
            ]
        else:
            self.header_lines = [
                "Project Stockwood Recovery",
                "WARNING: Possibly unofficial hw config (NoelleStockwood folder missing)",
                kernel1,
                "Connect a USB keyboard.",
            ]

        self.corrupt_lines = [
            "Cannot load Android system. Your data may be corrupt. If",
            "you continue to get this message, you may need to perform",
            "a factory data reset and erase all user data stored on this",
            "device.",
        ]

        self.footer_lines = [
            f"Running kernel {kernel1}",
            "system info:",
            f"Is hw NoelleStockwood? {is_stockwood}",
            f"rpi_model=\"{model}\" stockwood_model=\"{stockwoodmodel}\"",
            f"serial=\"{serial}\"",
            f"rpi_rev=\"{revision}\" stockwood_rev=\"{stockwoodrev}\""
        ]

        self.corrupt_lines = [
            "Warning: Unofficial hw config (NoelleStockwood folder missing)"
        ]

        if self.reason == "corrupt":
            self.footer_lines = [
                "***** NEED TO WIPE USERDATA *****",
                "REASON IS:",
                "[RescueParty]",
            ]

        self.test_lines = [
            "Test page",
            "",
            "This is a stock Android Recovery style test screen.",
            "",
            "Only the selected row is highlighted.",
            "",
            "Select Back to return.",
        ]

    def setup(self) -> None:
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        curses.curs_set(0)
        curses.noecho()
        curses.cbreak()
        self.stdscr.keypad(True)

        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()

            LIGHT_YELLOW = 226
            curses.init_pair(1, LIGHT_YELLOW, -1)
            curses.init_pair(2, curses.COLOR_RED, -1)
            curses.init_pair(3, curses.COLOR_CYAN, -1)
            curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_CYAN)
            curses.init_pair(5, curses.COLOR_WHITE, -1)

    def attr(self, pair: int, bold: bool = False) -> int:
        value = curses.color_pair(pair) if curses.has_colors() else 0
        if bold:
            value |= curses.A_BOLD
        return value

    def add_line(self, y: int, text: str, attr: int = 0) -> None:
        height, width = self.stdscr.getmaxyx()

        if y < 0 or y >= height:
            return

        try:
            self.stdscr.addstr(y, 0, text[: max(0, width - 1)], attr)
        except curses.error:
            pass

    def add_selected_line(self, y: int, text: str) -> None:
        height, width = self.stdscr.getmaxyx()

        if y < 0 or y >= height:
            return

        try:
            highlight = self.attr(4)
            self.stdscr.addstr(y, 0, " " * max(0, width), highlight)
            self.stdscr.addstr(y, 0, text[: max(0, width)], highlight | curses.A_BOLD)
        except curses.error:
            pass

    def draw(self) -> None:
        self.stdscr.erase()

        if self.page == "main":
            self.draw_main()
        else:
            self.draw_test_page()

        self.stdscr.refresh()

    def print_banner(self) -> int:
        y = 0

        for index, line in enumerate(self.header_lines):
            self.add_line(y, line, self.attr(1, bold=(index == 0)))
            y += 1

        return y

    def draw_header(self) -> int:
        return self.print_banner()

    def draw_corrupt_warning(self, y: int) -> int:
        if self.reason != "corrupt":
            return y

        for line in self.corrupt_lines:
            self.add_line(y, line, self.attr(2, bold=True))
            y += 1

        return y

    def draw_menu(self, y: int) -> None:
        for index, item in enumerate(self.items):
            if index == self.selected:
                self.add_selected_line(y + index, item.label)
            else:
                self.add_line(y + index, item.label, self.attr(3))

    def draw_menu_border(self, y: int) -> None:
        height, width = self.stdscr.getmaxyx()

        if y < 0 or y >= height:
            return

        try:
            self.stdscr.hline(y, 0, curses.ACS_HLINE, max(0, width - 1), self.attr(3))
        except curses.error:
            pass

    def draw_footer(self) -> None:
        height, _width = self.stdscr.getmaxyx()
        footer_y = max(0, height - len(self.footer_lines))

        for index, line in enumerate(self.footer_lines):
            self.add_line(footer_y + index, line, self.attr(5))

    def draw_main(self) -> None:
        y = self.print_banner()
        y = self.draw_corrupt_warning(y)
        self.draw_menu_border(y)
        self.draw_menu(y + 1)
        self.draw_menu_border(y + 1 + len(self.items))
        self.draw_footer()

    def draw_test_page(self) -> None:
        y = 0

        for line in self.test_lines:
            self.add_line(y, line, self.attr(5, bold=(line == "Test page")))
            y += 1

        self.add_selected_line(y, "Back")
        self.draw_footer()

    def exit_to_system(self) -> None:
        self.running = False

    def show_test_page(self) -> None:
        self.page = "test"

    def back_to_main(self) -> None:
        self.page = "main"

    def activate_selected(self) -> None:
        if self.page == "main":
            self.items[self.selected].action()
        else:
            self.back_to_main()

    def handle_key(self, key: int) -> None:
        if self.page == "test":
            if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
                self.back_to_main()
            return

        if key == curses.KEY_UP:
            self.selected = (self.selected - 1) % len(self.items)
            return

        if key == curses.KEY_DOWN:
            self.selected = (self.selected + 1) % len(self.items)
            return

        if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
            self.activate_selected()

    def run(self) -> None:
        self.setup()

        while self.running:
            self.draw()
            key = self.stdscr.getch()
            self.handle_key(key)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Terminal mock of stock Android Recovery."
    )
    parser.add_argument(
        "--reason",
        choices=["corrupt"],
        default=None,
        help="Show the red Android corruption warning.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    curses.wrapper(lambda stdscr: AndroidRecoveryMock(stdscr, args.reason).run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())