"""Find the audio source of a device through pactl."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .devices import DeviceProfile

PACTL_TIMEOUT_SECONDS = 5

# alsa_input.usb-Vendor_Product_<SERIAL>-00.profile
_SERIAL_IN_NAME = re.compile(r"_[^_.\-]+(-\d+\.)")


class AudioError(Exception):
    """pactl is missing or failed."""


@dataclass(frozen=True)
class Source:
    name: str
    state: str


def parse_sources(text: str) -> List[Source]:
    """Parse the output of `pactl list short sources`."""
    sources = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) >= 2:
            sources.append(Source(fields[1], fields[4] if len(fields) >= 5 else "unknown"))
    return sources


def list_sources() -> List[Source]:
    # pactl output is localized, so pin the locale.
    env = {**os.environ, "LC_ALL": "C"}
    try:
        result = subprocess.run(
            ["pactl", "list", "short", "sources"],
            env=env,
            capture_output=True,
            text=True,
            timeout=PACTL_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise AudioError("pactl not found (package pulseaudio-utils)") from exc
    except subprocess.TimeoutExpired as exc:
        raise AudioError("pactl did not respond") from exc
    if result.returncode != 0:
        raise AudioError(f"pactl failed: {result.stderr.strip()}")
    return parse_sources(result.stdout)


def select_source(
    sources: Sequence[Source], profile: DeviceProfile, serial: Optional[str] = None
) -> Optional[Source]:
    """Return the device's microphone source, or None if there is no unique one.

    An ambiguous match returns None on purpose: guessing could leave the wrong
    microphone open.
    """
    candidates = [
        source
        for source in sources
        if source.name.startswith("alsa_input.")
        and profile.source_keyword in source.name
        and not source.name.endswith(".monitor")
    ]
    if len(candidates) > 1 and serial:
        candidates = [source for source in candidates if serial in source.name]
    return candidates[0] if len(candidates) == 1 else None


def mask_serial(name: str, serial: Optional[str] = None) -> str:
    """Hide the serial number in a source name, for output that may be shared."""
    if serial:
        name = name.replace(serial, "<SERIAL>")
    if "<SERIAL>" in name:
        return name
    return _SERIAL_IN_NAME.sub(r"_<SERIAL>\1", name)
