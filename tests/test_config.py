import os
import tempfile
import unittest
from unittest import mock

from speechmike_ptt.config import Config, ConfigError, config_path, load_config


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "config.ini")

    def load(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)
        return load_config(self.path)

    def test_missing_file_gives_defaults(self):
        self.assertEqual(load_config(self.path), Config("hold", "REC", None))

    def test_empty_file_gives_defaults(self):
        self.assertEqual(self.load(""), Config())

    def test_reads_all_settings(self):
        config = self.load("[speechmike-ptt]\nmode = toggle\nbutton = F1\nsource = alsa_input.x\n")
        self.assertEqual(config, Config("toggle", "F1", "alsa_input.x"))

    def test_normalizes_case(self):
        config = self.load("[speechmike-ptt]\nmode = TOGGLE\nbutton = f1\n")
        self.assertEqual(config, Config("toggle", "F1"))

    def test_empty_source_means_detection(self):
        self.assertIsNone(self.load("[speechmike-ptt]\nsource =\n").source)

    def test_percent_in_value_is_literal(self):
        self.assertEqual(self.load("[speechmike-ptt]\nsource = a%b\n").source, "a%b")

    def test_notify_is_off_by_default(self):
        self.assertFalse(self.load("[speechmike-ptt]\nmode = hold\n").notify)

    def test_notify_on(self):
        self.assertTrue(self.load("[speechmike-ptt]\nnotify = true\n").notify)

    def test_invalid_notify(self):
        with self.assertRaisesRegex(ConfigError, "notify must be"):
            self.load("[speechmike-ptt]\nnotify = maybe\n")

    def test_invalid_mode(self):
        with self.assertRaisesRegex(ConfigError, "mode must be"):
            self.load("[speechmike-ptt]\nmode = always\n")

    def test_unknown_setting_catches_typos(self):
        with self.assertRaisesRegex(ConfigError, "unknown setting 'mod'"):
            self.load("[speechmike-ptt]\nmod = toggle\n")

    def test_unknown_section(self):
        with self.assertRaisesRegex(ConfigError, "unknown section"):
            self.load("[speechmike]\nmode = toggle\n")

    def test_empty_button(self):
        with self.assertRaisesRegex(ConfigError, "button is empty"):
            self.load("[speechmike-ptt]\nbutton =\n")

    def test_syntax_error(self):
        with self.assertRaisesRegex(ConfigError, "Cannot read"):
            self.load("mode = toggle\n")


class ConfigPathTest(unittest.TestCase):
    def test_uses_xdg_config_home(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/x"}):
            self.assertEqual(config_path(), "/x/speechmike-ptt/config.ini")

    def test_falls_back_to_home(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "", "HOME": "/h"}):
            self.assertEqual(config_path(), "/h/.config/speechmike-ptt/config.ini")


if __name__ == "__main__":
    unittest.main()
