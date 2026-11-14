"""Change the button mapping in the configuration file.

Only the affected lines change, so comments and the rest of the file stay as
they are. The new file is validated before it replaces the old one.
"""

from __future__ import annotations

import os
import re
import tempfile
from typing import Dict, List, Optional, Sequence, Tuple

from .config import BUTTONS_SECTION, DICTATION_SECTION, SECTION, ConfigError, config_path, load_config
from .devices import DEVICES

# Functions offered in the status icon menu: (label, value in [buttons])
PRESETS: Tuple[Tuple[str, str], ...] = (
    ("Dictate", "dictate"),
    ("Talk (microphone only)", "talk"),
    ("Enter", "send"),
    ("Backspace (repeats)", "repeat BackSpace"),
    ("Delete (repeats)", "repeat Delete"),
    ("Cursor left (repeats)", "repeat Left"),
    ("Cursor right (repeats)", "repeat Right"),
    ("End, twice: Home", "double End Home"),
    ("Escape", "key Escape"),
    ("Shift+Tab", "key shift+Tab"),
    ("Nothing", "none"),
)
UNIQUE = ("talk", "dictate")

_SECTION_LINE = re.compile(r"^\s*\[([^\]]+)\]\s*$")
_KEY_LINE = re.compile(r"^\s*([A-Za-z0-9_]+)\s*[=:]")


def button_names() -> List[str]:
    """All button names from the device table, in their order."""
    names: List[str] = []
    for profile in DEVICES:
        for name in profile.buttons:
            if name not in names:
                names.append(name)
    return names


def _read(path: str) -> List[str]:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().splitlines()
    except FileNotFoundError:
        return []


def _section_of(lines: Sequence[str]) -> List[Optional[str]]:
    """The section name for every line."""
    section = None
    result = []
    for line in lines:
        match = _SECTION_LINE.match(line)
        if match:
            section = match.group(1).strip()
        result.append(section)
    return result


def current_mapping(path: Optional[str] = None) -> Dict[str, str]:
    """Return button -> function as the daemon applies it, including the old form."""
    config = load_config(path or config_path())
    mapping = {name: "none" for name in button_names()}
    if config.button:
        mapping[config.button] = "talk"
    if config.dictation is not None and config.dictation.button:
        mapping[config.dictation.button] = "dictate"
    lines = _read(path or config_path())
    for line, section in zip(lines, _section_of(lines)):
        if section == BUTTONS_SECTION:
            match = re.match(r"^\s*([A-Za-z0-9_]+)\s*[=:]\s*(.*?)\s*$", line)
            if match and not line.lstrip().startswith(("#", ";")):
                mapping[match.group(1).upper()] = match.group(2)
    return mapping


def assign(button: str, function: str, path: Optional[str] = None) -> Dict[str, str]:
    """Set one button and write the file. Returns the new mapping.

    Raises ConfigError if the button is unknown or the result would be invalid.
    The file is left unchanged then.
    """
    path = path or config_path()
    button = button.upper()
    if button not in button_names():
        raise ConfigError(f"Unknown button {button}. Known: {', '.join(button_names())}")
    function = " ".join(function.split())
    mapping = current_mapping(path)
    if function.lower() in UNIQUE:
        function = function.lower()
        for name, value in mapping.items():
            if value == function and name != button:
                mapping[name] = "none"
    mapping[button] = function

    lines = _read(path)
    sections = _section_of(lines)
    output: List[str] = []
    written = set()
    in_buttons_end = None
    for index, (line, section) in enumerate(zip(lines, sections)):
        key = _KEY_LINE.match(line)
        # The old form sets the buttons elsewhere. [buttons] replaces it.
        if key and key.group(1).lower() == "button" and section in (SECTION, DICTATION_SECTION):
            continue
        if section == BUTTONS_SECTION and key and not line.lstrip().startswith(("#", ";")):
            name = key.group(1).upper()
            if name in written:
                continue
            output.append(f"{name.lower()} = {mapping.get(name, 'none')}")
            written.add(name)
        else:
            output.append(line)
        if section == BUTTONS_SECTION:
            in_buttons_end = len(output)

    missing = [f"{name.lower()} = {value}" for name, value in mapping.items()
               if name not in written and value != "none"]
    if in_buttons_end is None:
        if output and output[-1].strip():
            output.append("")
        output.append(f"[{BUTTONS_SECTION}]")
        output.extend(missing)
    else:
        # Insert after the last non-empty line of the section.
        position = in_buttons_end
        while position > 0 and not output[position - 1].strip():
            position -= 1
        output[position:position] = missing

    text = "\n".join(output).rstrip("\n") + "\n"
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".config-", suffix=".ini", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        load_config(temporary)  # raises ConfigError, and nothing is replaced
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
    return current_mapping(path)


def label_for(function: str) -> str:
    for label, value in PRESETS:
        if value == function:
            return label
    return function
