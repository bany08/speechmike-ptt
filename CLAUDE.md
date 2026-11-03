# speechmike-ptt

Linux push-to-talk daemon for the Philips SpeechMike. Public open-source project (GitHub, MIT).

## Rules
- Python 3.9+, standard library only, no external dependencies.
- Code, comments and README in English. Talk to the owner in German.
- Work in small testable steps and say how to test each one.
- This repo is public. Never commit anything that identifies the owner or their hardware: serial
  numbers, MAC addresses, hostnames, personal paths or email addresses. Mask serials in all program
  output. Commits use the GitHub noreply address, set in the local git config of this repo.
- Only add button mappings that were observed on real hardware. Never infer them from a pattern.
- Fail safe: when in doubt, the microphone stays muted.
- The daemon runs as the normal user, not root (`pactl` needs the user's PipeWire session).

## Commands
- Tests: `python3 -m unittest discover -s tests`
- Diagnostics: `python3 -m speechmike_ptt --debug`

## Layout
- `speechmike_ptt/devices.py`: device table. New models are added here.
- `speechmike_ptt/hid.py`: sysfs discovery, report reading, bit diff.
- `speechmike_ptt/audio.py`: `pactl` wrapper, source discovery, mute, serial masking.
- `speechmike_ptt/daemon.py`: push-to-talk logic and main loop, muted on every exit path.
- `speechmike_ptt/config.py`: config file. Invalid settings are errors, never silent fallbacks.
- `speechmike_ptt/cli.py`: argument parsing and the `--debug` mode.

## Roadmap
1. Detection and `--debug` (done)
2. Map the buttons with `--debug` (done for the LFH3200, index finger button unresolved)
3. Mute control and `hold` mode, muted on start, exit and signals (done)
4. `toggle` mode, config file `~/.config/speechmike-ptt/config.ini`, configurable button (done)
5. Hotplug reconnect
6. Optional notifications (`notify-send`, can be disabled)
7. systemd user service, udev rule file, `install.sh` with uninstall
8. Final README, test on a clean system

Out of scope for now: LED control (output report format unknown; goes into the README roadmap),
testing on plain PulseAudio.

## Hardware facts: SpeechMike III, `0911:0c1c`
- Dictation buttons produce no evdev events. Only the trackball works as a mouse.
- There are 4 hidraw interfaces (`bInterfaceNumber` 03 to 06). Buttons report on interface 04.
  The `/dev/hidrawN` number is not stable, so find it through sysfs.
- Interface 04 descriptor: vendor page 0xFFA0, 9-byte input, 9-byte output, no report ID.
- sysfs values `idVendor`, `idProduct` and `bInterfaceNumber` are hex text.
- Reports are 9 bytes and each one arrives twice. Evaluate state changes only.
- Bytes 7 and 8 are a 16-bit big-endian button bitmask. Several buttons combine (REC+PLAY = 0x0005).
  Bytes 1 to 5 change with unknown meaning and are ignored.

  | Button | Mask | | Button | Mask |
  | --- | --- | --- | --- | --- |
  | REC | 0x0001 | | INS/OVR | 0x0040 |
  | PLAY/PAUSE | 0x0004 | | F1 | 0x0200 |
  | `>>` FWD | 0x0008 | | F2 | 0x0400 |
  | `<<` REW | 0x0010 | | F3 | 0x0800 |
  | EOL/PRIO | 0x0020 | | | |

- EOL/PRIO sends the same bit for single and double press. PRIO is not a separate signal.
- Left and right mouse buttons are not on interface 04. They report on interface 03 (4-byte mouse
  report, byte 0: 0x01 left, 0x02 right). Interfaces 05 and 06 stayed silent for all buttons.

  ```
  REC pressed:    80 00 00 00 00 35 00 00 01
  REC released:   80 00 00 00 84 2a 00 00 00
  PLAY pressed:   80 34 00 08 00 02 00 00 04
  PLAY released:  80 20 00 20 4b 13 00 00 00
  ```
- The LFH3200 has F1 to F3 only.
- The EOL/PRIO and INS/OVR buttons have their own LEDs, in addition to the device LED.
- Index finger button (inside of the device): unresolved. It produced no report on interface 04. On
  interface 03 byte 0 went to 0x01 and no return to 0x00 was seen, also not after left clicks. The
  button does not latch mechanically and no LED lights up when it is pressed. The captures suppressed
  identical reports, so repeated presses may be hidden. Not usable for
  push-to-talk as far as known.

## Audio facts
- Source name: `alsa_input.usb-Philips_Speech_Processing_SpeechMike_III_<SERIAL>-00.mono-fallback`
  (s16le, 1 channel, 22050 Hz). The speaker adds an `alsa_output...monitor` source, which must be excluded.
- The USB serial from sysfs appears in the source name. It links a HID device to its source.
- `pactl set-source-mute <source> 1|0` works.
- `pactl` output is localized, so always run it with `LC_ALL=C`.

## udev
Rule, not filtered by interface because udev cannot reliably combine ATTRS of different parents:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0911", ATTRS{idProduct}=="0c1c", TAG+="uaccess"
```

The development machine has unrelated broad hidraw rules, so access there does not prove this rule
is sufficient. Verify it on a clean system (roadmap step 8).
