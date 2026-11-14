import os
import tempfile
import unittest
from unittest import mock

from speechmike_ptt.config import (
    Config,
    ConfigError,
    ConfigWatcher,
    DictationConfig,
    config_path,
    load_config,
)


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

    DICTATION = (
        "[dictation]\nbutton = f1\non_press = pkill -USR2 -x handy\n"
        "on_release = sh -c 'echo a b'\n"
    )

    def test_no_dictation_by_default(self):
        self.assertIsNone(self.load("[speechmike-ptt]\n").dictation)

    def test_reads_dictation(self):
        config = self.load(self.DICTATION + "release_delay_ms = 250\nrecorder = cpal\n")
        self.assertEqual(
            config.dictation,
            DictationConfig(
                "F1",
                ("pkill", "-USR2", "-x", "handy"),
                ("sh", "-c", "echo a b"),
                250,
                "cpal",
            ),
        )

    def test_reads_tool_settings(self):
        config = self.load(self.DICTATION + "tool_start = handy --start-hidden\ntool_process = handy\n")
        self.assertEqual(config.dictation.tool_start, ("handy", "--start-hidden"))
        self.assertEqual(config.dictation.tool_process, "handy")

    def test_dictation_defaults(self):
        dictation = self.load(self.DICTATION).dictation
        self.assertEqual(dictation.release_delay_ms, 400)
        self.assertIsNone(dictation.recorder)

    def test_dictation_needs_its_settings(self):
        for missing in ("button = F1\non_press = a\n", "on_press = a\non_release = b\n"):
            with self.assertRaisesRegex(ConfigError, "needs|missing"):
                self.load("[dictation]\n" + missing)

    def test_dictation_button_must_differ(self):
        with self.assertRaisesRegex(ConfigError, "must differ"):
            self.load("[dictation]\nbutton = REC\non_press = a\non_release = b\n")
        with self.assertRaisesRegex(ConfigError, "must differ"):
            self.load(
                "[speechmike-ptt]\nbutton = F1\n"
                "[dictation]\nbutton = F1\non_press = a\non_release = b\n"
            )

    def test_invalid_delay(self):
        for value in ("soon", "-1"):
            with self.assertRaisesRegex(ConfigError, "release_delay_ms"):
                self.load(self.DICTATION + f"release_delay_ms = {value}\n")

    def test_invalid_quoting(self):
        with self.assertRaisesRegex(ConfigError, "invalid on_press"):
            self.load("[dictation]\nbutton = F1\non_press = 'a\non_release = b\n")

    def test_unknown_dictation_setting(self):
        with self.assertRaisesRegex(ConfigError, "unknown setting"):
            self.load(self.DICTATION + "delay = 1\n")

    DICTATION_COMMANDS = "[dictation]\non_press = a\non_release = b\n"

    def test_buttons_swap_talk_and_dictate(self):
        config = self.load("[buttons]\nrec = dictate\nf1 = talk\nf3 = send\n" + self.DICTATION_COMMANDS)
        self.assertEqual(config.button, "F1")
        self.assertEqual(config.dictation.button, "REC")
        self.assertEqual(config.key_buttons, (("F3", ("xdotool", "key", "--clearmodifiers", "Return")),))

    def test_without_buttons_the_old_settings_apply(self):
        config = self.load(self.DICTATION)
        self.assertEqual((config.button, config.dictation.button, config.key_buttons), ("REC", "F1", ()))

    def test_no_talk_button(self):
        config = self.load("[buttons]\nf1 = dictate\nrec = none\n" + self.DICTATION_COMMANDS)
        self.assertIsNone(config.button)

    def test_buttons_without_dictate_disable_dictation(self):
        config = self.load("[buttons]\nrec = talk\n" + self.DICTATION_COMMANDS)
        self.assertIsNone(config.dictation)

    def test_unknown_function(self):
        with self.assertRaisesRegex(ConfigError, "must be one of"):
            self.load("[buttons]\nrec = shout\n")

    def test_only_one_talk_button(self):
        with self.assertRaisesRegex(ConfigError, "only one button can talk"):
            self.load("[buttons]\nrec = talk\nf1 = talk\n")

    def test_dictate_needs_commands(self):
        with self.assertRaisesRegex(ConfigError, "needs a \\[dictation\\] section"):
            self.load("[buttons]\nf1 = dictate\n")

    def test_old_and_new_button_settings_conflict(self):
        with self.assertRaisesRegex(ConfigError, "not with button"):
            self.load("[speechmike-ptt]\nbutton = REC\n[buttons]\nrec = talk\n")
        with self.assertRaisesRegex(ConfigError, "not in \\[dictation\\]"):
            self.load("[buttons]\nrec = dictate\n" + self.DICTATION)

    def test_key_function(self):
        config = self.load("[buttons]\nf1 = key BackSpace\nf2 = key ctrl+z\n")
        self.assertEqual(
            dict(config.key_buttons),
            {
                "F1": ("xdotool", "key", "--clearmodifiers", "BackSpace"),
                "F2": ("xdotool", "key", "--clearmodifiers", "ctrl+z"),
            },
        )

    def test_repeat_function(self):
        config = self.load("[buttons]\nf2 = repeat BackSpace\nf3 = send\n")
        self.assertEqual(config.repeat_buttons, ("F2",))
        self.assertEqual(dict(config.key_buttons)["F2"][-1], "BackSpace")
        self.assertEqual((config.repeat_delay_ms, config.repeat_interval_ms), (500, 40))

    def test_repeat_timing(self):
        config = self.load("[speechmike-ptt]\nrepeat_delay_ms = 300\nrepeat_interval_ms = 25\n")
        self.assertEqual((config.repeat_delay_ms, config.repeat_interval_ms), (300, 25))
        with self.assertRaisesRegex(ConfigError, "at least 10"):
            self.load("[speechmike-ptt]\nrepeat_interval_ms = 1\n")
        with self.assertRaisesRegex(ConfigError, "must be a number"):
            self.load("[speechmike-ptt]\nrepeat_delay_ms = soon\n")

    def test_invalid_key_name(self):
        for value in ("key", "key a;b", "key a b"):
            with self.assertRaises(ConfigError):
                self.load(f"[buttons]\nf1 = {value}\n")

    def test_send_uses_the_send_command(self):
        config = self.load("[speechmike-ptt]\nsend_command = xdotool key ctrl+Return\n[buttons]\nf3 = send\n")
        self.assertEqual(config.key_buttons, (("F3", ("xdotool", "key", "ctrl+Return")),))

    def test_send_command(self):
        config = self.load("[speechmike-ptt]\nsend_command = xdotool key ctrl+Return\n")
        self.assertEqual(config.send_command, ("xdotool", "key", "ctrl+Return"))
        self.assertEqual(self.load("").send_command[-1], "Return")

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


class ConfigWatcherTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "config.ini")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)
        os.utime(self.path, ns=(0, os.stat(self.path).st_mtime_ns + 10**9))

    def test_unchanged_file(self):
        self.write("[speechmike-ptt]\n")
        self.assertIsNone(ConfigWatcher(self.path).poll())

    def test_changed_file_is_loaded(self):
        self.write("[speechmike-ptt]\n")
        watcher = ConfigWatcher(self.path)
        self.write("[speechmike-ptt]\nmode = toggle\n")
        self.assertEqual(watcher.poll().mode, "toggle")
        self.assertIsNone(watcher.poll())

    def test_invalid_change_is_ignored(self):
        self.write("[speechmike-ptt]\n")
        watcher = ConfigWatcher(self.path)
        self.write("[speechmike-ptt]\nmode = shout\n")
        with self.assertLogs("speechmike_ptt.config", "ERROR"):
            self.assertIsNone(watcher.poll())

    def test_created_file_is_noticed(self):
        watcher = ConfigWatcher(self.path)
        self.write("[speechmike-ptt]\nmode = toggle\n")
        self.assertEqual(watcher.poll().mode, "toggle")


class ConfigPathTest(unittest.TestCase):
    def test_uses_xdg_config_home(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/x"}):
            self.assertEqual(config_path(), "/x/speechmike-ptt/config.ini")

    def test_falls_back_to_home(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "", "HOME": "/h"}):
            self.assertEqual(config_path(), "/h/.config/speechmike-ptt/config.ini")


if __name__ == "__main__":
    unittest.main()
