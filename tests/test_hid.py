import os
import tempfile
import unittest

from speechmike_ptt.cli import format_report
from speechmike_ptt.devices import DEVICES
from speechmike_ptt.hid import DeviceGone, diff_reports, find_hid_interfaces, read_reports

PROFILE = DEVICES[0]


class FakeSysfs:
    """Builds a sysfs tree shaped like the real one."""

    def __init__(self, root):
        self.root = root
        os.makedirs(os.path.join(root, "class", "hidraw"))

    def add(self, number, vendor, product, interface, serial="TESTSERIAL"):
        usb = os.path.join(self.root, "devices", "usb1", f"1-{vendor}-{product}")
        iface = os.path.join(usb, f"1-1:1.{interface}")
        hid = os.path.join(iface, f"0003:{vendor}:{product}.{number:04X}")
        os.makedirs(hid)
        self._write(usb, "idVendor", vendor)
        self._write(usb, "idProduct", product)
        if serial:
            self._write(usb, "serial", serial)
        self._write(iface, "bInterfaceNumber", interface)
        self._link(number, hid)

    def add_virtual(self, number):
        hid = os.path.join(self.root, "devices", "virtual", "misc", "uhid", f"x.{number}")
        os.makedirs(hid)
        self._link(number, hid)

    def _link(self, number, target):
        entry = os.path.join(self.root, "class", "hidraw", f"hidraw{number}")
        os.makedirs(entry)
        os.symlink(target, os.path.join(entry, "device"))

    @staticmethod
    def _write(directory, name, value):
        with open(os.path.join(directory, name), "w") as f:
            f.write(value + "\n")


class FindHidInterfacesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.sysfs = FakeSysfs(os.path.join(tmp.name, "sys"))
        self.dev = os.path.join(tmp.name, "dev")
        os.makedirs(self.dev)

    def find(self):
        return find_hid_interfaces(sysfs_root=self.sysfs.root, dev_root=self.dev)

    def test_finds_all_interfaces_and_marks_the_button_interface(self):
        for number, interface in ((8, "03"), (9, "04"), (10, "05"), (11, "06")):
            self.sysfs.add(number, "0911", "0c1c", interface)
        found = self.find()
        self.assertEqual([item.interface for item in found], [3, 4, 5, 6])
        buttons = [item for item in found if item.carries_buttons]
        self.assertEqual([os.path.basename(item.node) for item in buttons], ["hidraw9"])
        self.assertEqual(buttons[0].serial, "TESTSERIAL")

    def test_sorts_numerically(self):
        self.sysfs.add(10, "0911", "0c1c", "05")
        self.sysfs.add(9, "0911", "0c1c", "04")
        self.assertEqual([item.interface for item in self.find()], [4, 5])

    def test_ignores_other_vendors_and_virtual_devices(self):
        self.sysfs.add(0, "04d9", "1603", "04")
        self.sysfs.add_virtual(7)
        self.assertEqual(self.find(), [])

    def test_interface_number_is_hex(self):
        self.sysfs.add(1, "0911", "0c1c", "0a")
        self.assertEqual(self.find()[0].interface, 10)

    def test_missing_serial(self):
        self.sysfs.add(9, "0911", "0c1c", "04", serial=None)
        self.assertIsNone(self.find()[0].serial)

    def test_readable_reflects_the_node(self):
        self.sysfs.add(9, "0911", "0c1c", "04")
        self.assertFalse(self.find()[0].readable)
        open(os.path.join(self.dev, "hidraw9"), "w").close()
        self.assertTrue(self.find()[0].readable)

    def test_no_hidraw_class(self):
        self.assertEqual(find_hid_interfaces(sysfs_root=self.dev, dev_root=self.dev), [])


class ReadReportsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.node = os.path.join(tmp.name, "fifo")
        os.mkfifo(self.node)
        # Opened for reading and writing, so that opening the reader does not block.
        self.writer = os.open(self.node, os.O_RDWR)

    def tearDown(self):
        if self.writer is not None:
            os.close(self.writer)

    def test_yields_none_on_timeout_and_then_data(self):
        reader = read_reports(self.node, timeout=0.01)
        self.assertIsNone(next(reader))
        os.write(self.writer, b"\x80\x01")
        self.assertEqual(next(reader), b"\x80\x01")
        reader.close()

    def test_without_timeout_returns_data(self):
        os.write(self.writer, b"\x80\x01")
        reader = read_reports(self.node)
        self.assertEqual(next(reader), b"\x80\x01")
        reader.close()

    def test_end_of_data_means_device_gone(self):
        reader = read_reports(self.node, timeout=1)
        os.write(self.writer, b"\x80")
        next(reader)
        os.close(self.writer)
        self.writer = None
        with self.assertRaises(DeviceGone):
            next(reader)


REC_DOWN = bytes.fromhex("800000000035000001")
REC_UP = bytes.fromhex("80000000842a000000")
PLAY_DOWN = bytes.fromhex("803400080002000004")


class ReportTest(unittest.TestCase):
    def test_decodes_observed_reports(self):
        self.assertEqual(PROFILE.decode_buttons(REC_DOWN), (["REC"], 0))
        self.assertEqual(PROFILE.decode_buttons(REC_UP), ([], 0))
        self.assertEqual(PROFILE.decode_buttons(PLAY_DOWN), (["PLAY"], 0))

    def test_decodes_every_mapped_button(self):
        observed = {
            "REC": "0001",
            "PLAY": "0004",
            "FWD": "0008",
            "REW": "0010",
            "EOL_PRIO": "0020",
            "INS_OVR": "0040",
            "F1": "0200",
            "F2": "0400",
            "F3": "0800",
        }
        for name, mask in observed.items():
            report = bytes.fromhex("8000000084" + "2a00" + mask)
            self.assertEqual(PROFILE.decode_buttons(report), ([name], 0))

    def test_decodes_two_buttons_at_once(self):
        report = bytes.fromhex("802000 20c135 00 0005".replace(" ", ""))
        self.assertEqual(PROFILE.decode_buttons(report), (["REC", "PLAY"], 0))

    def test_reports_unknown_bits(self):
        report = bytes.fromhex("800000000000008002")
        self.assertEqual(PROFILE.decode_buttons(report), ([], 0x8002))

    def test_short_report(self):
        self.assertEqual(PROFILE.decode_buttons(b"\x80"), ([], 0))

    def test_diff_lists_changed_bytes_and_bits(self):
        changes = diff_reports(REC_DOWN, REC_UP)
        self.assertEqual([c.index for c in changes], [4, 5, 8])
        last = changes[-1]
        self.assertEqual((last.set_bits, last.cleared_bits), (0, 0x01))
        self.assertEqual((changes[0].set_bits, changes[0].cleared_bits), (0x84, 0))

    def test_diff_of_equal_reports_is_empty(self):
        self.assertEqual(diff_reports(REC_DOWN, REC_DOWN), [])

    def test_diff_handles_different_lengths(self):
        self.assertEqual([c.index for c in diff_reports(b"\x01", b"\x01\x02")], [1])

    def test_format_shows_buttons_and_diff(self):
        lines = format_report(PROFILE, REC_DOWN, REC_UP)
        self.assertIn("80 00 00 00 00 35 00 00 01  buttons: REC", lines[0])
        self.assertIn("    byte 8: 0x00 -> 0x01  set 0x01", lines)

    def test_format_flags_unexpected_length(self):
        lines = format_report(PROFILE, b"\x80\x00", None)
        self.assertIn("unexpected length", lines[1])


if __name__ == "__main__":
    unittest.main()
