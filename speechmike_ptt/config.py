"""Load the configuration file.

Invalid settings are an error and never fall back silently: a typo must not
leave the user in a mode they did not choose.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from typing import Optional

SECTION = "speechmike-ptt"
MODES = ("hold", "toggle")


class ConfigError(Exception):
    """The configuration file is invalid."""


@dataclass(frozen=True)
class Config:
    mode: str = "hold"
    button: str = "REC"
    source: Optional[str] = None  # overrides the automatic source detection
    notify: bool = False  # desktop notification on every switch


def config_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "speechmike-ptt", "config.ini")


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

    unknown_sections = set(parser.sections()) - {SECTION}
    if unknown_sections:
        raise ConfigError(f"{path}: unknown section {sorted(unknown_sections)[0]!r}")
    if not parser.has_section(SECTION):
        return Config()

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
    return Config(mode=mode, button=button, source=values.get("source") or None, notify=notify)
