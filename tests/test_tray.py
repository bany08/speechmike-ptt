import os
import unittest
from unittest import mock

from speechmike_ptt.config import DictationConfig
from speechmike_ptt.tray import (
    ICON_DIR,
    describe,
    dictation_failed,
    handle_failed_dictation,
    launch,
    tool_running,
)


class DescribeTest(unittest.TestCase):
    def test_states(self):
        cases = {
            None: "speechmike-ptt-off",
            (False, "muted", False): "speechmike-ptt-off",
            (True, "muted", False): "speechmike-ptt-muted",
            (True, "open", False): "speechmike-ptt-open",
            (True, "open", True): "speechmike-ptt-dictating",
        }
        for key, icon in cases.items():
            state = key and {"device": key[0], "mic": key[1], "dictating": key[2]}
            self.assertEqual(describe(state)[0], icon)

    def test_every_icon_exists(self):
        for name in ("off", "muted", "open", "dictating"):
            path = os.path.join(ICON_DIR, f"speechmike-ptt-{name}.svg")
            self.assertTrue(os.path.exists(path), path)


class FailureTest(unittest.TestCase):
    TOOL = DictationConfig("F1", ("a",), ("b",), tool_start=("handy",), tool_process="handy")

    def test_detects_a_new_failure(self):
        self.assertEqual(dictation_failed(1, {"failed_starts": 2}), (2, True))

    def test_no_new_failure(self):
        self.assertEqual(dictation_failed(2, {"failed_starts": 2}), (2, False))

    def test_daemon_restart_resets_the_count(self):
        self.assertEqual(dictation_failed(5, {"failed_starts": 0}), (0, False))

    def test_no_daemon(self):
        self.assertEqual(dictation_failed(5, None), (0, False))

    @mock.patch("speechmike_ptt.tray.notify")
    @mock.patch("speechmike_ptt.tray.launch")
    @mock.patch("speechmike_ptt.tray.tool_running", return_value=False)
    def test_starts_a_missing_tool(self, _running, launch_mock, notify):
        handle_failed_dictation(self.TOOL)
        launch_mock.assert_called_once_with(("handy",))
        self.assertIn("not running", notify.call_args.args[0])

    @mock.patch("speechmike_ptt.tray.notify")
    @mock.patch("speechmike_ptt.tray.launch")
    @mock.patch("speechmike_ptt.tray.tool_running", return_value=True)
    def test_running_tool_is_not_started_twice(self, _running, launch_mock, notify):
        handle_failed_dictation(self.TOOL)
        launch_mock.assert_not_called()
        self.assertIn("failed", notify.call_args.args[0])

    @mock.patch("speechmike_ptt.tray.notify")
    @mock.patch("speechmike_ptt.tray.launch")
    def test_without_tool_settings_it_only_notifies(self, launch_mock, notify):
        handle_failed_dictation(None)
        launch_mock.assert_not_called()
        notify.assert_called_once()


class ToolTest(unittest.TestCase):
    @mock.patch("speechmike_ptt.tray.subprocess.run")
    def test_running(self, run):
        run.return_value.returncode = 0
        self.assertTrue(tool_running("handy"))
        self.assertEqual(run.call_args.args[0], ["pgrep", "-x", "handy"])

    @mock.patch("speechmike_ptt.tray.subprocess.run", side_effect=OSError)
    def test_missing_pgrep(self, _run):
        self.assertFalse(tool_running("handy"))

    @mock.patch("speechmike_ptt.tray.subprocess.Popen", side_effect=OSError("boom"))
    def test_failed_launch_does_not_raise(self, _popen):
        with self.assertLogs("speechmike_ptt.tray", "ERROR"):
            launch(["handy"])


if __name__ == "__main__":
    unittest.main()
