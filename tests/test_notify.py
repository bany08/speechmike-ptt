import subprocess
import threading
import unittest
from unittest import mock

from speechmike_ptt.notify import Notifier


def result(stdout="43\n", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, "error text")


class NotifierTest(unittest.TestCase):
    def setUp(self):
        which = mock.patch("speechmike_ptt.notify.shutil.which", return_value="/bin/notify-send")
        self.which = which.start()
        self.addCleanup(which.stop)
        run = mock.patch("speechmike_ptt.notify.subprocess.run", return_value=result())
        self.run_mock = run.start()
        self.addCleanup(run.stop)
        self.notifier = Notifier()

    def commands(self):
        return [call.args[0] for call in self.run_mock.call_args_list]

    def test_second_notification_replaces_the_first(self):
        self.notifier._show(True)
        self.notifier._show(False)
        first, second = self.commands()
        self.assertEqual(first[-1], "Microphone open")
        self.assertFalse(any(arg.startswith("--replace-id") for arg in first))
        self.assertEqual(second[-1], "Microphone muted")
        self.assertIn("--replace-id=43", second)

    def test_same_state_is_not_shown_twice(self):
        self.notifier._show(True)
        self.notifier._show(True)
        self.assertEqual(len(self.commands()), 1)

    def test_missing_notify_send_is_ignored(self):
        self.which.return_value = None
        self.notifier._show(True)
        self.run_mock.assert_not_called()

    def test_failure_to_start_does_not_raise(self):
        self.run_mock.side_effect = OSError("boom")
        with self.assertLogs("speechmike_ptt.notify", "WARNING"):
            self.notifier._show(True)

    def test_timeout_does_not_raise(self):
        self.run_mock.side_effect = subprocess.TimeoutExpired("notify-send", 5)
        with self.assertLogs("speechmike_ptt.notify", "WARNING"):
            self.notifier._show(True)

    def test_failing_notify_send_disables_notifications(self):
        self.run_mock.return_value = result(returncode=1)
        with self.assertLogs("speechmike_ptt.notify", "WARNING"):
            self.notifier._show(True)
        self.notifier._show(False)
        self.assertEqual(len(self.commands()), 1)

    def test_call_does_not_wait_and_flush_shows_the_state(self):
        release = threading.Event()

        def slow(*_args, **_kwargs):
            release.wait(5)
            return result()

        self.run_mock.side_effect = slow
        self.notifier(True)  # returns although notify-send is still running
        release.set()
        self.notifier.flush(5)
        self.assertEqual(self.commands()[-1][-1], "Microphone open")

    def test_backlog_collapses_to_the_newest_state(self):
        started = threading.Event()
        release = threading.Event()

        def slow(*_args, **_kwargs):
            started.set()
            release.wait(5)
            return result()

        self.run_mock.side_effect = slow
        self.notifier(True)
        self.assertTrue(started.wait(5))
        for state in (False, True, False, True, False):
            self.notifier(state)
        release.set()
        self.notifier.flush(5)
        self.assertEqual([command[-1] for command in self.commands()],
                         ["Microphone open", "Microphone muted"])


if __name__ == "__main__":
    unittest.main()
