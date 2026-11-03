"""Table of supported devices.

To add a model, append a DeviceProfile to DEVICES. Use `--debug` to find the
values, and only list buttons that were observed on real hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, Tuple


@dataclass(frozen=True)
class DeviceProfile:
    name: str
    vendor_id: int
    product_id: int
    button_interface: int  # USB bInterfaceNumber that carries the button reports
    report_size: int
    button_offset: int  # index of the first byte of the button bitmask
    button_length: int  # size of the bitmask in bytes, big-endian
    buttons: Mapping[str, int]  # button name -> bit mask
    source_keyword: str  # text that identifies the device in audio source names

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"

    def decode_buttons(self, report: bytes) -> Tuple[List[str], int]:
        """Return the pressed button names and any set bits with no known button."""
        end = self.button_offset + self.button_length
        if len(report) < end:
            return [], 0
        mask = int.from_bytes(report[self.button_offset:end], "big")
        pressed = [name for name, bit in self.buttons.items() if mask & bit]
        known = 0
        for bit in self.buttons.values():
            known |= bit
        return pressed, mask & ~known


DEVICES: Tuple[DeviceProfile, ...] = (
    DeviceProfile(
        name="SpeechMike III",
        vendor_id=0x0911,
        product_id=0x0C1C,
        button_interface=4,
        report_size=9,
        button_offset=7,
        button_length=2,
        buttons={
            "REC": 0x0001,
            "PLAY": 0x0004,
            "FWD": 0x0008,
            "REW": 0x0010,
            "EOL_PRIO": 0x0020,
            "INS_OVR": 0x0040,
            "F1": 0x0200,
            "F2": 0x0400,
            "F3": 0x0800,
        },
        source_keyword="SpeechMike",
    ),
)
