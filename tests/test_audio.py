import subprocess
import unittest
from unittest import mock

from speechmike_ptt.audio import (
    AudioError,
    Source,
    list_sources,
    mask_serial,
    parse_sources,
    select_source,
    set_source_mute,
)
from speechmike_ptt.devices import DEVICES

PROFILE = DEVICES[0]
MIC_A = "alsa_input.usb-Philips_Speech_Processing_SpeechMike_III_AAA111-00.mono-fallback"
MIC_B = "alsa_input.usb-Philips_Speech_Processing_SpeechMike_III_BBB222-00.mono-fallback"
MONITOR = "alsa_output.usb-Philips_Speech_Processing_SpeechMike_III_AAA111-00.mono-fallback.monitor"
OTHER = "alsa_input.usb-Other_Headset-00.mono-fallback"

PACTL_OUTPUT = (
    f"97\t{MONITOR}\tPipeWire\ts16le 1ch 22050Hz\tSUSPENDED\n"
    f"98\t{MIC_A}\tPipeWire\ts16le 1ch 22050Hz\tRUNNING\n"
    f"107\t{OTHER}\tPipeWire\ts16le 1ch 16000Hz\tSUSPENDED\n"
)


def sources(*names):
    return [Source(name, "SUSPENDED") for name in names]


class ParseTest(unittest.TestCase):
    def test_parses_name_and_state(self):
        parsed = parse_sources(PACTL_OUTPUT)
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[1], Source(MIC_A, "RUNNING"))

    def test_empty_output_and_junk_lines(self):
        self.assertEqual(parse_sources(""), [])
        self.assertEqual(parse_sources("junk\n"), [])


class SelectSourceTest(unittest.TestCase):
    def test_excludes_monitor_and_other_devices(self):
        found = select_source(sources(MONITOR, OTHER, MIC_A), PROFILE)
        self.assertEqual(found.name, MIC_A)

    def test_no_source(self):
        self.assertIsNone(select_source(sources(MONITOR, OTHER), PROFILE))

    def test_two_devices_are_told_apart_by_serial(self):
        found = select_source(sources(MIC_A, MIC_B), PROFILE, serial="BBB222")
        self.assertEqual(found.name, MIC_B)

    def test_two_devices_without_serial_are_ambiguous(self):
        self.assertIsNone(select_source(sources(MIC_A, MIC_B), PROFILE))

    def test_two_devices_with_unknown_serial_are_ambiguous(self):
        self.assertIsNone(select_source(sources(MIC_A, MIC_B), PROFILE, serial="CCC333"))

    def test_single_source_does_not_need_a_matching_serial(self):
        found = select_source(sources(MIC_A), PROFILE, serial="CCC333")
        self.assertEqual(found.name, MIC_A)


class MaskSerialTest(unittest.TestCase):
    EXPECTED = "alsa_input.usb-Philips_Speech_Processing_SpeechMike_III_<SERIAL>-00.mono-fallback"

    def test_with_known_serial(self):
        self.assertEqual(mask_serial(MIC_A, "AAA111"), self.EXPECTED)

    def test_without_known_serial(self):
        self.assertEqual(mask_serial(MIC_A), self.EXPECTED)

    def test_serial_that_does_not_appear_in_the_name(self):
        self.assertEqual(mask_serial(MIC_A, "CCC333"), self.EXPECTED)


class ListSourcesTest(unittest.TestCase):
    @mock.patch("speechmike_ptt.audio.subprocess.run")
    def test_pins_the_locale(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, PACTL_OUTPUT, "")
        self.assertEqual(len(list_sources()), 3)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")

    @mock.patch("speechmike_ptt.audio.subprocess.run")
    def test_set_mute_arguments(self, run):
        run.return_value = subprocess.CompletedProcess([], 0, "", "")
        set_source_mute(MIC_A, True)
        self.assertEqual(run.call_args.args[0], ["pactl", "set-source-mute", MIC_A, "1"])
        set_source_mute(MIC_A, False)
        self.assertEqual(run.call_args.args[0], ["pactl", "set-source-mute", MIC_A, "0"])

    @mock.patch("speechmike_ptt.audio.subprocess.run")
    def test_set_mute_failure(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "No such entity")
        with self.assertRaises(AudioError):
            set_source_mute(MIC_A, True)

    @mock.patch("speechmike_ptt.audio.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_pactl(self, _run):
        with self.assertRaises(AudioError):
            list_sources()

    @mock.patch("speechmike_ptt.audio.subprocess.run")
    def test_pactl_failure(self, run):
        run.return_value = subprocess.CompletedProcess([], 1, "", "Connection refused")
        with self.assertRaisesRegex(AudioError, "Connection refused"):
            list_sources()

    @mock.patch(
        "speechmike_ptt.audio.subprocess.run",
        side_effect=subprocess.TimeoutExpired("pactl", 5),
    )
    def test_pactl_timeout(self, _run):
        with self.assertRaises(AudioError):
            list_sources()


if __name__ == "__main__":
    unittest.main()
