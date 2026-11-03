"""The push-to-talk daemon. Every failure path ends with the microphone muted."""

from __future__ import annotations

import logging
import signal
import time
from typing import Callable

from .audio import AudioError, list_sources, mask_serial, select_source, set_source_mute
from .devices import DeviceProfile
from .hid import DeviceGone, find_hid_interfaces, read_reports, udev_hint

log = logging.getLogger(__name__)

PTT_BUTTON = "REC"
MUTE_ATTEMPTS = 3
MUTE_RETRY_DELAY_SECONDS = 0.2

SetMute = Callable[[bool], None]


def mute(set_mute: SetMute) -> None:
    """Mute the microphone, retrying because a failure here leaves it open."""
    for attempt in range(1, MUTE_ATTEMPTS + 1):
        try:
            set_mute(True)
            return
        except AudioError as exc:
            log.warning("Muting failed (attempt %d of %d): %s", attempt, MUTE_ATTEMPTS, exc)
            if attempt == MUTE_ATTEMPTS:
                raise
            time.sleep(MUTE_RETRY_DELAY_SECONDS)


class PushToTalk:
    """Hold mode: the microphone is open exactly while the button is held."""

    def __init__(self, profile: DeviceProfile, button: str, set_mute: SetMute) -> None:
        self.profile = profile
        self.button = button
        self.set_mute = set_mute
        self.is_open = False

    def handle_report(self, report: bytes) -> None:
        pressed, _ = self.profile.decode_buttons(report)
        want_open = self.button in pressed
        if want_open == self.is_open:
            return
        if want_open:
            self._open()
        else:
            self._close()

    def _open(self) -> None:
        try:
            self.set_mute(False)
        except AudioError as exc:
            # The call may have taken effect even though it reported an error.
            log.error("Could not open the microphone: %s", exc)
            mute(self.set_mute)
            return
        self.is_open = True
        log.info("Microphone open")

    def _close(self) -> None:
        mute(self.set_mute)
        self.is_open = False
        log.info("Microphone muted")


def run() -> int:
    """Run until interrupted. Returns the process exit code."""
    interfaces = [item for item in find_hid_interfaces() if item.carries_buttons]
    if not interfaces:
        log.error("No supported device found.")
        return 1
    target = interfaces[0]

    try:
        source = select_source(list_sources(), target.profile, target.serial)
    except AudioError as exc:
        log.error("%s", exc)
        return 1
    if source is None:
        log.error("No unique audio source found for %s.", target.profile.name)
        return 1

    def set_mute(muted: bool) -> None:
        set_source_mute(source.name, muted)

    try:
        mute(set_mute)
    except AudioError as exc:
        log.error("Could not mute the microphone, exiting: %s", exc)
        return 1

    log.info("Using %s and %s", target.node, mask_serial(source.name, target.serial))
    log.info("Microphone muted. Hold %s to talk.", PTT_BUTTON)

    ptt = PushToTalk(target.profile, PTT_BUTTON, set_mute)
    exit_code = 0
    try:
        for report in read_reports(target.node):
            ptt.handle_report(report)
    except KeyboardInterrupt:
        pass
    except PermissionError:
        log.error("%s", udev_hint(target.profile))
        exit_code = 1
    except DeviceGone:
        log.error("Device disconnected.")
        exit_code = 1
    except AudioError as exc:
        log.error("%s", exc)
        exit_code = 1
    finally:
        # A second signal must not interrupt the final mute.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        try:
            mute(set_mute)
            log.info("Microphone muted, exiting.")
        except AudioError as exc:
            log.error("COULD NOT MUTE THE MICROPHONE: %s", exc)
            exit_code = 1
    return exit_code
