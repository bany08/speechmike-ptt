# speechmike-ptt

Push-to-talk for the Philips SpeechMike on Linux: the microphone stays muted and only opens
while the REC button is held. A second button can dictate into any text field through a
speech-to-text tool.

## Why

Voice modes of AI assistants and other voice tools listen as long as the microphone is open. A
dictation microphone with real buttons is a good fit for this, but Philips ships its software
(SpeechControl, Device Control Center) for Windows only. On Linux the dictation buttons of the
SpeechMike do nothing: they produce no keyboard or input events at all, only raw HID reports.

This program reads those reports and controls the microphone. When in doubt, it stays muted.

**Status: early development.** Tested on one device and one system so far: an LFH3200 on Linux Mint
with X11 and PipeWire. Plain PulseAudio should work through `pactl`, but is untested.

## Supported devices

| Device | USB ID | Status |
| --- | --- | --- |
| Philips SpeechMike III (LFH3200) | `0911:0c1c` | tested: detection, all dictation and function buttons |

Other models need an entry in the device table, see [Adding a device](#adding-a-device).

## Installation

Requires Python 3.9+, `pactl` and a systemd user session. No other dependencies.

```sh
git clone https://github.com/bany08/speechmike-ptt.git
cd speechmike-ptt
./install.sh
```

This installs the program for your user, starts it as a systemd user service and adds a udev rule,
which is the only step that needs `sudo`.

```sh
systemctl --user status speechmike-ptt       # state
journalctl --user -u speechmike-ptt -f       # log
systemctl --user restart speechmike-ptt      # apply config changes
systemctl --user stop speechmike-ptt         # use the microphone normally
./install.sh --uninstall                     # remove everything except your config
```

The microphone stays muted after the service stops. Unmute it in your sound settings if you want to
use it without push-to-talk.

To try it without installing, run `python3 -m speechmike_ptt` in the repository. Stop the service
first, otherwise two instances run.

## Usage

The microphone is muted on start and opens while REC is held. It is muted again on exit, including
Ctrl+C, SIGTERM and SIGHUP. If the program is killed with SIGKILL it cannot clean up, so the
microphone keeps its last state. The service then restarts it after 2 seconds, which mutes it again.

While the microphone should be muted, the program checks this every 2 seconds and mutes it again if
another application or an audio server restart opened it. To use the microphone normally, stop the
program.

The device can be unplugged and plugged in at any time. If it is missing, the program waits for it,
and it mutes the microphone before it reacts to any button after reconnecting.

Muting works on the whole audio source. While the microphone is open, every application that
records from it can hear you, not only the one you are talking to. `pactl list source-outputs`
shows which applications these are.

## Configuration

Optional file `~/.config/speechmike-ptt/config.ini`. Without it, the defaults below apply. Restart
the service after changing it.

```ini
[speechmike-ptt]
# hold: open while the button is held. toggle: each press switches.
mode = hold
# REC, PLAY, FWD, REW, EOL_PRIO, INS_OVR, F1, F2, F3
button = REC
# Optional: use this audio source instead of detecting it (see `pactl list short sources`)
# source =
# Desktop notification on every switch, if notify-send is installed
notify = false
```

In toggle mode the microphone stays open until the next press, so it listens until you close it.
An invalid setting stops the program with an error message.

## Dictation

A second button can drive a speech-to-text tool such as [Handy](https://github.com/cjpais/Handy),
which types into the focused window. REC stays pure push-to-talk.

```ini
[dictation]
button = F1
on_press = pkill -USR2 -x handy
on_release = pkill -USR2 -x handy
# Keeps the microphone open and recording this long after the button is released,
# so the last word is not cut off
release_delay_ms = 400
# Text in the application name of the tool's audio stream, see `pactl list source-outputs`
recorder = cpal-pulseaudio
```

While the button is held the microphone is open and the tool records. After the release and the
delay, the program stops the tool and mutes the microphone.

`on_press` and `on_release` are commands, not shell lines. Tools like Handy only have a toggle, so a
stop sent at the wrong moment would start a recording. With `recorder` set, the program checks the
real audio stream first. It also stops recordings that keep running while the microphone is muted.

Notes for Handy:

- Do not use these signals with the AppImage directly. It runs a helper process that is also called
  `handy`, and the signal kills it. Use the `.deb` package or run `./Handy.AppImage --appimage-extract`
  and start `squashfs-root/AppRun`.
- Select the SpeechMike as input device, and set a paste method. Direct typing mixed up the letters
  in our tests, `Ctrl+Shift+V` worked in a terminal.
- Lower the input volume of the SpeechMike if the recordings are clipped.
- Start Handy inside your desktop session, for example with an autostart entry for
  `handy --start-hidden`, because it needs the display to paste text.
- Handy keeps recordings and a history of the text on disk. Check its settings before you dictate
  anything confidential.

## Diagnostic mode

```sh
python3 -m speechmike_ptt --debug
```

This lists the detected HID interfaces and the audio source, then prints every report the device
sends, with the bytes and bits that changed. It is read-only and does not touch the mute state.
Serial numbers are masked, so the output is safe to paste into an issue.

If the device is not readable, the udev rule from `packaging/70-speechmike.rules` is missing.
`./install.sh` adds it.

## Adding a device

Contributions for other SpeechMike models are welcome. Only add what you observed on real hardware.

1. Find the USB ID with `lsusb -d 0911:`. Do not post the output of `lsusb -v`, it contains the
   serial number of your device.
2. Add the ID to `packaging/70-speechmike.rules` and install the rule, or run `--debug` with `sudo`
   for a first look.
3. Add a `DeviceProfile` to `DEVICES` in `speechmike_ptt/devices.py`. The program ignores devices
   that are not in this table, so this comes before the diagnostic mode.
4. Run `python3 -m speechmike_ptt --debug`. It lists all HID interfaces of the device. If no reports
   appear when you press a button, change `button_interface` to the next interface and try again.
5. Press each button once and note the bits that change. Set `report_size`, `button_offset`,
   `button_length` and `buttons`, and `source_keyword` to a text from the audio source name.
6. Open a pull request or an issue with the `--debug` output. Serial numbers are masked there.

## Roadmap

- Control the LEDs of the device, for example to show that the microphone is open. The device has
  output reports, but their format is unknown.
- Support for more SpeechMike models.
- Test the udev rule on a clean system.

## License

MIT, see [LICENSE](LICENSE).
