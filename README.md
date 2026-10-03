# kbd-debounce — a software keyboard chatter filter for Linux

*Русская версия: [ПРОЧТИ.md](ПРОЧТИ.md)*

MacBook keyboards with the butterfly mechanism (2015–2019) start to chatter as they age: one keystroke produces two letters. macOS partly hides this in firmware and in the OS; Linux does not. `kbd-debounce` removes the false repeats in software, at the evdev level, without touching the driver or the keyboard layout.

The filter was written and tuned on a 12" MacBook (MacBook10,1, 2017, `applespi` driver) running Manjaro with GNOME on Wayland, but it is tied neither to that model nor to a desktop environment: it works with any keyboard that evdev can see.

## How it works

The service grabs the keyboard exclusively (`EVIOCGRAB`), creates a virtual keyboard named `kbd-debounce` through uinput, and forwards events to it through the filter. The desktop sees only the virtual keyboard, so the filter behaves the same under Wayland, X11 and on the text console.

A plain time threshold ("ignore a repeat sooner than N ms") is not enough. Measurements showed two kinds of chatter:

| Kind | Gap between release and the false press | How long the false press lasts |
| --- | --- | --- |
| Contact break in the middle of a keystroke | 2–50 ms | any (it is the rest of the real keystroke) |
| Delayed false trigger | 90–250 ms | 4–55 ms |

By its gap, the second kind is indistinguishable from fast typing of double letters ("account", "tool"): a human makes the same pause. Raise the threshold to 100–200 ms and the filter starts eating real double letters and quick double taps on the arrow keys and Backspace. What does tell them apart is duration: a false press lasts no more than 55 ms, while a human holds a key for 80 ms or longer.

Hence two stages:

1. **Fast.** A repeated press of the same key sooner than `--ms` (50 ms) after its release is dropped immediately.
2. **Hold-back.** A repeated press within the window from `--ms` to `--slow-ms` (50–250 ms), with no other key pressed in between, is not forwarded at once but held back for `--hold-ms` (55 ms):
   - the key is released within that time — it is chatter, and both the press and the release are discarded;
   - the key is still down, or another key is pressed — the press is real and is forwarded.

The cost is a delay of up to 55 ms, and only for a repeat of the same key within a quarter of a second. Ordinary typing is not delayed at all.

Known limitation: a real repeated press shorter than `--hold-ms`, made within the window, is lost. In practice these are very light taps; if you lose them, lower `--hold-ms`.

## Contents

| File | Purpose |
| --- | --- |
| `kbd-debounce.py` | the filter itself (evdev → uinput) |
| `kbd-debounce.service` | hardened systemd service |
| `60-kbd-debounce.rules` | udev rule that starts the service when the keyboard appears |
| `kbd-diag.py` | diagnostics: what gets through the filter |

The program's messages and journal output are in Russian; the lines you will need are quoted below.

## Installation

Requires Python 3.10 or newer and python-evdev:

```
pamac install --no-confirm python-evdev      # Manjaro
sudo pacman -S python-evdev                  # Arch
sudo apt install python3-evdev               # Debian, Ubuntu
sudo dnf install python3-evdev               # Fedora
```

Find the name of your keyboard:

```
grep -E '^N: Name' /proc/bus/input/devices
```

If it is not `Apple SPI Keyboard`, replace the name in two places: the `--name` argument in `kbd-debounce.service` and `ATTRS{name}` in `60-kbd-debounce.rules`.

Install the files and start the filter:

```
sudo install -m 755 kbd-debounce.py /usr/local/bin/kbd-debounce.py
sudo install -m 644 kbd-debounce.service /etc/systemd/system/kbd-debounce.service
sudo install -m 644 60-kbd-debounce.rules /etc/udev/rules.d/60-kbd-debounce.rules
sudo systemctl daemon-reload
sudo udevadm control --reload
sudo udevadm trigger --action=add --subsystem-match=input
```

Check:

```
systemctl status kbd-debounce
```

The journal should show a line like `устройство: /dev/input/event4 (Apple SPI Keyboard), порог 50.0 мс, окно 250.0 мс, удержание 55.0 мс` (device, fast threshold, window, hold-back time).

The service deliberately has no `[Install]` section and does not need `systemctl enable`: the udev rule starts it the moment the keyboard appears — at boot and after every driver rebind (for example, after suspend). If the keyboard disappears, the filter exits with code 0 and is started again by the same rule.

The service needs `/dev/uinput`. If the node is missing, load the module with `sudo modprobe uinput`, and to load it permanently create `/etc/modules-load.d/uinput.conf` containing the line `uinput`.

## Tuning

| Option | Default | Meaning |
| --- | --- | --- |
| `--name` | `Apple SPI Keyboard` | part of the device name (case-insensitive) |
| `--ms` | 50 | a repeat sooner than this is dropped unconditionally |
| `--slow-ms` | 250 | upper bound of the suspicious-repeat window |
| `--hold-ms` | 55 | how long a suspicious press is held back |
| `--log` | off | log every dropped repeat to the journal |

The thresholds are set in the `ExecStart` line of the service file; after editing run `sudo systemctl daemon-reload && sudo systemctl restart kbd-debounce`.

Tune them from your own statistics. With `--log` (enabled in the supplied service file) every dropped repeat goes to the journal:

```
journalctl -u kbd-debounce | grep дребезг
```

```
дребезг KEY_V: пауза 25 мс, удержание 92 мс (быстрый)
дребезг KEY_N: пауза 119 мс, удержание 49 мс (придержан)
```

Here `пауза` is the gap, `удержание` is the hold duration, `быстрый` marks the fast stage and `придержан` the hold-back stage.

How to read it:

- **Double letters still get through** — run `sudo ./kbd-diag.py --seconds 90 --max-ms 500` and type ordinary text. The tool listens to the virtual keyboard, so it shows what passed the filter: the gap and hold duration of every repeat. Repeats with a short hold that exceeds `--hold-ms` mean `--hold-ms` should be raised; repeats with a gap above `--slow-ms` mean the window should be widened.
- **Real double taps are lost** — look in the journal for `быстрый` entries with a gap close to `--ms` and a hold of 80 ms or more: those are your own keystrokes, lower `--ms`. If the lost ones are `придержан` entries with a hold close to `--hold-ms`, lower `--hold-ms`.

For reference, measurements on one MacBook10,1 unit: with `--ms 80` the filter ate quick double taps on the arrow keys, Backspace and CapsLock (gaps of 50–76 ms, holds from 87 ms); a 65 ms hold threshold ate real double letters during fast typing.

## Security

The filter sees every keystroke, so it runs as root but with privileges cut down as far as possible: `ProtectSystem=strict`, `ProtectHome=yes`, an empty `CapabilityBoundingSet`, no network (`RestrictAddressFamilies=none`), and device access limited to input devices and `/dev/uinput` (`DevicePolicy=closed`). You can rate the sandboxing with `systemd-analyze security kbd-debounce.service`.

The `--log` output contains only the key codes of dropped repeats, not the text you type. If even that is unwanted, remove `--log` from `ExecStart`.

## Good to know

- With a chattering keyboard it is easy to mistype a password three times in a row and get locked out by `pam_faillock` for 10 minutes. Until the filter is tuned you can disable the lockout: `deny = 0` in `/etc/security/faillock.conf`.
- Layouts, XKB options and shortcuts are not affected: the virtual keyboard mirrors the capabilities of the real one.
- To switch the filter off temporarily: `sudo systemctl stop kbd-debounce` — the keyboard goes back to working directly.

## Removal

```
sudo systemctl stop kbd-debounce
sudo rm /etc/udev/rules.d/60-kbd-debounce.rules /etc/systemd/system/kbd-debounce.service /usr/local/bin/kbd-debounce.py
sudo systemctl daemon-reload
sudo udevadm control --reload
```

## License

© 2026 Alex Elph. GNU General Public License version 3 (GPL-3.0-only); see [LICENSE](LICENSE) for the full text.
