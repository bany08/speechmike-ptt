import json
import os
import tempfile
import unittest
from unittest import mock

from speechmike_ptt.status import StatusFile, read_status, status_path


class StatusFileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "sub", "state.json")

    def test_round_trip(self):
        status = StatusFile(self.path)
        status.update(device=True, mic="open")
        state = read_status(self.path)
        self.assertEqual((state["device"], state["mic"]), (True, "open"))

    def test_unchanged_state_is_not_rewritten(self):
        status = StatusFile(self.path)
        status.update()
        with mock.patch("speechmike_ptt.status.os.replace") as replace:
            status.update()
        replace.assert_not_called()

    def test_clear_removes_the_file(self):
        status = StatusFile(self.path)
        status.update()
        status.clear()
        self.assertIsNone(read_status(self.path))

    def test_missing_file(self):
        self.assertIsNone(read_status(self.path))

    def test_dead_daemon_counts_as_not_running(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"pid": 2**22 + 12345, "device": True}, f)
        self.assertIsNone(read_status(self.path))

    def test_broken_file(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        self.assertIsNone(read_status(self.path))

    def test_write_failure_does_not_raise(self):
        status = StatusFile("/proc/forbidden/state.json")
        with self.assertLogs("speechmike_ptt.status", "WARNING"):
            status.update()

    def test_without_runtime_dir_nothing_happens(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": ""}):
            self.assertIsNone(status_path())
            status = StatusFile()
            status.update()
            status.clear()
            self.assertIsNone(read_status())


if __name__ == "__main__":
    unittest.main()
