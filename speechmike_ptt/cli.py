"""Command line interface."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from datetime import datetime
from typing import List, Optional, Sequence

from . import __version__, daemon
from .audio import AudioError, list_sources, mask_serial, select_source
from .config import ConfigError, load_config
from .devices import DEVICES, DeviceProfile
from .hid import (
    DeviceGone,
    HidInterface,
    diff_reports,
    find_hid_interfaces,
    read_reports,
    udev_hint,
)


def format_report(profile: DeviceProfile, report: bytes, previous: Optional[bytes]) -> List[str]:
    """Return the output lines for one report."""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    pressed, unknown = profile.decode_buttons(report)
    labels = pressed + ([f"unknown 0x{unknown:04x}"] if unknown else [])
    lines = [f"{timestamp}  {report.hex(' ')}  buttons: {', '.join(labels) or 'none'}"]
    if len(report) != profile.report_size:
        lines.append(f"    unexpected length: {len(report)} bytes, expected {profile.report_size}")
    if previous is not None:
        for change in diff_reports(previous, report):
            line = f"    byte {change.index}: 0x{change.old:02x} -> 0x{change.new:02x}"
            if change.set_bits:
                line += f"  set 0x{change.set_bits:02x}"
            if change.cleared_bits:
                line += f"  cleared 0x{change.cleared_bits:02x}"
            lines.append(line)
    return lines


def _print_interfaces(interfaces: Sequence[HidInterface]) -> None:
    print("HID interfaces:")
    for item in interfaces:
        marker = "*" if item.carries_buttons else " "
        access = "readable" if item.readable else "NOT readable"
        role = "  <- buttons" if item.carries_buttons else ""
        print(
            f"  {marker} {item.node:<14} {item.profile.name} {item.profile.usb_id}  "
            f"interface {item.interface:02x}  {access}{role}"
        )


def _print_source(profile: DeviceProfile, serial: Optional[str]) -> None:
    print("Audio source:")
    try:
        source = select_source(list_sources(), profile, serial)
    except AudioError as exc:
        print(f"  error: {exc}")
        return
    if source is None:
        print("  no unique source found")
    else:
        print(f"  {mask_serial(source.name, serial)}  ({source.state})")


def run_debug() -> int:
    print(f"speechmike-ptt {__version__} debug mode (read-only, the mute state is not changed)\n")

    interfaces = find_hid_interfaces()
    if not interfaces:
        known = ", ".join(f"{d.name} {d.usb_id}" for d in DEVICES)
        print(f"No supported device found. Supported: {known}")
        return 1

    _print_interfaces(interfaces)
    print()

    button_interfaces = [item for item in interfaces if item.carries_buttons]
    if not button_interfaces:
        print("Device found, but not its button interface.")
        return 1
    target = button_interfaces[0]

    _print_source(target.profile, target.serial)
    print()

    if len(button_interfaces) > 1:
        print(f"{len(button_interfaces)} devices found, using only {target.node}.\n")
    if not target.readable:
        print(udev_hint(target.profile))
        return 1

    print(f"Reading {target.node}. Press buttons, Ctrl+C to stop.\n")
    previous = None
    try:
        for report in read_reports(target.node):
            if report != previous:  # every report arrives twice
                print("\n".join(format_report(target.profile, report, previous)))
                previous = report
    except PermissionError:
        print(udev_hint(target.profile))
        return 1
    except DeviceGone:
        print("Device disconnected.")
        return 1
    except KeyboardInterrupt:
        print("\nStopped.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="speechmike-ptt",
        description="Push-to-talk for the Philips SpeechMike on Linux. "
        "Keeps the microphone muted and opens it with a button.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="show detected devices and print button reports (read-only)",
    )
    parser.add_argument(
        "--tray",
        action="store_true",
        help="show a status icon (needs PyGObject and AyatanaAppIndicator3)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if args.tray:
        from . import tray

        logging.basicConfig(level=logging.INFO, format="%(message)s")
        return tray.main()

    sys.stdout.reconfigure(line_buffering=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Turn these into KeyboardInterrupt so that cleanup code runs.
    signal.signal(signal.SIGTERM, signal.default_int_handler)
    signal.signal(signal.SIGHUP, signal.default_int_handler)
    try:
        if args.debug:
            return run_debug()
        return daemon.run(load_config())
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
