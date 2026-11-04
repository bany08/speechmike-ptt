"""A small state file that lets other programs show what the daemon is doing."""

from __future__ import annotations

import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)


def status_path() -> Optional[str]:
    base = os.environ.get("XDG_RUNTIME_DIR")
    return os.path.join(base, "speechmike-ptt", "state.json") if base else None


class StatusFile:
    """Publishes the daemon state. Never raises: it must not affect the microphone."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or status_path()
        self.state = {
            "pid": os.getpid(),
            "device": False,
            "mic": "muted",
            "dictating": False,
            "failed_starts": 0,
        }
        self.written: Optional[dict] = None
        self.failed = False

    def update(self, **changes: object) -> None:
        self.state.update(changes)
        if self.path is None or self.state == self.written:
            return
        temporary = self.path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(self.state, f)
            os.replace(temporary, self.path)
            self.written = dict(self.state)
        except OSError as exc:
            if not self.failed:
                log.warning("Could not write the status file: %s", exc)
            self.failed = True

    def clear(self) -> None:
        if self.path is None:
            return
        try:
            os.remove(self.path)
        except OSError:
            pass


def read_status(path: Optional[str] = None) -> Optional[dict]:
    """Return the published state, or None if no daemon is running."""
    path = path or status_path()
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        alive = os.path.exists(f"/proc/{int(state['pid'])}")
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return state if alive else None
