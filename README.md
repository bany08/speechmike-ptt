# speechmike-ptt

Push-to-talk for the Philips SpeechMike on Linux: the microphone stays muted and only opens
while the REC button is held. Useful for voice control of AI assistants, without a microphone
that listens all the time. Philips ships no Linux software for these devices.

**Status: early development.** Device detection and the diagnostic mode work. The push-to-talk
daemon does not exist yet.

## Supported devices

| Device | USB ID | Status |
| --- | --- | --- |
| Philips SpeechMike III (LFH3200) | `0911:0c1c` | tested: detection, all dictation and function buttons |

## Diagnostic mode

Requires Python 3.9+ and `pactl`. No other dependencies.

```sh
python3 -m speechmike_ptt --debug
```

This lists the detected HID interfaces and the audio source, then prints every report the device
sends, with the bytes and bits that changed. It is read-only and does not touch the mute state.
Serial numbers are masked, so the output is safe to paste into an issue.

If the device is not readable, add this udev rule as
`/etc/udev/rules.d/70-speechmike.rules`, then replug the device:

```
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0911", ATTRS{idProduct}=="0c1c", TAG+="uaccess"
```

## License

MIT, see [LICENSE](LICENSE).
