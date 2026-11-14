import os
import tempfile
import unittest

from speechmike_ptt.assign import PRESETS, assign, button_names, current_mapping, label_for
from speechmike_ptt.config import ConfigError, load_config

DICTATION = """[dictation]
# my dictation tool
on_press = pkill -USR2 -x handy
on_release = pkill -USR2 -x handy
"""


class AssignTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = os.path.join(tmp.name, "config.ini")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_changes_one_line_and_keeps_comments(self):
        self.write("[speechmike-ptt]\nnotify = true\n\n" + DICTATION + "\n[buttons]\n# keys\nrec = dictate\nf2 = key BackSpace\n")
        assign("f2", "repeat BackSpace", self.path)
        text = self.read()
        self.assertIn("# my dictation tool", text)
        self.assertIn("# keys", text)
        self.assertIn("f2 = repeat BackSpace", text)
        self.assertIn("rec = dictate", text)
        self.assertIn("notify = true", text)

    def test_adds_a_missing_button(self):
        self.write(DICTATION + "\n[buttons]\nrec = dictate\n")
        assign("f3", "send", self.path)
        self.assertEqual(current_mapping(self.path)["F3"], "send")

    def test_dictate_moves_from_the_old_button(self):
        self.write(DICTATION + "\n[buttons]\nrec = dictate\nf1 = talk\n")
        mapping = assign("f1", "dictate", self.path)
        self.assertEqual((mapping["F1"], mapping["REC"]), ("dictate", "none"))
        config = load_config(self.path)
        self.assertEqual((config.dictation.button, config.button), ("F1", None))

    def test_migrates_the_old_form(self):
        self.write("[speechmike-ptt]\nbutton = REC\nnotify = true\n\n[dictation]\nbutton = F1\non_press = a\non_release = b\n")
        assign("f2", "repeat BackSpace", self.path)
        text = self.read()
        self.assertNotIn("button =", text)
        config = load_config(self.path)
        self.assertEqual((config.button, config.dictation.button), ("REC", "F1"))
        self.assertIn("F2", config.repeat_buttons)

    def test_works_without_a_file(self):
        assign("f3", "send", self.path)
        config = load_config(self.path)
        self.assertEqual(config.button, "REC")
        self.assertEqual(dict(config.key_buttons)["F3"][-1], "Return")

    def test_invalid_result_leaves_the_file_unchanged(self):
        self.write("[buttons]\nrec = talk\n")
        before = self.read()
        with self.assertRaises(ConfigError):
            assign("f1", "dictate", self.path)  # no [dictation] section
        self.assertEqual(self.read(), before)
        self.assertEqual([n for n in os.listdir(os.path.dirname(self.path)) if n != "config.ini"], [])

    def test_unknown_button(self):
        with self.assertRaisesRegex(ConfigError, "Unknown button"):
            assign("f9", "send", self.path)

    def test_invalid_function(self):
        self.write("[buttons]\nrec = talk\n")
        with self.assertRaises(ConfigError):
            assign("f2", "shout", self.path)

    def test_every_preset_is_valid(self):
        self.write(DICTATION + "\n[buttons]\nrec = dictate\n")
        for _label, function in PRESETS:
            assign("f2", function, self.path)
            self.assertEqual(current_mapping(self.path)["F2"], function)

    def test_mapping_of_the_defaults(self):
        mapping = current_mapping(self.path)
        self.assertEqual(mapping["REC"], "talk")
        self.assertEqual(set(mapping), set(button_names()))

    def test_labels(self):
        self.assertEqual(label_for("repeat BackSpace"), "Backspace (repeats)")
        self.assertEqual(label_for("key ctrl+z"), "key ctrl+z")


if __name__ == "__main__":
    unittest.main()
