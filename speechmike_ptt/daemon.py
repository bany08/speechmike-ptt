"""The push-to-talk daemon. Every failure path ends with the microphone muted."""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from typing import Callable, Dict, Optional, Sequence, Tuple

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
from .config import Config, ConfigError, ConfigWatcher, DictationConfig
from .devices import DeviceProfile
from .hid import DeviceGone, HidInterface, find_hid_interfaces, read_reports, udev_hint
from .notify import Notifier
from .status import StatusFile

log = logging.getLogger(__name__)

MUTE_ATTEMPTS = 3
MUTE_RETRY_DELAY_SECONDS = 0.2
DEVICE_POLL_SECONDS = 1.0
NOTIFY_FLUSH_SECONDS = 1.0
MUTE_CHECK_SECONDS = 2.0
CHECK_TOLERANCE_SECONDS = 0.05
ACTION_TIMEOUT_SECONDS = 2

SetMute = Callable[[bool], None]


class NotReady(Exception):
    """The device or its audio source is not available yet."""


class ConfigChanged(Exception):
    """The configuration file changed. The session restarts with the new one."""

    def __init__(self, config: Config) -> None:
        super().__init__("configuration changed")
        self.config = config


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


def display_env() -> Optional[Dict[str, str]]:
    """Return an environment with the desktop display, for commands that type keys.

    The service often starts before the desktop exports DISPLAY, so ask the
    systemd user manager, which has it once the session is up.
    """
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return None
    try:
        output = subprocess.run(
            ["systemctl", "--user", "show-environment"],
            capture_output=True,
            text=True,
            timeout=ACTION_TIMEOUT_SECONDS,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    found = {}
    for line in output.splitlines():
        name, _, value = line.partition("=")
        if name in ("DISPLAY", "XAUTHORITY", "WAYLAND_DISPLAY"):
            found[name] = value
    return {**os.environ, **found} if found else None


def run_action(command: Sequence[str], env: Optional[Dict[str, str]] = None) -> bool:
    """Run a configured command. Returns False if it failed, and never raises."""
    try:
        result = subprocess.run(
            list(command),
            env=env,
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
        self.failed_starts = 0

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
        else:
            self.failed_starts += 1

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
        button: Optional[str],
        set_mute: SetMute,
        mode: str = "hold",
        source_exists: Optional[Callable[[], bool]] = None,
        on_change: Optional[Callable[[bool], None]] = None,
        is_muted: Optional[Callable[[], bool]] = None,
        dictation: Optional[Dictation] = None,
        on_state: Optional[Callable[[bool, bool], None]] = None,
        key_buttons: Sequence[str] = (),
        on_key: Optional[Callable[[str], None]] = None,
        repeat_buttons: Sequence[str] = (),
        repeat_delay: float = 0.5,
        repeat_interval: float = 0.04,
        clock: Callable[[], float] = time.monotonic,
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
        self.on_state = on_state or (lambda is_open, dictating: None)
        self.key_buttons = tuple(key_buttons)
        self.on_key = on_key or (lambda name: None)
        self.keys_held: set = set()
        self.repeat_buttons = set(repeat_buttons)
        self.repeat_delay = repeat_delay
        self.repeat_interval = repeat_interval
        self.clock = clock
        self.repeat_at: Dict[str, float] = {}  # held repeating key -> time of the next press
        self.is_open = False
        self.talking = False
        self.was_held = False

    def handle_report(self, report: bytes) -> None:
        pressed, _ = self.profile.decode_buttons(report)
        self._handle_keys(pressed)
        held = self.button is not None and self.button in pressed
        if self.mode == "toggle":
            self.talking = self.talking != (held and not self.was_held)
        else:
            self.talking = held
        self.was_held = held

        if self.dictation is None:
            self._apply()
            self._publish()
            return
        dictation_held = self.dictation.config.button in pressed
        # The microphone must be open before the tool starts to record.
        self._apply(dictation_held and not self.dictation.was_held)
        self.dictation.update(dictation_held)
        self._apply()
        self._publish()

    def _handle_keys(self, pressed: Sequence[str]) -> None:
        held = {name for name in self.key_buttons if name in pressed}
        for name in sorted(held - self.keys_held):
            self.on_key(name)
            if name in self.repeat_buttons:
                self.repeat_at[name] = self.clock() + self.repeat_delay
        for name in self.keys_held - held:
            self.repeat_at.pop(name, None)
        self.keys_held = held

    def _repeat_keys(self) -> None:
        now = self.clock()
        for name, due in list(self.repeat_at.items()):
            if now >= due:
                self.on_key(name)
                # Schedule from now, so a slow command cannot cause a burst.
                self.repeat_at[name] = self.clock() + self.repeat_interval

    def poll(self) -> None:
        """Handle timers. Call this after every report and timeout."""
        self._repeat_keys()
        if self.dictation is not None:
            self.dictation.poll()
            self._apply()
            self._publish()

    def _publish(self) -> None:
        self.on_state(self.is_open, self.dictation is not None and self.dictation.active)

    def next_timeout(self) -> float:
        waits = [MUTE_CHECK_SECONDS]
        stop = self.dictation.seconds_until_stop() if self.dictation else None
        if stop is not None:
            waits.append(stop)
        if self.repeat_at:
            waits.append(max(0.0, min(self.repeat_at.values()) - self.clock()))
        return min(waits)

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

    if config.button is not None and config.button not in target.profile.buttons:
        known = ", ".join(target.profile.buttons)
        raise ConfigError(f"Unknown button {config.button} for {target.profile.name}. Known: {known}")
    if config.dictation and config.dictation.button not in target.profile.buttons:
        known = ", ".join(target.profile.buttons)
        raise ConfigError(
            f"Unknown button {config.dictation.button} for {target.profile.name}. Known: {known}"
        )
    for name, _command in config.key_buttons:
        if name not in target.profile.buttons:
            known = ", ".join(target.profile.buttons)
            raise ConfigError(f"Unknown button {name} for {target.profile.name}. Known: {known}")

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
    status: Optional[StatusFile] = None,
    watcher: Optional[ConfigWatcher] = None,
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
    status = status or StatusFile(None)
    status.update(device=True, mic="muted", dictating=False)
    log.info("Using %s and %s", target.node, mask_serial(source.name, target.serial))
    if config.button is None:
        log.info("Microphone muted. No button is set to talk.")
    else:
        action = "Hold" if config.mode == "hold" else "Press"
        log.info("Microphone muted. %s %s to talk (%s mode).", action, config.button, config.mode)
    key_commands = dict(config.key_buttons)
    for name, command in config.key_buttons:
        repeats = " (repeats while held)" if name in config.repeat_buttons else ""
        log.info("Press %s for key %s%s.", name, command[-1], repeats)

    def press_key(name: str) -> None:
        if run_action(key_commands[name], display_env()):
            log.info("Key %s sent", key_commands[name][-1])

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
        lambda is_open, dictating: status.update(
            mic="open" if is_open else "muted",
            dictating=dictating,
            failed_starts=dictation.failed_starts if dictation else 0,
        ),
        tuple(key_commands),
        press_key,
        config.repeat_buttons,
        config.repeat_delay_ms / 1000,
        config.repeat_interval_ms / 1000,
    )
    last_check = float("-inf")
    try:
        for report in read_reports(target.node, ptt.next_timeout):
            if report is None:
                # Timers fire often while a key repeats. Check the rest at the usual pace.
                now = time.monotonic()
                if now - last_check >= MUTE_CHECK_SECONDS - CHECK_TOLERANCE_SECONDS:
                    last_check = now
                    ptt.enforce_mute()
                    changed = watcher.poll() if watcher else None
                    if changed is not None:
                        raise ConfigChanged(changed)
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


def run(config: Config = Config(), watcher: Optional[ConfigWatcher] = None) -> int:
    """Serve the device across reconnects until interrupted. Returns the exit code.

    With a watcher, a changed configuration file applies without a restart.
    """
    last_message = None
    notifier = Notifier() if config.notify else None
    status = StatusFile()
    status.update()

    def report_once(message: str, level: int = logging.INFO) -> None:
        nonlocal last_message
        if message != last_message:
            log.log(level, "%s", message)
            last_message = message

    try:
        while True:
            try:
                target, source = find_target(config)
                run_session(target, source, config, notifier, status, watcher)
            except ConfigChanged as change:
                config = change.config
                log.info("Configuration reloaded.")
                last_message = None
                continue
            except NotReady as exc:
                status.update(device=False, mic="muted", dictating=False)
                report_once(f"{exc} Waiting.")
            except PermissionError:
                # Also happens briefly after replugging, until udev has set the ACL.
                report_once(udev_hint(target.profile), logging.ERROR)
            except DeviceGone:
                status.update(device=False, mic="muted", dictating=False)
                log.info("Device disconnected. Waiting.")
                last_message = None
            if watcher is not None:
                changed = watcher.poll()
                if changed is not None:
                    config = changed
                    log.info("Configuration reloaded.")
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
        status.clear()
        if notifier is not None:
            notifier.flush(NOTIFY_FLUSH_SECONDS)
