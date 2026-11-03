"""Optional desktop notifications through notify-send."""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from typing import Optional

log = logging.getLogger(__name__)

EXPIRE_MILLISECONDS = 1500
NOTIFY_SEND_TIMEOUT_SECONDS = 5


class Notifier:
    """Shows the microphone state in a single notification that gets replaced.

    Calls never block, so a notification cannot delay the switching of the
    microphone. A background thread shows only the newest state, which keeps a
    backlog from building up when the button is pressed quickly.
    """

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._pending: Optional[bool] = None
        self._busy = False
        self._thread: Optional[threading.Thread] = None
        self._shown: Optional[bool] = None
        self._notification_id: Optional[str] = None
        self._disabled = False

    def __call__(self, is_open: bool) -> None:
        with self._changed:
            self._pending = is_open
            if self._thread is None:
                self._thread = threading.Thread(target=self._work, daemon=True)
                self._thread.start()
            self._changed.notify_all()

    def flush(self, timeout: float) -> None:
        """Wait until the newest state is shown, so that it is not lost on exit."""
        with self._changed:
            self._changed.wait_for(lambda: self._pending is None and not self._busy, timeout)

    def _work(self) -> None:
        while True:
            with self._changed:
                self._changed.wait_for(lambda: self._pending is not None)
                state = self._pending
                self._pending = None
                self._busy = True
            self._show(state)
            with self._changed:
                self._busy = False
                self._changed.notify_all()

    def _show(self, is_open: bool) -> None:
        if self._disabled or is_open == self._shown:
            return
        path = shutil.which("notify-send")
        if path is None:
            return
        command = [
            path,
            "--app-name=speechmike-ptt",
            f"--expire-time={EXPIRE_MILLISECONDS}",
            "--hint=boolean:transient:true",
            "--print-id",
        ]
        if self._notification_id:
            command.append(f"--replace-id={self._notification_id}")
        command.append("Microphone open" if is_open else "Microphone muted")
        try:
            result = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=NOTIFY_SEND_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("Notification failed: %s", exc)
            return
        if result.returncode != 0:
            log.warning("Notifications disabled, notify-send failed: %s", result.stderr.strip())
            self._disabled = True
            return
        self._shown = is_open
        self._notification_id = result.stdout.strip() or None
