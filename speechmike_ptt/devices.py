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
    button_byte: int  # index of the bitmask byte within a report
    buttons: Mapping[str, int]  # button name -> bit mask
    source_keyword: str  # text that identifies the device in audio source names

    @property
    def usb_id(self) -> str:
        return f"{self.vendor_id:04x}:{self.product_id:04x}"

    def decode_buttons(self, report: bytes) -> Tuple[List[str], int]:
        """Return the pressed button names and any set bits with no known button."""
        if len(report) <= self.button_byte:
            return [], 0
        mask = report[self.button_byte]
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
        button_byte=8,
        buttons={"REC": 0x01, "PLAY": 0x04},
        source_keyword="SpeechMike",
    ),
)
