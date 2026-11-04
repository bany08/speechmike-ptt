"""Optional status icon. It only shows the state and never controls the microphone.

This is the only part of the program with a dependency beyond the standard
library (PyGObject with AyatanaAppIndicator3), so `gi` is imported on demand.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
from typing import Optional, Sequence, Tuple

from .config import ConfigError, DictationConfig, load_config
from .status import read_status

log = logging.getLogger(__name__)

POLL_MILLISECONDS = 300
TOOL_CHECK_EVERY_POLLS = 7  # about every 2 seconds
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")


def describe(status: Optional[dict]) -> Tuple[str, str]:
    """Return the icon name and the label for a daemon state."""
    if status is None:
        return "speechmike-ptt-off", "Service not running"
    if not status.get("device"):
        return "speechmike-ptt-off", "SpeechMike not found"
    if status.get("dictating"):
        return "speechmike-ptt-dictating", "Dictating"
    if status.get("mic") == "open":
        return "speechmike-ptt-open", "Microphone open"
    return "speechmike-ptt-muted", "Microphone muted"


def dictation_failed(previous: int, status: Optional[dict]) -> Tuple[int, bool]:
    """Return the new failure count and whether a dictation failed since the last check."""
    count = status.get("failed_starts", 0) if status else 0
    return count, count > previous


def tool_running(process: str) -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-x", process], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def launch(command: Sequence[str]) -> None:
    """Start a program that outlives the icon."""
    try:
        subprocess.Popen(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log.error("Could not start %s: %s", command[0], exc)


def notify(text: str) -> None:
    if shutil.which("notify-send"):
        launch(["notify-send", "--app-name=speechmike-ptt", text])


def handle_failed_dictation(tool: Optional[DictationConfig]) -> None:
    """Start the tool if that is why the dictation failed."""
    if tool is None or tool_running(tool.tool_process):
        notify("Dictation failed, see the log.")
        return
    launch(tool.tool_start)
    notify(f"{tool.tool_process} was not running. Starting it, try again in a few seconds.")


def log_command() -> Optional[Sequence[str]]:
    terminal = shutil.which("x-terminal-emulator")
    if terminal is None:
        return None
    return [terminal, "-e", "journalctl", "--user", "-u", "speechmike-ptt", "-f"]


def load_tool() -> Optional[DictationConfig]:
    """Return the dictation settings if they name a tool to manage."""
    try:
        dictation = load_config().dictation
    except ConfigError as exc:
        log.error("%s", exc)
        return None
    if dictation is None or not dictation.tool_start or not dictation.tool_process:
        return None
    return dictation


def main() -> int:
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as exc:
        print(f"The status icon needs PyGObject and AyatanaAppIndicator3: {exc}", file=sys.stderr)
        return 1

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)
    tool = load_tool()

    indicator = AppIndicator.Indicator.new(
        "speechmike-ptt",
        "speechmike-ptt-off",
        AppIndicator.IndicatorCategory.HARDWARE,
    )
    indicator.set_icon_theme_path(ICON_DIR)
    indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

    menu = Gtk.Menu()
    status_item = Gtk.MenuItem(label="")
    status_item.set_sensitive(False)
    menu.append(status_item)

    tool_item = start_item = None
    if tool is not None:
        tool_item = Gtk.MenuItem(label="")
        tool_item.set_sensitive(False)
        menu.append(tool_item)
        start_item = Gtk.MenuItem(label=f"Start {tool.tool_process}")
        start_item.connect("activate", lambda _item: launch(tool.tool_start))
        menu.append(start_item)

    menu.append(Gtk.SeparatorMenuItem())
    command = log_command()
    if command is not None:
        log_item = Gtk.MenuItem(label="Show log")
        log_item.connect("activate", lambda _item: launch(command))
        menu.append(log_item)
    restart_item = Gtk.MenuItem(label="Restart service")
    restart_item.connect(
        "activate",
        lambda _item: launch(["systemctl", "--user", "restart", "speechmike-ptt"]),
    )
    menu.append(restart_item)
    quit_item = Gtk.MenuItem(label="Quit icon")
    quit_item.connect("activate", lambda _item: Gtk.main_quit())
    menu.append(quit_item)
    menu.show_all()
    indicator.set_menu(menu)

    polls = 0
    last_icon = None
    failures, _ = dictation_failed(0, read_status())

    def refresh() -> bool:
        nonlocal polls, last_icon, failures
        status = read_status()
        failures, failed = dictation_failed(failures, status)
        if failed:
            handle_failed_dictation(tool)
            polls = 0  # show the new tool state right away
        icon, label = describe(status)
        if icon != last_icon:
            indicator.set_icon_full(icon, label)
            last_icon = icon
        status_item.set_label(label)
        if tool is not None and polls % TOOL_CHECK_EVERY_POLLS == 0:
            running = tool_running(tool.tool_process)
            state = "running" if running else "NOT running"
            tool_item.set_label(f"{tool.tool_process}: {state}")
            start_item.set_visible(not running)
        polls += 1
        return True

    refresh()
    GLib.timeout_add(POLL_MILLISECONDS, refresh)
    Gtk.main()
    return 0
