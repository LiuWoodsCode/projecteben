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
import signal
import subprocess
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

        self.main_items: list[MenuItem] = [
            MenuItem("Reboot system now", self.exit_to_system),
            MenuItem("Factory reset", self.show_test_page),
            MenuItem("Wipe cache", self.show_test_page),
            MenuItem("Enter bash shell", self.show_test_page),
            MenuItem("View logs", self.show_logs_page),
            MenuItem("Power off", self.show_test_page),
        ]

        self.log_items: list[MenuItem] = [
            MenuItem("Kernel logs", self.view_kernel_logs),
            MenuItem("Userspace logs", self.view_userspace_logs),
            MenuItem("Back", self.back_to_main),
        ]

        with open("/proc/version", "r") as f:
            version = f.read().strip()

        try:
            with open("/etc/NoelleStockwood/revision", "r") as f:
                stage = f.read().strip()
        except:
            stage = "unknown"
        try:
            with open("/proc/device-tree/model", "r") as f:
                device_model = f.read().strip('\x00').strip()
        except FileNotFoundError:
            device_model = "unknown" 

        try:
            with open("/proc/device-tree/serial_number", "r") as f:
                device_sn = f.read().strip('\x00').strip()
        except FileNotFoundError:
            device_sn = "unknown" 
        parts = version.split()

        kernel_name = parts[0]          # Linux
        kernel_word = parts[1]          # version
        kernel_version = parts[2]       # 6.8.0-51-generic

        build_user_host = version.split("(")[1].split(")")[0]
        compiler_info = version.split("(", 2)[2].split(")")[0]

        build_info = version.split(")")[-1].strip()

        kernel1 = f"{kernel_name} {kernel_version} built by {build_user_host}"
        line2 = f"base=\"{device_model}\" rev=\"{stage}\""
        self.header_lines = [
            "Project Stockwood Recovery",
            kernel1,
            line2,
            "Connect a USB keyboard to continue.",
            # "Use volume up/down and power.",
        ]

        self.corrupt_lines = [
            "Cannot load Android system. Your data may be corrupt. If",
            "you continue to get this message, you may need to perform",
            "a factory data reset and erase all user data stored on this",
            "device.",
        ]

        self.footer_lines = [
            "Use volume up/down and power.",
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
            if curses.COLORS >= 256:
                LIGHT_YELLOW = 226
            else:
                LIGHT_YELLOW = curses.COLOR_YELLOW
            curses.init_pair(1, LIGHT_YELLOW, -1)
            curses.init_pair(2, curses.COLOR_RED, -1)
            curses.init_pair(3, curses.COLOR_CYAN, -1)
            curses.init_pair(4, curses.COLOR_WHITE, curses.COLOR_BLUE)
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
        elif self.page == "logs":
            self.draw_logs_page()
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
        for index, item in enumerate(self.current_items()):
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
        self.draw_menu_border(y + 1 + len(self.current_items()))
        self.draw_footer()

    def draw_logs_page(self) -> None:
        y = 0

        for line in ["View logs", "", "Select a log source to open its pager."]:
            self.add_line(y, line, self.attr(5, bold=(line == "View logs")))
            y += 1

        self.draw_menu(y + 1)
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

    def show_logs_page(self) -> None:
        self.page = "logs"
        self.selected = 0

    def back_to_main(self) -> None:
        self.page = "main"
        self.selected = 0

    def current_items(self) -> list[MenuItem]:
        if self.page == "logs":
            return self.log_items
        if self.page == "main":
            return self.main_items
        return []

    def run_external_command(self, command: list[str]) -> None:
        curses.def_prog_mode()
        curses.endwin()
        try:
            subprocess.run(command, check=False)
        finally:
            curses.reset_prog_mode()
            self.stdscr.refresh()

    def view_kernel_logs(self) -> None:
        self.run_external_command(["dmesg"])

    def view_userspace_logs(self) -> None:
        self.run_external_command(["journalctl"])

    def activate_selected(self) -> None:
        if self.page == "main":
            self.current_items()[self.selected].action()
        elif self.page == "logs":
            self.current_items()[self.selected].action()
        else:
            self.back_to_main()

    def handle_key(self, key: int) -> None:
        if self.page == "test":
            if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
                self.back_to_main()
            return

        if self.page == "logs":
            if key == curses.KEY_UP:
                self.selected = (self.selected - 1) % len(self.log_items)
                return

            if key == curses.KEY_DOWN:
                self.selected = (self.selected + 1) % len(self.log_items)
                return

            if key in (curses.KEY_ENTER, 10, 13, ord(" ")):
                self.activate_selected()
            return

        if key == curses.KEY_UP:
            self.selected = (self.selected - 1) % len(self.main_items)
            return

        if key == curses.KEY_DOWN:
            self.selected = (self.selected + 1) % len(self.main_items)
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