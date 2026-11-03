import logging
import signal
import unittest
from unittest import mock

from speechmike_ptt import daemon
from speechmike_ptt.audio import AudioError, Source
from speechmike_ptt.config import Config
from speechmike_ptt.daemon import PushToTalk
from speechmike_ptt.devices import DEVICES
from speechmike_ptt.hid import DeviceGone, HidInterface

PROFILE = DEVICES[0]
IDLE = bytes.fromhex("80000000842a000000")
REC = bytes.fromhex("80000000842a000001")
PLAY = bytes.fromhex("80000000842a000004")
REC_AND_PLAY = bytes.fromhex("80200020c135000005")
SOURCE = "alsa_input.usb-Philips_Speech_Processing_SpeechMike_III_AAA111-00.mono-fallback"


class FakeMute:
    """Records mute calls and fails on demand."""

    def __init__(self):
        self.calls = []
        self.fail_open = False
        self.fail_mute = 0  # number of mute calls that fail

    def __call__(self, muted):
        self.calls.append(muted)
        if not muted and self.fail_open:
            raise AudioError("open failed")
        if muted and self.fail_mute > 0:
            self.fail_mute -= 1
            raise AudioError("mute failed")


class QuietTestCase(unittest.TestCase):
    def setUp(self):
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        patcher = mock.patch("speechmike_ptt.daemon.time.sleep")
        patcher.start()
        self.addCleanup(patcher.stop)


class PushToTalkTest(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMute()
        self.ptt = PushToTalk(PROFILE, "REC", self.fake)

    def test_opens_while_held_and_mutes_on_release(self):
        self.ptt.handle_report(REC)
        self.assertTrue(self.ptt.is_open)
        self.ptt.handle_report(IDLE)
        self.assertFalse(self.ptt.is_open)
        self.assertEqual(self.fake.calls, [False, True])

    def test_duplicate_reports_change_nothing(self):
        for report in (REC, REC, IDLE, IDLE):
            self.ptt.handle_report(report)
        self.assertEqual(self.fake.calls, [False, True])

    def test_other_buttons_do_not_open(self):
        self.ptt.handle_report(PLAY)
        self.assertEqual(self.fake.calls, [])

    def test_stays_open_when_another_button_is_added_and_released(self):
        for report in (REC, REC_AND_PLAY, REC):
            self.ptt.handle_report(report)
        self.assertEqual(self.fake.calls, [False])

    def test_mutes_when_rec_is_released_but_another_button_stays_held(self):
        for report in (REC_AND_PLAY, PLAY):
            self.ptt.handle_report(report)
        self.assertEqual(self.fake.calls, [False, True])

    def test_malformed_report_mutes(self):
        self.ptt.handle_report(REC)
        self.ptt.handle_report(b"\x80")
        self.assertFalse(self.ptt.is_open)

    def test_failed_open_stays_muted(self):
        self.fake.fail_open = True
        self.ptt.handle_report(REC)
        self.assertFalse(self.ptt.is_open)
        self.assertEqual(self.fake.calls, [False, True])

    def test_failed_mute_is_retried(self):
        self.ptt.handle_report(REC)
        self.fake.fail_mute = 2
        self.ptt.handle_report(IDLE)
        self.assertFalse(self.ptt.is_open)
        self.assertEqual(self.fake.calls, [False, True, True, True])

    def test_persistent_mute_failure_is_fatal(self):
        self.ptt.handle_report(REC)
        self.fake.fail_mute = 99
        with self.assertRaises(AudioError):
            self.ptt.handle_report(IDLE)


class NotificationTest(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMute()
        self.changes = []
        self.ptt = PushToTalk(PROFILE, "REC", self.fake, on_change=self.changes.append)

    def test_reports_each_switch_once(self):
        for report in (REC, REC, IDLE, IDLE):
            self.ptt.handle_report(report)
        self.assertEqual(self.changes, [True, False])

    def test_failed_open_does_not_claim_open(self):
        self.fake.fail_open = True
        self.ptt.handle_report(REC)
        self.assertEqual(self.changes, [])


class EnforceMuteTest(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMute()
        self.muted = True
        self.ptt = PushToTalk(PROFILE, "REC", self.fake, is_muted=self.read_state)

    def read_state(self):
        if isinstance(self.muted, Exception):
            raise self.muted
        return self.muted

    def test_muted_source_is_left_alone(self):
        self.ptt.enforce_mute()
        self.assertEqual(self.fake.calls, [])

    def test_mutes_a_source_that_was_opened_from_outside(self):
        self.muted = False
        self.ptt.enforce_mute()
        self.assertEqual(self.fake.calls, [True])

    def test_open_microphone_is_not_touched(self):
        self.ptt.handle_report(REC)
        self.muted = False
        self.ptt.enforce_mute()
        self.assertEqual(self.fake.calls, [False])

    def test_toggle_open_microphone_is_not_touched(self):
        ptt = PushToTalk(PROFILE, "REC", self.fake, mode="toggle", is_muted=self.read_state)
        for report in (REC, IDLE):
            ptt.handle_report(report)
        self.muted = False
        ptt.enforce_mute()
        self.assertEqual(self.fake.calls, [False])

    def test_failed_check_is_survived_and_retried(self):
        self.muted = AudioError("no server")
        self.ptt.enforce_mute()
        self.assertEqual(self.fake.calls, [])
        self.muted = False
        self.ptt.enforce_mute()
        self.assertEqual(self.fake.calls, [True])

    def test_failed_check_warns_once(self):
        logging.disable(logging.NOTSET)
        self.muted = AudioError("no server")
        with self.assertLogs("speechmike_ptt.daemon", "WARNING") as logs:
            self.ptt.enforce_mute()
            self.ptt.enforce_mute()
        self.assertEqual(len(logs.output), 1)


class ToggleTest(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeMute()
        self.ptt = PushToTalk(PROFILE, "REC", self.fake, mode="toggle")

    def test_press_opens_and_stays_open_after_release(self):
        for report in (REC, REC, IDLE, IDLE):
            self.ptt.handle_report(report)
        self.assertTrue(self.ptt.is_open)
        self.assertEqual(self.fake.calls, [False])

    def test_second_press_mutes(self):
        for report in (REC, IDLE, REC, IDLE):
            self.ptt.handle_report(report)
        self.assertFalse(self.ptt.is_open)
        self.assertEqual(self.fake.calls, [False, True])

    def test_other_button_while_held_is_not_a_second_press(self):
        for report in (REC, REC_AND_PLAY, REC):
            self.ptt.handle_report(report)
        self.assertTrue(self.ptt.is_open)

    def test_other_button_alone_does_nothing(self):
        for report in (PLAY, IDLE):
            self.ptt.handle_report(report)
        self.assertEqual(self.fake.calls, [])

    def test_failed_open_stays_muted_and_next_press_retries(self):
        self.fake.fail_open = True
        self.ptt.handle_report(REC)
        self.ptt.handle_report(IDLE)
        self.assertFalse(self.ptt.is_open)
        self.fake.fail_open = False
        self.ptt.handle_report(REC)
        self.assertTrue(self.ptt.is_open)


def reports(*items):
    """Yield reports, raising any item that is an exception."""
    for item in items:
        if isinstance(item, BaseException) or (
            isinstance(item, type) and issubclass(item, BaseException)
        ):
            raise item
        yield item


class RunTest(QuietTestCase):
    def setUp(self):
        super().setUp()
        self.mute_calls = []
        self.fail_mute = False
        self.interface = HidInterface("/dev/hidraw9", PROFILE, 4, "AAA111", True)

        def set_source_mute(name, muted):
            self.assertEqual(name, SOURCE)
            self.mute_calls.append(muted)
            if muted and self.fail_mute:
                raise AudioError("mute failed")

        self.patch("find_hid_interfaces", return_value=[self.interface])
        self.patch("list_sources", return_value=[Source(SOURCE, "SUSPENDED")])
        self.patch("set_source_mute", side_effect=set_source_mute)
        self.get_mute = self.patch("get_source_mute", return_value=True)
        self.read = self.patch("read_reports")
        # By default the first wait ends the run, so run() cannot loop forever.
        self.wait = self.patch("wait_for_device", side_effect=KeyboardInterrupt)

        # run() ignores signals in its cleanup. Restore them after each test.
        for number in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            self.addCleanup(signal.signal, number, signal.getsignal(number))

    def patch(self, name, **kwargs):
        patcher = mock.patch(f"speechmike_ptt.daemon.{name}", **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def run_with(self, *items):
        self.read.return_value = reports(*items)
        return daemon.run()

    def test_mutes_before_reading(self):
        def check(_node, _timeout=None):
            self.assertEqual(self.mute_calls, [True])
            return reports(KeyboardInterrupt)

        self.read.side_effect = check
        self.assertEqual(daemon.run(), 0)

    def test_interrupt_while_open_mutes(self):
        self.assertEqual(self.run_with(REC, KeyboardInterrupt), 0)
        self.assertEqual(self.mute_calls, [True, False, True])

    def test_unplug_while_open_mutes(self):
        self.assertEqual(self.run_with(REC, DeviceGone("x")), 0)
        self.assertEqual(self.mute_calls, [True, False, True])

    def test_reconnect_mutes_before_reading_again(self):
        self.wait.side_effect = [None, KeyboardInterrupt]
        calls_at_reconnect = []

        def read(_node, _timeout=None):
            if self.read.call_count == 1:
                return reports(REC, DeviceGone("x"))
            calls_at_reconnect.extend(self.mute_calls)
            return reports(KeyboardInterrupt)

        self.read.side_effect = read
        self.assertEqual(daemon.run(), 0)
        self.assertEqual(self.read.call_count, 2)
        self.assertEqual(calls_at_reconnect, [True, False, True, True])

    def test_held_button_does_not_survive_a_reconnect(self):
        self.wait.side_effect = [None, KeyboardInterrupt]
        self.read.side_effect = [reports(REC, DeviceGone("x")), reports(IDLE, KeyboardInterrupt)]
        self.assertEqual(daemon.run(), 0)
        self.assertEqual(self.mute_calls.count(False), 1)

    def test_source_gone_with_the_device_is_not_an_error(self):
        def read(_node, _timeout=None):
            self.fail_mute = True
            daemon.list_sources.return_value = []
            return reports(DeviceGone("x"))

        self.read.side_effect = read
        self.assertEqual(daemon.run(), 0)

    def test_mute_failure_with_source_present_is_fatal(self):
        def read(_node, _timeout=None):
            self.fail_mute = True
            return reports(DeviceGone("x"))

        self.read.side_effect = read
        self.assertEqual(daemon.run(), 1)

    def test_signals_work_again_after_a_session(self):
        before = signal.getsignal(signal.SIGTERM)
        self.run_with(DeviceGone("x"))
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_unexpected_exception_still_mutes(self):
        with self.assertRaises(RuntimeError):
            self.run_with(REC, RuntimeError("bug"))
        self.assertEqual(self.mute_calls[-1], True)

    def test_permission_error_mutes_and_retries(self):
        self.wait.side_effect = [None, KeyboardInterrupt]
        self.read.side_effect = [reports(PermissionError()), reports(KeyboardInterrupt)]
        self.assertEqual(daemon.run(), 0)
        self.assertEqual(self.read.call_count, 2)
        self.assertEqual(self.mute_calls[-1], True)

    def test_does_not_read_if_the_first_mute_fails(self):
        self.fail_mute = True
        self.assertEqual(daemon.run(), 1)
        self.read.assert_not_called()
        self.assertNotIn(False, self.mute_calls)

    def test_failed_final_mute_is_an_error(self):
        def fail_after_open(_node, _timeout=None):
            self.fail_mute = True
            return reports(KeyboardInterrupt)

        self.read.side_effect = fail_after_open
        self.assertEqual(daemon.run(), 1)

    def test_toggle_open_at_exit_mutes(self):
        self.read.return_value = reports(REC, IDLE, KeyboardInterrupt)
        self.assertEqual(daemon.run(Config(mode="toggle")), 0)
        self.assertEqual(self.mute_calls, [True, False, True])

    def test_configured_button(self):
        self.read.return_value = reports(REC, PLAY, KeyboardInterrupt)
        self.assertEqual(daemon.run(Config(button="PLAY")), 0)
        self.assertEqual(self.mute_calls, [True, False, True])

    def test_unknown_button(self):
        self.assertEqual(daemon.run(Config(button="NOPE")), 1)
        self.read.assert_not_called()
        self.assertEqual(self.mute_calls, [])

    def test_configured_source_is_used(self):
        other = "alsa_input.other"
        daemon.list_sources.return_value = [Source(SOURCE, "IDLE"), Source(other, "IDLE")]
        daemon.set_source_mute.side_effect = lambda name, muted: self.mute_calls.append(name)
        self.read.return_value = reports(KeyboardInterrupt)
        self.assertEqual(daemon.run(Config(source=other)), 0)
        self.assertEqual(set(self.mute_calls), {other})

    def test_waits_for_the_configured_source(self):
        self.assertEqual(daemon.run(Config(source="alsa_input.missing")), 0)
        self.wait.assert_called_once()
        self.read.assert_not_called()

    def test_notifies_only_when_enabled(self):
        notify = self.patch("Notifier").return_value
        self.read.return_value = reports(REC, IDLE, KeyboardInterrupt)
        daemon.run(Config())
        notify.assert_not_called()
        self.read.return_value = reports(REC, IDLE, KeyboardInterrupt)
        daemon.run(Config(notify=True))
        self.assertEqual([call.args[0] for call in notify.call_args_list], [True, False])

    def test_notifies_when_the_session_ends_with_an_open_microphone(self):
        notify = self.patch("Notifier").return_value
        self.read.return_value = reports(REC, DeviceGone("x"))
        daemon.run(Config(notify=True))
        self.assertEqual([call.args[0] for call in notify.call_args_list], [True, False])

    def test_waits_for_notifications_before_exiting(self):
        notify = self.patch("Notifier").return_value
        self.read.return_value = reports(KeyboardInterrupt)
        daemon.run(Config(notify=True))
        notify.flush.assert_called_once()

    def test_idle_timeout_checks_the_mute_state(self):
        self.get_mute.return_value = False
        self.assertEqual(self.run_with(None, KeyboardInterrupt), 0)
        self.get_mute.assert_called_once_with(SOURCE)
        self.assertEqual(self.mute_calls, [True, True, True])

    def test_reads_with_a_timeout(self):
        self.run_with(KeyboardInterrupt)
        self.read.assert_called_once_with("/dev/hidraw9", daemon.MUTE_CHECK_SECONDS)

    def test_waits_for_the_device(self):
        daemon.find_hid_interfaces.return_value = []
        self.assertEqual(daemon.run(), 0)
        self.wait.assert_called_once()
        self.assertEqual(self.mute_calls, [])

    def test_device_appearing_later_is_picked_up(self):
        daemon.find_hid_interfaces.side_effect = [[], [], [self.interface]]
        self.wait.side_effect = [None, None, KeyboardInterrupt]
        self.read.return_value = reports(KeyboardInterrupt)
        self.assertEqual(daemon.run(), 0)
        self.read.assert_called_once()

    def test_waits_for_the_source(self):
        daemon.list_sources.return_value = []
        self.assertEqual(daemon.run(), 0)
        self.wait.assert_called_once()
        self.read.assert_not_called()

    def test_waits_for_the_audio_server(self):
        daemon.list_sources.side_effect = AudioError("no server")
        self.assertEqual(daemon.run(), 0)
        self.wait.assert_called_once()

    def test_waiting_message_is_logged_once(self):
        logging.disable(logging.NOTSET)
        daemon.find_hid_interfaces.return_value = []
        self.wait.side_effect = [None, None, KeyboardInterrupt]
        with self.assertLogs("speechmike_ptt.daemon", "INFO") as logs:
            daemon.run()
        self.assertEqual(sum("No supported device" in line for line in logs.output), 1)


if __name__ == "__main__":
    unittest.main()
