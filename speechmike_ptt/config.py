"""Load the configuration file.

Invalid settings are an error and never fall back silently: a typo must not
leave the user in a mode they did not choose.
"""

from __future__ import annotations

import configparser
import os
import shlex
from dataclasses import dataclass
from typing import Optional, Tuple

SECTION = "speechmike-ptt"
DICTATION_SECTION = "dictation"
MODES = ("hold", "toggle")
DEFAULT_RELEASE_DELAY_MS = 400


class ConfigError(Exception):
    """The configuration file is invalid."""


@dataclass(frozen=True)
class DictationConfig:
    button: str
    on_press: Tuple[str, ...]  # command, already split into arguments
    on_release: Tuple[str, ...]
    release_delay_ms: int = DEFAULT_RELEASE_DELAY_MS
    recorder: Optional[str] = None  # text in the application name of the tool's audio stream
    tool_start: Optional[Tuple[str, ...]] = None  # lets the status icon start the tool
    tool_process: Optional[str] = None  # process name the status icon looks for


@dataclass(frozen=True)
class Config:
    mode: str = "hold"
    button: str = "REC"
    source: Optional[str] = None  # overrides the automatic source detection
    notify: bool = False  # desktop notification on every switch
    dictation: Optional[DictationConfig] = None


def config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "speechmike-ptt", "config.ini")


def _command(path: str, values: dict, key: str) -> Tuple[str, ...]:
    text = values.get(key)
    if not text:
        raise ConfigError(f"{path}: [{DICTATION_SECTION}] needs {key}")
    try:
        command = tuple(shlex.split(text))
    except ValueError as exc:
        raise ConfigError(f"{path}: invalid {key}: {exc}") from exc
    if not command:
        raise ConfigError(f"{path}: [{DICTATION_SECTION}] needs {key}")
    return command


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
    button = values.get("button", "").upper()
    if not button:
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

    unknown_sections = set(parser.sections()) - {SECTION, DICTATION_SECTION}
    if unknown_sections:
        raise ConfigError(f"{path}: unknown section {sorted(unknown_sections)[0]!r}")
    dictation = _load_dictation(path, parser)
    if not parser.has_section(SECTION):
        if dictation is not None and dictation.button == Config().button:
            raise ConfigError(f"{path}: the dictation button must differ from button")
        return Config(dictation=dictation)

    values = dict(parser[SECTION])
    unknown_keys = set(values) - {"mode", "button", "source", "notify"}
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
    if dictation is not None and dictation.button == button:
        raise ConfigError(f"{path}: the dictation button must differ from button")
    return Config(
        mode=mode,
        button=button,
        source=values.get("source") or None,
        notify=notify,
        dictation=dictation,
    )
