"""Find SpeechMike hidraw interfaces through sysfs and read their reports."""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from itertools import zip_longest
from typing import Iterator, List, Optional, Sequence

from .devices import DEVICES, DeviceProfile

# Larger than any known report, so that a longer report is shown and not cut off.
READ_BUFFER_SIZE = 64


class DeviceGone(Exception):
    """The device disappeared, for example because it was unplugged."""


@dataclass(frozen=True)
class HidInterface:
    node: str  # e.g. /dev/hidraw9
    profile: DeviceProfile
    interface: int  # USB bInterfaceNumber
    serial: Optional[str]
    readable: bool

    @property
    def carries_buttons(self) -> bool:
        return self.interface == self.profile.button_interface


@dataclass(frozen=True)
class ByteChange:
    index: int
    old: int
    new: int

    @property
    def set_bits(self) -> int:
        return self.new & ~self.old

    @property
    def cleared_bits(self) -> int:
        return self.old & ~self.new


def _read_attr(directory: str, name: str) -> Optional[str]:
    try:
        with open(os.path.join(directory, name), encoding="ascii", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return None


def _read_hex_attr(directory: str, name: str) -> Optional[int]:
    value = _read_attr(directory, name)
    try:
        return int(value, 16) if value is not None else None
    except ValueError:
        return None


def _hidraw_number(path: str) -> int:
    suffix = os.path.basename(path)[len("hidraw"):]
    return int(suffix) if suffix.isdigit() else -1


def find_hid_interfaces(
    sysfs_root: str = "/sys",
    dev_root: str = "/dev",
    devices: Sequence[DeviceProfile] = DEVICES,
) -> List[HidInterface]:
    """Return all hidraw interfaces that belong to a supported device."""
    root = os.path.realpath(sysfs_root)
    found = []
    entries = glob.glob(os.path.join(sysfs_root, "class", "hidraw", "hidraw*"))
    for entry in sorted(entries, key=_hidraw_number):
        interface = None
        vendor_id = product_id = None
        serial = None

        # Walk up from the HID device. The USB interface directory holds
        # bInterfaceNumber, and its parent USB device holds the IDs.
        path = os.path.realpath(os.path.join(entry, "device"))
        while path.startswith(root + os.sep):
            if interface is None:
                interface = _read_hex_attr(path, "bInterfaceNumber")
            vendor_id = _read_hex_attr(path, "idVendor")
            product_id = _read_hex_attr(path, "idProduct")
            if vendor_id is not None and product_id is not None:
                serial = _read_attr(path, "serial")
                break
            path = os.path.dirname(path)

        if interface is None or vendor_id is None or product_id is None:
            continue
        for profile in devices:
            if (profile.vendor_id, profile.product_id) == (vendor_id, product_id):
                node = os.path.join(dev_root, os.path.basename(entry))
                found.append(
                    HidInterface(
                        node=node,
                        profile=profile,
                        interface=interface,
                        serial=serial,
                        readable=os.access(node, os.R_OK),
                    )
                )
                break
    return found


def udev_hint(profile: DeviceProfile) -> str:
    return (
        f"No read access to the device. Add this udev rule as "
        f"/etc/udev/rules.d/70-speechmike.rules and replug the device:\n\n"
        f'  SUBSYSTEM=="hidraw", ATTRS{{idVendor}}=="{profile.vendor_id:04x}", '
        f'ATTRS{{idProduct}}=="{profile.product_id:04x}", TAG+="uaccess"\n'
    )


def read_reports(node: str) -> Iterator[bytes]:
    """Yield reports from a hidraw node until the device goes away.

    Raises PermissionError if the node cannot be opened and DeviceGone once
    the device disappears.
    """
    try:
        fd = os.open(node, os.O_RDONLY)
    except FileNotFoundError as exc:
        raise DeviceGone(node) from exc
    try:
        while True:
            try:
                data = os.read(fd, READ_BUFFER_SIZE)
            except OSError as exc:
                raise DeviceGone(node) from exc
            if not data:
                raise DeviceGone(node)
            yield data
    finally:
        os.close(fd)


def diff_reports(old: bytes, new: bytes) -> List[ByteChange]:
    """Return the bytes that differ between two reports."""
    return [
        ByteChange(index, a, b)
        for index, (a, b) in enumerate(zip_longest(old, new, fillvalue=0))
        if a != b
    ]
