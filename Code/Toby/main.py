#!/usr/bin/env python3
import curses
import curses.textpad
import os
import shutil
import socket
import subprocess
import textwrap

APP_NAME = "Eben.Toby"
APP_VERSION = "1.0"
COLOR_BG = 1
COLOR_HILITE = 2
COLOR_TITLE = 3
COLOR_STATUS = 4
COLOR_WARNING = 5


# -----------------------------
# Utility / system helpers
# -----------------------------
def run_cmd(cmd, check=False):
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=check
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def command_exists(cmd):
    return shutil.which(cmd) is not None


def get_hostname():
    code, out, _ = run_cmd(["hostnamectl", "--static"])
    if code == 0 and out:
        return out
    return socket.gethostname()


def get_timezone():
    code, out, _ = run_cmd(["timedatectl", "show", "-p", "Timezone", "--value"])
    if code == 0 and out:
        return out
    if os.path.exists("/etc/timezone"):
        try:
            with open("/etc/timezone", "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return "Unknown"


def ssh_service_name():
    # Most Raspberry Pi OS installs use "ssh"
    for name in ["ssh", "ssh.service", "sshd", "sshd.service"]:
        code, _, _ = run_cmd(["systemctl", "status", name])
        if code in (0, 3, 4):  # status can return non-zero for inactive units
            return name
    return "ssh"


def is_ssh_enabled():
    svc = ssh_service_name()
    code, out, _ = run_cmd(["systemctl", "is-enabled", svc])
    return code == 0 and out.strip() == "enabled"


def get_ip_addresses():
    code, out, _ = run_cmd(["hostname", "-I"])
    if code == 0 and out:
        return out.strip()
    return "Unavailable"


def get_kernel():
    code, out, _ = run_cmd(["uname", "-r"])
    return out if code == 0 else "Unknown"


def get_model():
    if os.path.exists("/proc/device-tree/model"):
        try:
            with open("/proc/device-tree/model", "rb") as f:
                return f.read().replace(b"\x00", b"").decode("utf-8", errors="ignore")
        except Exception:
            pass
    code, out, _ = run_cmd(["uname", "-m"])
    return out if code == 0 else "Unknown"


def set_hostname(new_name):
    return run_cmd(["hostnamectl", "set-hostname", new_name], check=False)


def set_timezone(zone):
    return run_cmd(["timedatectl", "set-timezone", zone], check=False)


def enable_ssh(enable=True):
    svc = ssh_service_name()
    if enable:
        code1, out1, err1 = run_cmd(["systemctl", "enable", svc], check=False)
        code2, out2, err2 = run_cmd(["systemctl", "start", svc], check=False)
        rc = code1 or code2
        return rc, "\n".join([out1, out2]).strip(), "\n".join([err1, err2]).strip()
    else:
        code1, out1, err1 = run_cmd(["systemctl", "stop", svc], check=False)
        code2, out2, err2 = run_cmd(["systemctl", "disable", svc], check=False)
        rc = code1 or code2
        return rc, "\n".join([out1, out2]).strip(), "\n".join([err1, err2]).strip()


def expand_filesystem():
    if command_exists("raspi-config"):
        # Use raspi-config backend if present; this is reliable on Raspberry Pi OS
        return run_cmd(["raspi-config", "nonint", "do_expand_rootfs"], check=False)
    return 1, "", "raspi-config not found; automatic rootfs expansion is unavailable on this system."


def reboot_system():
    return run_cmd(["reboot"], check=False)


def poweroff_system():
    return run_cmd(["poweroff"], check=False)


# -----------------------------
# UI helpers
# -----------------------------
def init_colors():
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(COLOR_BG, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(COLOR_HILITE, curses.COLOR_YELLOW, curses.COLOR_CYAN)
    curses.init_pair(COLOR_TITLE, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(COLOR_STATUS, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(COLOR_WARNING, curses.COLOR_YELLOW, curses.COLOR_BLUE)


def center_text(win, y, text, attr=0):
    h, w = win.getmaxyx()
    x = max(0, (w - len(text)) // 2)
    win.addstr(y, x, text[: w - 1], attr)


def draw_frame(stdscr, title="", footer=""):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    stdscr.bkgd(" ", curses.color_pair(COLOR_BG))
    stdscr.attron(curses.color_pair(COLOR_BG))
    stdscr.box()

    # Title bar
    title_text = f" {APP_NAME} v{APP_VERSION} "
    stdscr.addstr(0, 2, title_text, curses.color_pair(COLOR_TITLE) | curses.A_BOLD)

    if title:
        t = f"[ {title} ]"
        center_text(stdscr, 0, t, curses.color_pair(COLOR_TITLE) | curses.A_BOLD)

    # Footer bar
    if footer:
        stdscr.attron(curses.color_pair(COLOR_STATUS))
        footer_text = footer[: max(0, w - 4)]
        stdscr.addstr(h - 1, 2, footer_text.ljust(w - 4), curses.color_pair(COLOR_STATUS))
        stdscr.attroff(curses.color_pair(COLOR_STATUS))

    stdscr.attroff(curses.color_pair(COLOR_BG))


def draw_tabs(stdscr, tabs, selected_tab):
    x = 3
    for i, tab in enumerate(tabs):
        label = f" {tab} "
        if i == selected_tab:
            stdscr.addstr(2, x, label, curses.color_pair(COLOR_HILITE) | curses.A_BOLD)
        else:
            stdscr.addstr(2, x, label, curses.color_pair(COLOR_BG) | curses.A_BOLD)
        x += len(label) + 1


def draw_menu(stdscr, items, selected_idx, status_provider):
    h, w = stdscr.getmaxyx()
    top = 5
    left = 4
    right = w - 4
    bottom = h - 4

    # Inner frame
    for x in range(left, right):
        stdscr.addch(top, x, curses.ACS_HLINE)
        stdscr.addch(bottom, x, curses.ACS_HLINE)
    for y in range(top, bottom + 1):
        stdscr.addch(y, left, curses.ACS_VLINE)
        stdscr.addch(y, right, curses.ACS_VLINE)
    stdscr.addch(top, left, curses.ACS_ULCORNER)
    stdscr.addch(top, right, curses.ACS_URCORNER)
    stdscr.addch(bottom, left, curses.ACS_LLCORNER)
    stdscr.addch(bottom, right, curses.ACS_LRCORNER)

    menu_title = " BIOS Features Setup "
    stdscr.addstr(top, left + 2, menu_title, curses.A_BOLD)

    row_y = top + 2
    for idx, item in enumerate(items):
        label = item["label"]
        value = status_provider(item)
        line_attr = curses.A_BOLD if idx == selected_idx else curses.A_NORMAL
        if idx == selected_idx:
            stdscr.attron(curses.color_pair(COLOR_HILITE))
            stdscr.addstr(row_y + idx, left + 2, " " * (right - left - 3))
            stdscr.addstr(row_y + idx, left + 3, label[: (right - left - 20)], line_attr)
            if value:
                value_x = right - len(value) - 2
                if value_x > left + 10:
                    stdscr.addstr(row_y + idx, value_x, value[: right - value_x - 1], line_attr)
            stdscr.attroff(curses.color_pair(COLOR_HILITE))
        else:
            stdscr.addstr(row_y + idx, left + 3, label[: (right - left - 20)], line_attr)
            if value:
                value_x = right - len(value) - 2
                if value_x > left + 10:
                    stdscr.addstr(row_y + idx, value_x, value[: right - value_x - 1], line_attr)


def message_box(stdscr, title, message):
    h, w = stdscr.getmaxyx()
    lines = []
    for paragraph in str(message).splitlines() or [""]:
        wrapped = textwrap.wrap(paragraph, width=max(20, w - 14)) or [""]
        lines.extend(wrapped)

    box_h = min(h - 4, len(lines) + 6)
    box_w = min(w - 4, max(len(title) + 6, max((len(x) for x in lines), default=20) + 6))
    y = (h - box_h) // 2
    x = (w - box_w) // 2

    win = curses.newwin(box_h, box_w, y, x)
    win.bkgd(" ", curses.color_pair(COLOR_BG))
    win.box()
    win.addstr(0, 2, f" {title} ", curses.A_BOLD)

    visible_lines = lines[: box_h - 4]
    for i, line in enumerate(visible_lines, start=2):
        win.addstr(i, 2, line[: box_w - 4])

    hint = "Press any key"
    win.addstr(box_h - 2, max(2, box_w - len(hint) - 3), hint, curses.A_DIM)
    win.refresh()
    win.getch()


def confirm_box(stdscr, title, question):
    h, w = stdscr.getmaxyx()
    lines = textwrap.wrap(question, width=max(20, w - 18))
    box_h = len(lines) + 7
    box_w = max(40, max(len(title) + 6, max((len(x) for x in lines), default=20) + 6))
    box_w = min(box_w, w - 4)
    box_h = min(box_h, h - 4)

    y = (h - box_h) // 2
    x = (w - box_w) // 2

    selected = 0  # 0 = Yes, 1 = No
    while True:
        win = curses.newwin(box_h, box_w, y, x)
        win.bkgd(" ", curses.color_pair(COLOR_BG))
        win.box()
        win.addstr(0, 2, f" {title} ", curses.A_BOLD)

        for i, line in enumerate(lines[: box_h - 5], start=2):
            win.addstr(i, 2, line[: box_w - 4])

        yes_attr = curses.color_pair(COLOR_HILITE) | curses.A_BOLD if selected == 0 else curses.A_NORMAL
        no_attr = curses.color_pair(COLOR_HILITE) | curses.A_BOLD if selected == 1 else curses.A_NORMAL

        btn_y = box_h - 2
        yes_x = box_w // 2 - 10
        no_x = box_w // 2 + 2
        win.addstr(btn_y, yes_x, "[ Yes ]", yes_attr)
        win.addstr(btn_y, no_x, "[ No ]", no_attr)
        win.refresh()

        ch = win.getch()
        if ch in (curses.KEY_LEFT, curses.KEY_RIGHT, 9):
            selected = 1 - selected
        elif ch in (10, 13, curses.KEY_ENTER):
            return selected == 0
        elif ch in (27, ord("q"), ord("n"), ord("N")):
            return False
        elif ch in (ord("y"), ord("Y")):
            return True


def input_box(stdscr, title, prompt, initial=""):
    h, w = stdscr.getmaxyx()
    box_w = min(max(50, len(prompt) + 10), w - 4)
    box_h = 8
    y = (h - box_h) // 2
    x = (w - box_w) // 2

    win = curses.newwin(box_h, box_w, y, x)
    win.bkgd(" ", curses.color_pair(COLOR_BG))
    win.box()
    win.addstr(0, 2, f" {title} ", curses.A_BOLD)
    win.addstr(2, 2, prompt[: box_w - 4])

    edit_w = box_w - 4
    editwin = curses.newwin(1, edit_w, y + 4, x + 2)
    editwin.bkgd(" ", curses.color_pair(COLOR_STATUS))
    editwin.addstr(0, 0, initial[: edit_w - 1])
    curses.curs_set(1)
    textpad = curses.textpad.Textbox(editwin)

    win.refresh()
    editwin.refresh()
    try:
        value = textpad.edit().strip()
    finally:
        curses.curs_set(0)
    return value


def system_summary():
    return (
        f"Model:     {get_model()}\n"
        f"Hostname:  {get_hostname()}\n"
        f"Timezone:  {get_timezone()}\n"
        f"SSH:       {'Enabled' if is_ssh_enabled() else 'Disabled'}\n"
        f"Kernel:    {get_kernel()}\n"
        f"IP Addr:   {get_ip_addresses()}\n"
    )


# -----------------------------
# Main application
# -----------------------------
def main(stdscr):
    if os.geteuid() != 0:
        print("Please run this script as root: sudo python3 Toby.py")
        return

    curses.curs_set(0)
    curses.noecho()
    curses.cbreak()
    stdscr.keypad(True)
    init_colors()

    tabs = ["Main", "Advanced", "Power"]
    selected_tab = 0
    selected_idx = 0

    menu = {
        "Main": [
            {"label": "System Summary", "action": "summary"},
            {"label": "System Hostname", "action": "hostname"},
            {"label": "SSH Service", "action": "ssh"},
            {"label": "System Timezone", "action": "timezone"},
        ],
        "Advanced": [
            {"label": "Expand Filesystem", "action": "expandfs"},
            {"label": "About Toby", "action": "about"},
        ],
        "Power": [
            {"label": "Save & Exit Setup", "action": "exit"},
            {"label": "Reboot System", "action": "reboot"},
            {"label": "Power Off System", "action": "poweroff"},
        ],
    }

    def current_items():
        return menu[tabs[selected_tab]]

    def status_provider(item):
        act = item["action"]
        if act == "hostname":
            return get_hostname()
        if act == "ssh":
            return "Enabled" if is_ssh_enabled() else "Disabled"
        if act == "timezone":
            return get_timezone()
        if act == "expandfs":
            return "Available" if command_exists("raspi-config") else "Unavailable"
        return ""

    while True:
        items = current_items()
        selected_idx = max(0, min(selected_idx, len(items) - 1))

        draw_frame(
            stdscr,
            title="Raspberry Pi Setup Utility",
            footer="←/→ Tab   ↑/↓ Move   Enter Select   Esc Exit"
        )
        draw_tabs(stdscr, tabs, selected_tab)
        draw_menu(stdscr, items, selected_idx, status_provider)
        stdscr.refresh()

        ch = stdscr.getch()

        if ch in (27,):  # ESC
            if confirm_box(stdscr, "Exit Setup", "Exit Setup Utility?"):
                break

        elif ch == curses.KEY_LEFT:
            selected_tab = (selected_tab - 1) % len(tabs)
            selected_idx = 0

        elif ch == curses.KEY_RIGHT:
            selected_tab = (selected_tab + 1) % len(tabs)
            selected_idx = 0

        elif ch == curses.KEY_UP:
            selected_idx = (selected_idx - 1) % len(items)

        elif ch == curses.KEY_DOWN:
            selected_idx = (selected_idx + 1) % len(items)

        elif ch in (10, 13, curses.KEY_ENTER):
            choice = items[selected_idx]["action"]

            if choice == "summary":
                message_box(stdscr, "System Summary", system_summary())

            elif choice == "hostname":
                current = get_hostname()
                new_name = input_box(
                    stdscr,
                    "Set Hostname",
                    "Enter new hostname:",
                    current
                )
                if not new_name:
                    continue
                if not all(c.isalnum() or c in "-." for c in new_name):
                    message_box(stdscr, "Invalid Hostname",
                                "Hostname may only contain letters, numbers, hyphens, and dots.")
                    continue
                if confirm_box(stdscr, "Confirm Hostname",
                               f"Change hostname from '{current}' to '{new_name}'?"):
                    rc, out, err = set_hostname(new_name)
                    if rc == 0:
                        message_box(stdscr, "Success",
                                    f"Hostname changed to '{new_name}'.\nA reboot may be recommended.")
                    else:
                        message_box(stdscr, "Error", err or out or "Failed to set hostname.")

            elif choice == "ssh":
                enabled = is_ssh_enabled()
                target = not enabled
                if confirm_box(
                    stdscr,
                    "Toggle SSH",
                    f"{'Disable' if enabled else 'Enable'} SSH service?"
                ):
                    rc, out, err = enable_ssh(target)
                    if rc == 0:
                        state = "enabled" if target else "disabled"
                        message_box(stdscr, "Success", f"SSH has been {state}.")
                    else:
                        message_box(stdscr, "Error", err or out or "Failed to change SSH state.")

            elif choice == "timezone":
                current = get_timezone()
                new_tz = input_box(
                    stdscr,
                    "Set Timezone",
                    "Enter timezone (e.g. America/New_York):",
                    current
                )
                if not new_tz:
                    continue
                if confirm_box(stdscr, "Confirm Timezone",
                               f"Change timezone from '{current}' to '{new_tz}'?"):
                    rc, out, err = set_timezone(new_tz)
                    if rc == 0:
                        message_box(stdscr, "Success", f"Timezone changed to '{new_tz}'.")
                    else:
                        message_box(stdscr, "Error",
                                    err or out or "Failed to set timezone.\nMake sure the timezone name is valid.")

            elif choice == "expandfs":
                if confirm_box(stdscr, "Expand Filesystem",
                               "Attempt to expand the root filesystem?\nA reboot may be required."):
                    rc, out, err = expand_filesystem()
                    if rc == 0:
                        message_box(stdscr, "Success",
                                    "Filesystem expansion command completed.\nPlease reboot the system.")
                    else:
                        message_box(stdscr, "Error", err or out or "Expansion failed.")

            elif choice == "about":
                message_box(
                    stdscr,
                    "About Toby",
                    (
                        f"{APP_NAME} v{APP_VERSION}\n\n"
                        "A Raspberry Pi configuration utility with an old-school\n"
                        "AMI BIOS terminal look.\n\n"
                        "This tool applies changes immediately, unlike real BIOS\n"
                        "firmware menus. Use with caution and preferably test on\n"
                        "a non-critical system first."
                    )
                )

            elif choice == "reboot":
                if confirm_box(stdscr, "Reboot System", "Reboot now?"):
                    rc, out, err = reboot_system()
                    if rc != 0:
                        message_box(stdscr, "Error", err or out or "Failed to reboot.")

            elif choice == "poweroff":
                if confirm_box(stdscr, "Power Off System", "Power off now?"):
                    rc, out, err = poweroff_system()
                    if rc != 0:
                        message_box(stdscr, "Error", err or out or "Failed to power off.")

            elif choice == "exit":
                if confirm_box(stdscr, "Exit Setup", "Exit Toby Setup Utility?"):
                    break


curses.wrapper(main)

