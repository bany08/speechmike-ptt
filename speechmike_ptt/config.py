"""Load the configuration file.

Invalid settings are an error and never fall back silently: a typo must not
leave the user in a mode they did not choose.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import shlex
from dataclasses import dataclass, replace
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

SECTION = "speechmike-ptt"
DICTATION_SECTION = "dictation"
BUTTONS_SECTION = "buttons"
FUNCTIONS = ("talk", "dictate", "send", "key <name>", "repeat <name>", "none")
DEFAULT_REPEAT_DELAY_MS = 500
DEFAULT_REPEAT_INTERVAL_MS = 40
DEFAULT_SEND_COMMAND = ("xdotool", "key", "--clearmodifiers", "Return")
KEY_NAME = re.compile(r"^[A-Za-z0-9_+]+$")  # an xdotool key name such as BackSpace or ctrl+z
MODES = ("hold", "toggle")
DEFAULT_RELEASE_DELAY_MS = 400


class ConfigError(Exception):
    """The configuration file is invalid."""


@dataclass(frozen=True)
class DictationConfig:
    button: Optional[str]
    on_press: Tuple[str, ...]  # command, already split into arguments
    on_release: Tuple[str, ...]
    release_delay_ms: int = DEFAULT_RELEASE_DELAY_MS
    recorder: Optional[str] = None  # text in the application name of the tool's audio stream
    tool_start: Optional[Tuple[str, ...]] = None  # lets the status icon start the tool
    tool_process: Optional[str] = None  # process name the status icon looks for


@dataclass(frozen=True)
class Config:
    mode: str = "hold"
    button: Optional[str] = "REC"  # the talk button, None if no button talks
    source: Optional[str] = None  # overrides the automatic source detection
    notify: bool = False  # desktop notification on every switch
    dictation: Optional[DictationConfig] = None
    key_buttons: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()  # button and the command it runs
    repeat_buttons: Tuple[str, ...] = ()  # key buttons that repeat while held
    repeat_delay_ms: int = DEFAULT_REPEAT_DELAY_MS
    repeat_interval_ms: int = DEFAULT_REPEAT_INTERVAL_MS
    send_command: Tuple[str, ...] = DEFAULT_SEND_COMMAND


def config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "speechmike-ptt", "config.ini")


def _command(path: str, values: dict, key: str) -> Tuple[str, ...]:
    text = values.get(key)
    if not text:
        raise ConfigError(f"{path}: missing {key}")
    try:
        command = tuple(shlex.split(text))
    except ValueError as exc:
        raise ConfigError(f"{path}: invalid {key}: {exc}") from exc
    if not command:
        raise ConfigError(f"{path}: missing {key}")
    return command


def _milliseconds(path: str, values: dict, key: str, default: int) -> int:
    try:
        value = int(values.get(key, default))
    except ValueError as exc:
        raise ConfigError(f"{path}: {key} must be a number") from exc
    if value < 0:
        raise ConfigError(f"{path}: {key} must not be negative")
    return value


def _load_buttons(path: str, parser: configparser.ConfigParser) -> Optional[Dict[str, str]]:
    if not parser.has_section(BUTTONS_SECTION):
        return None
    buttons = {}
    for name, function in parser[BUTTONS_SECTION].items():
        words = function.split()
        if len(words) == 2 and words[0].lower() in ("key", "repeat"):
            if not KEY_NAME.match(words[1]):
                raise ConfigError(f"{path}: {name} = {function!r}, invalid key name")
            buttons[name.upper()] = f"{words[0].lower()} {words[1]}"
            continue
        function = function.lower()
        if function not in FUNCTIONS or "<" in function:
            raise ConfigError(
                f"{path}: {name} = {function!r}, must be one of {', '.join(FUNCTIONS)}"
            )
        buttons[name.upper()] = function
    for function in ("talk", "dictate"):
        names = [name for name, value in buttons.items() if value == function]
        if len(names) > 1:
            raise ConfigError(f"{path}: only one button can {function}, not {', '.join(names)}")
    return buttons


def _load_dictation(path: str, parser: configparser.ConfigParser) -> Optional[DictationConfig]:
    if not parser.has_section(DICTATION_SECTION):
        return None
    values = dict(parser[DICTATION_SECTION])
    known = {
        "button",
        "on_press",
        "on_release",
        "release_delay_ms",
        "recorder",
        "tool_start",
        "tool_process",
    }
    unknown_keys = set(values) - known
    if unknown_keys:
        raise ConfigError(f"{path}: unknown setting {sorted(unknown_keys)[0]!r}")
    button = values.get("button", "").upper() or None
    if button is None and not parser.has_section(BUTTONS_SECTION):
        raise ConfigError(f"{path}: [{DICTATION_SECTION}] needs button")
    try:
        delay = int(values.get("release_delay_ms", DEFAULT_RELEASE_DELAY_MS))
    except ValueError as exc:
        raise ConfigError(f"{path}: release_delay_ms must be a number") from exc
    if delay < 0:
        raise ConfigError(f"{path}: release_delay_ms must not be negative")
    return DictationConfig(
        button=button,
        on_press=_command(path, values, "on_press"),
        on_release=_command(path, values, "on_release"),
        release_delay_ms=delay,
        recorder=values.get("recorder") or None,
        tool_start=_command(path, values, "tool_start") if values.get("tool_start") else None,
        tool_process=values.get("tool_process") or None,
    )


def load_config(path: Optional[str] = None) -> Config:
    """Read the configuration. A missing file means defaults."""
    path = path or config_path()
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as f:
            parser.read_file(f)
    except FileNotFoundError:
        return Config()
    except (OSError, UnicodeDecodeError, configparser.Error) as exc:
        raise ConfigError(f"Cannot read {path}: {exc}") from exc

    unknown_sections = set(parser.sections()) - {SECTION, DICTATION_SECTION, BUTTONS_SECTION}
    if unknown_sections:
        raise ConfigError(f"{path}: unknown section {sorted(unknown_sections)[0]!r}")
    dictation = _load_dictation(path, parser)
    buttons = _load_buttons(path, parser)
    values = dict(parser[SECTION]) if parser.has_section(SECTION) else {}
    unknown_keys = set(values) - {
        "mode",
        "button",
        "source",
        "notify",
        "send_command",
        "repeat_delay_ms",
        "repeat_interval_ms",
    }
    if unknown_keys:
        raise ConfigError(f"{path}: unknown setting {sorted(unknown_keys)[0]!r}")

    defaults = Config()
    mode = values.get("mode", defaults.mode).lower()
    if mode not in MODES:
        raise ConfigError(f"{path}: mode must be one of {', '.join(MODES)}, not {mode!r}")
    button = values.get("button", defaults.button).upper()
    if not button:
        raise ConfigError(f"{path}: button is empty")
    try:
        notify = parser.getboolean(SECTION, "notify", fallback=defaults.notify)
    except ValueError as exc:
        raise ConfigError(f"{path}: notify must be true or false") from exc
    send_command = (
        _command(path, values, "send_command") if values.get("send_command") else DEFAULT_SEND_COMMAND
    )
    key_buttons: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()
    repeat_buttons: Tuple[str, ...] = ()
    repeat_delay = _milliseconds(path, values, "repeat_delay_ms", DEFAULT_REPEAT_DELAY_MS)
    repeat_interval = _milliseconds(path, values, "repeat_interval_ms", DEFAULT_REPEAT_INTERVAL_MS)
    if repeat_interval < 10:
        raise ConfigError(f"{path}: repeat_interval_ms must be at least 10")
    if buttons is None:
        if dictation is not None and dictation.button == button:
            raise ConfigError(f"{path}: the dictation button must differ from button")
    else:
        if "button" in values:
            raise ConfigError(f"{path}: set the buttons in [{BUTTONS_SECTION}], not with button")
        if dictation is not None and dictation.button is not None:
            raise ConfigError(
                f"{path}: set the dictation button in [{BUTTONS_SECTION}], not in [{DICTATION_SECTION}]"
            )
        talk = [name for name, value in buttons.items() if value == "talk"]
        dictate = [name for name, value in buttons.items() if value == "dictate"]
        if dictate and dictation is None:
            raise ConfigError(f"{path}: {dictate[0]} = dictate needs a [{DICTATION_SECTION}] section")
        button = talk[0] if talk else None
        dictation = replace(dictation, button=dictate[0]) if dictate else None
        keys = []
        for name, value in buttons.items():
            if value == "send":
                keys.append((name, send_command))
            elif value.startswith(("key ", "repeat ")):
                key = value.split(" ", 1)[1]
                keys.append((name, ("xdotool", "key", "--clearmodifiers", key)))
        key_buttons = tuple(keys)
        repeat_buttons = tuple(name for name, value in buttons.items() if value.startswith("repeat "))
    return Config(
        mode=mode,
        button=button,
        source=values.get("source") or None,
        notify=notify,
        dictation=dictation,
        key_buttons=key_buttons,
        repeat_buttons=repeat_buttons,
        repeat_delay_ms=repeat_delay,
        repeat_interval_ms=repeat_interval,
        send_command=send_command,
    )


class ConfigWatcher:
    """Notices changes of the configuration file, so they apply without a restart."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or config_path()
        self.stamp = self._stamp()

    def _stamp(self) -> Optional[Tuple[int, int]]:
        try:
            stat = os.stat(self.path)
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def poll(self) -> Optional[Config]:
        """Return the new configuration if the file changed and is valid."""
        stamp = self._stamp()
        if stamp == self.stamp:
            return None
        self.stamp = stamp
        try:
            return load_config(self.path)
        except ConfigError as exc:
            log.error("The changed configuration is invalid and was not applied: %s", exc)
            return None
