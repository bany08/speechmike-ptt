"""The push-to-talk daemon. Every failure path ends with the microphone muted."""

from __future__ import annotations

import logging
import signal
import subprocess
import time
from typing import Callable, Optional, Sequence, Tuple

from .audio import (
    AudioError,
    Source,
    get_source_mute,
    list_sources,
    mask_serial,
    recording_active,
    select_source,
    set_source_mute,
)
from .config import Config, ConfigError, DictationConfig
from .devices import DeviceProfile
from .hid import DeviceGone, HidInterface, find_hid_interfaces, read_reports, udev_hint
from .notify import Notifier

log = logging.getLogger(__name__)

MUTE_ATTEMPTS = 3
MUTE_RETRY_DELAY_SECONDS = 0.2
DEVICE_POLL_SECONDS = 1.0
NOTIFY_FLUSH_SECONDS = 1.0
MUTE_CHECK_SECONDS = 2.0
ACTION_TIMEOUT_SECONDS = 2

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


def run_action(command: Sequence[str]) -> bool:
    """Run a configured command. Returns False if it failed, and never raises."""
    try:
        result = subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=ACTION_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.error("Action %s failed: %s", command[0], exc)
        return False
    if result.returncode != 0:
        log.error("Action %s failed with exit code %d", command[0], result.returncode)
        return False
    return True


class Dictation:
    """Starts and stops a speech-to-text tool while a button is held.

    Tools like Handy only offer a toggle, so a stop sent while nothing records
    would start a recording. With `is_recording`, the real state of the tool's
    audio stream decides, and the two cannot drift apart.
    """

    def __init__(
        self,
        config: DictationConfig,
        is_recording: Optional[Callable[[], bool]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.is_recording = is_recording
        self.clock = clock
        self.active = False  # from the start until the stop action, including the delay
        self.stop_at: Optional[float] = None
        self.was_held = False

    def _recording(self) -> bool:
        if self.is_recording is None:
            return False
        try:
            return self.is_recording()
        except AudioError as exc:
            log.warning("Could not check the recording state: %s", exc)
            return False

    def update(self, held: bool) -> None:
        if held and not self.was_held:
            self._press()
        elif self.was_held and not held and self.active:
            self.stop_at = self.clock() + self.config.release_delay_ms / 1000
        self.was_held = held

    def _press(self) -> None:
        if self.stop_at is not None:
            self.stop_at = None  # pressed again during the delay: keep recording
            return
        if self._recording():
            log.warning("The dictation tool is already recording.")
            self.active = True
        elif run_action(self.config.on_press):
            self.active = True
            log.info("Dictation started")

    def poll(self) -> None:
        if self.stop_at is not None and self.clock() >= self.stop_at:
            self.stop()

    def stop(self) -> None:
        self.stop_at = None
        if not self.active:
            return
        self.active = False
        if self.is_recording is not None and not self._recording():
            log.warning("The dictation tool was not recording, so no stop was sent.")
            return
        run_action(self.config.on_release)
        log.info("Dictation stopped")

    def stop_stray_recording(self) -> None:
        """Stop a recording that runs while the microphone is muted."""
        if not self.active and self._recording():
            log.warning("Stopping a recording that runs without the button.")
            run_action(self.config.on_release)

    def seconds_until_stop(self) -> Optional[float]:
        if self.stop_at is None:
            return None
        return max(0.0, self.stop_at - self.clock())


class PushToTalk:
    """Decide from button reports whether the microphone is open.

    hold: open exactly while the button is held.
    toggle: each press of the button switches between open and muted.

    With dictation, its button also opens the microphone, for as long as the
    dictation is active. The microphone is open if either one needs it.
    """

    def __init__(
        self,
        profile: DeviceProfile,
        button: str,
        set_mute: SetMute,
        mode: str = "hold",
        source_exists: Optional[Callable[[], bool]] = None,
        on_change: Optional[Callable[[bool], None]] = None,
        is_muted: Optional[Callable[[], bool]] = None,
        dictation: Optional[Dictation] = None,
    ) -> None:
        self.profile = profile
        self.button = button
        self.set_mute = set_mute
        self.mode = mode
        self.source_exists = source_exists
        self.on_change = on_change or (lambda is_open: None)
        self.is_muted = is_muted
        self.check_failing = False
        self.dictation = dictation
        self.is_open = False
        self.talking = False
        self.was_held = False

    def handle_report(self, report: bytes) -> None:
        pressed, _ = self.profile.decode_buttons(report)
        held = self.button in pressed
        if self.mode == "toggle":
            self.talking = self.talking != (held and not self.was_held)
        else:
            self.talking = held
        self.was_held = held

        if self.dictation is None:
            self._apply()
            return
        dictation_held = self.dictation.config.button in pressed
        # The microphone must be open before the tool starts to record.
        self._apply(dictation_held and not self.dictation.was_held)
        self.dictation.update(dictation_held)
        self._apply()

    def poll(self) -> None:
        """Handle timers. Call this after every report and timeout."""
        if self.dictation is not None:
            self.dictation.poll()
            self._apply()

    def next_timeout(self) -> float:
        wait = self.dictation.seconds_until_stop() if self.dictation else None
        return MUTE_CHECK_SECONDS if wait is None else min(wait, MUTE_CHECK_SECONDS)

    def shutdown(self) -> None:
        """Stop a running dictation before the microphone gets muted."""
        if self.dictation is not None:
            self.dictation.stop()

    def _apply(self, also_open: bool = False) -> None:
        dictating = self.dictation is not None and self.dictation.active
        want_open = self.talking or dictating or also_open
        if want_open == self.is_open:
            return
        if want_open:
            self._open()
        else:
            self._close()

    def mute(self) -> None:
        mute(self.set_mute, self.source_exists)

    def enforce_mute(self) -> None:
        """Mute again if something else opened the microphone behind our back."""
        if self.is_open:
            return
        if self.dictation is not None:
            self.dictation.stop_stray_recording()
        if self.is_muted is None:
            return
        try:
            muted = self.is_muted()
        except AudioError as exc:
            # For example an audio server restart. Try again on the next check.
            if not self.check_failing:
                log.warning("Could not check the mute state: %s", exc)
            self.check_failing = True
            return
        self.check_failing = False
        if not muted:
            log.warning("The microphone was unmuted from outside. Muting it again.")
            self.mute()

    def _open(self) -> None:
        try:
            self.set_mute(False)
        except AudioError as exc:
            # The call may have taken effect even though it reported an error.
            log.error("Could not open the microphone: %s", exc)
            self.talking = False
            self.mute()
            return
        self.is_open = True
        log.info("Microphone open")
        self.on_change(True)

    def _close(self) -> None:
        self.mute()
        self.is_open = False
        log.info("Microphone muted")
        self.on_change(False)


def find_target(config: Config) -> Tuple[HidInterface, Source]:
    """Return the device and its source. Raises NotReady if one is missing."""
    interfaces = [item for item in find_hid_interfaces() if item.carries_buttons]
    if not interfaces:
        raise NotReady("No supported device found.")
    target = interfaces[0]

    if config.button not in target.profile.buttons:
        known = ", ".join(target.profile.buttons)
        raise ConfigError(f"Unknown button {config.button} for {target.profile.name}. Known: {known}")
    if config.dictation and config.dictation.button not in target.profile.buttons:
        known = ", ".join(target.profile.buttons)
        raise ConfigError(
            f"Unknown button {config.dictation.button} for {target.profile.name}. Known: {known}"
        )

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


def run_session(
    target: HidInterface,
    source: Source,
    config: Config,
    notifier: Optional[Notifier] = None,
) -> None:
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

    dictation = None
    if config.dictation is not None:
        recorder = config.dictation.recorder
        is_recording = (lambda: recording_active(source.index, recorder)) if recorder else None
        dictation = Dictation(config.dictation, is_recording)
        log.info("Hold %s to dictate.", config.dictation.button)

    ptt = PushToTalk(
        target.profile,
        config.button,
        set_mute,
        config.mode,
        source_exists,
        notifier,
        lambda: get_source_mute(source.name),
        dictation,
    )
    try:
        for report in read_reports(target.node, ptt.next_timeout):
            if report is None:
                ptt.enforce_mute()
            else:
                ptt.handle_report(report)
            ptt.poll()
    finally:
        # A signal must not interrupt the final mute.
        handled = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        previous = [signal.signal(number, signal.SIG_IGN) for number in handled]
        try:
            ptt.shutdown()
            mute(set_mute, source_exists)
            log.info("Microphone muted.")
            if ptt.is_open:
                ptt.on_change(False)
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
    notifier = Notifier() if config.notify else None

    def report_once(message: str, level: int = logging.INFO) -> None:
        nonlocal last_message
        if message != last_message:
            log.log(level, "%s", message)
            last_message = message

    try:
        while True:
            try:
                target, source = find_target(config)
                run_session(target, source, config, notifier)
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
    finally:
        if notifier is not None:
            notifier.flush(NOTIFY_FLUSH_SECONDS)
