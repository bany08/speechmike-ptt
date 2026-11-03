"""The push-to-talk daemon. Every failure path ends with the microphone muted."""

from __future__ import annotations

import logging
import signal
import time
from typing import Callable, Optional, Tuple

from .audio import (
    AudioError,
    Source,
    list_sources,
    mask_serial,
    select_source,
    set_source_mute,
)
from .config import Config, ConfigError
from .devices import DeviceProfile
from .hid import DeviceGone, HidInterface, find_hid_interfaces, read_reports, udev_hint

log = logging.getLogger(__name__)

MUTE_ATTEMPTS = 3
MUTE_RETRY_DELAY_SECONDS = 0.2
DEVICE_POLL_SECONDS = 1.0

SetMute = Callable[[bool], None]


class NotReady(Exception):
    """The device or its audio source is not available yet."""


def mute(set_mute: SetMute, source_exists: Optional[Callable[[], bool]] = None) -> None:
    """Mute the microphone, retrying because a failure here leaves it open.

    A source that no longer exists cannot listen, so that is not a failure.
    """
    for attempt in range(1, MUTE_ATTEMPTS + 1):
        try:
            set_mute(True)
            return
        except AudioError as exc:
            if source_exists is not None and not source_exists():
                log.info("The audio source is gone.")
                return
            log.warning("Muting failed (attempt %d of %d): %s", attempt, MUTE_ATTEMPTS, exc)
            if attempt == MUTE_ATTEMPTS:
                raise
            time.sleep(MUTE_RETRY_DELAY_SECONDS)


class PushToTalk:
    """Decide from button reports whether the microphone is open.

    hold: open exactly while the button is held.
    toggle: each press of the button switches between open and muted.
    """

    def __init__(
        self,
        profile: DeviceProfile,
        button: str,
        set_mute: SetMute,
        mode: str = "hold",
        source_exists: Optional[Callable[[], bool]] = None,
    ) -> None:
        self.profile = profile
        self.button = button
        self.set_mute = set_mute
        self.mode = mode
        self.source_exists = source_exists
        self.is_open = False
        self.was_held = False

    def handle_report(self, report: bytes) -> None:
        pressed, _ = self.profile.decode_buttons(report)
        held = self.button in pressed
        if self.mode == "toggle":
            want_open = self.is_open != (held and not self.was_held)
        else:
            want_open = held
        self.was_held = held
        if want_open == self.is_open:
            return
        if want_open:
            self._open()
        else:
            self._close()

    def mute(self) -> None:
        mute(self.set_mute, self.source_exists)

    def _open(self) -> None:
        try:
            self.set_mute(False)
        except AudioError as exc:
            # The call may have taken effect even though it reported an error.
            log.error("Could not open the microphone: %s", exc)
            self.mute()
            return
        self.is_open = True
        log.info("Microphone open")

    def _close(self) -> None:
        self.mute()
        self.is_open = False
        log.info("Microphone muted")


def find_target(config: Config) -> Tuple[HidInterface, Source]:
    """Return the device and its source. Raises NotReady if one is missing."""
    interfaces = [item for item in find_hid_interfaces() if item.carries_buttons]
    if not interfaces:
        raise NotReady("No supported device found.")
    target = interfaces[0]

    if config.button not in target.profile.buttons:
        known = ", ".join(target.profile.buttons)
        raise ConfigError(f"Unknown button {config.button} for {target.profile.name}. Known: {known}")

    try:
        sources = list_sources()
    except AudioError as exc:
        raise NotReady(str(exc)) from exc
    if config.source:
        source = next((item for item in sources if item.name == config.source), None)
        if source is None:
            raise NotReady("The configured audio source does not exist.")
    else:
        source = select_source(sources, target.profile, target.serial)
        if source is None:
            raise NotReady(f"No unique audio source found for {target.profile.name}.")
    return target, source


def run_session(target: HidInterface, source: Source, config: Config) -> None:
    """Serve one connected device until it goes away. Always ends muted."""

    def set_mute(muted: bool) -> None:
        set_source_mute(source.name, muted)

    def source_exists() -> bool:
        try:
            return any(item.name == source.name for item in list_sources())
        except AudioError:
            return True  # unknown, so assume the worst

    mute(set_mute, source_exists)
    log.info("Using %s and %s", target.node, mask_serial(source.name, target.serial))
    action = "Hold" if config.mode == "hold" else "Press"
    log.info("Microphone muted. %s %s to talk (%s mode).", action, config.button, config.mode)

    ptt = PushToTalk(target.profile, config.button, set_mute, config.mode, source_exists)
    try:
        for report in read_reports(target.node):
            ptt.handle_report(report)
    finally:
        # A signal must not interrupt the final mute.
        handled = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        previous = [signal.signal(number, signal.SIG_IGN) for number in handled]
        try:
            mute(set_mute, source_exists)
            log.info("Microphone muted.")
        except AudioError as exc:
            log.error("COULD NOT MUTE THE MICROPHONE: %s", exc)
            raise
        finally:
            for number, handler in zip(handled, previous):
                signal.signal(number, handler)


def wait_for_device() -> None:
    time.sleep(DEVICE_POLL_SECONDS)


def run(config: Config = Config()) -> int:
    """Serve the device across reconnects until interrupted. Returns the exit code."""
    last_message = None

    def report_once(message: str, level: int = logging.INFO) -> None:
        nonlocal last_message
        if message != last_message:
            log.log(level, "%s", message)
            last_message = message

    try:
        while True:
            try:
                target, source = find_target(config)
                run_session(target, source, config)
            except NotReady as exc:
                report_once(f"{exc} Waiting.")
            except PermissionError:
                # Also happens briefly after replugging, until udev has set the ACL.
                report_once(udev_hint(target.profile), logging.ERROR)
            except DeviceGone:
                log.info("Device disconnected. Waiting.")
                last_message = None
            wait_for_device()
    except KeyboardInterrupt:
        log.info("Exiting.")
        return 0
    except ConfigError as exc:
        log.error("%s", exc)
        return 1
    except AudioError as exc:
        log.error("Exiting because of an audio error: %s", exc)
        return 1
