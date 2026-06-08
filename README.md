# display-ctl — serial display controller (ESP32-C3)

Turns an ESP32-C3 Super Mini + 128×64 I2C OLED into a **serial-controlled
display device**, to show information from a headless computer. The host sends
text commands over USB; the ESP32 draws text/images and drives a LED and a
buzzer. The ESP32 has no buttons — it is display-only.

➡️ The control language is documented in **[`PROTOCOL.md`](PROTOCOL.md)** — the
reference for writing a client program. Wiring is in **[`WIRING.md`](WIRING.md)**;
deploying the monitor as a service is in **[`INSTALL.md`](INSTALL.md)**.

## Hardware / wiring

| Function | GPIO | Note |
|----------|------|------|
| OLED SDA | 8 | I2C |
| OLED SCL | 9 | I2C |
| OLED VCC / GND | 3V3 / GND | address `0x3C` |
| Red LED | 3 | active high (GPIO→resistor→LED→GND) |
| Buzzer | 4 | passive (recommended), via LEDC |

Full details and diagrams in [WIRING.md](WIRING.md).

## Layout

```
src/main.cpp      firmware: command parser + ACK, fonts, images, LED, buzzer
src/images.h      1-bit bitmaps (splash, warning, panic) — generated
PROTOCOL.md       serial protocol spec (client reference)
WIRING.md         how to wire the display, LED and buzzer
INSTALL.md        deploy the monitor as a systemd service
tools/gen_images.py   PNG -> src/images.h
tools/displayctl.py   reference Python client + demo
tools/monitor.py      daemon: host metrics -> display (with alerts)
splash.png warning.png panic.png   source art (2:1) for the images
```

## Build / flash / test

PlatformIO is installed under `~/.pio-venv`. The upload uses `sg dialout` to work
around the serial-port permission in this session (you are already in the
`dialout` group; after a future logout/login it is no longer needed).

```bash
# build
~/.pio-venv/bin/pio run -d ~/esp32/server-display

# flash
sg dialout -c 'PLATFORMIO_UPLOAD_PORT=/dev/ttyACM0 ~/.pio-venv/bin/pio run -d ~/esp32/server-display -t upload'

# test (reference demo: status -> warning -> panic -> normal)
sg dialout -c '~/.pio-venv/bin/python ~/esp32/server-display/tools/displayctl.py /dev/ttyACM0'

# talk to the device by hand
sg dialout -c '~/.pio-venv/bin/pio device monitor -p /dev/ttyACM0 -b 115200'
```

## Host monitor (daemon)

`tools/monitor.py` reads host metrics and shows them on the display as rotating
pages, driving LED/buzzer/alert images by threshold. It only needs `pyserial` +
stdlib (reads `/proc`, `/sys`; uses `timedatectl`/`systemctl`/`who` if present).

```bash
# run (Ctrl+C to quit; exits with LED/buzzer off)
sg dialout -c '~/.pio-venv/bin/python ~/esp32/server-display/tools/monitor.py /dev/ttyACM0'

~/.pio-venv/bin/python tools/monitor.py --once          # one frame and exit
~/.pio-venv/bin/python tools/monitor.py --no-alerts     # no LED/buzzer
```

Rotating pages (SMALL font, top title + bottom clock/NTP bar):

| Page | Shows |
|------|-------|
| **HOST** | hostname (large) + primary IP |
| **NET** | primary IP, gateway+iface, RX/TX rate, SSH sessions, logged-in users |
| **CPU/MEM** | CPU%, load avg (1/5/15), MEM% + used/total, swap% |
| **DISK** | mount usage % + used/total, temperature, top-CPU process |
| **SYSTEM** | uptime, failed systemd units, pending reboot, users/SSH |

Alerts (edge-triggered, thresholds in `THRESH`):
- **warning** (CPU≥85, MEM≥85, DISK≥90, TEMP≥75, or a failed systemd unit):
  `warning` image, one beep, slow LED blink.
- **panic** (CPU≥96, MEM≥95, DISK≥97, TEMP≥88, or **no network**): `panic`
  image, low beep, fast LED blink.

To run on boot, see [INSTALL.md](INSTALL.md).

## Change the images

Replace `splash.png` / `warning.png` / `panic.png` (ideal aspect ratio **2:1**)
and regenerate the header:

```bash
~/.pio-venv/bin/python tools/gen_images.py   # -> src/images.h + /tmp/images_preview.png
```

## Technical notes (lessons learned)

- **U8g2 + HW I2C:** do not call `Wire.begin()` manually — pass the pins in the
  constructor and let U8g2 init the Wire bus, otherwise `begin()` **hangs**. This
  way it runs at 400 kHz.
- **Buzzer:** use the LEDC channel API (`ledcSetup`/`ledcAttachPin`/
  `ledcWriteTone`) initialized **once** in `setup()`. Do not use `tone()`/
  `noTone()`: they de-initialize the channel and emit an ESP-IDF error log that
  **leaks onto the serial and corrupts the protocol**.
- **Clean protocol:** `esp_log_level_set("*", ESP_LOG_NONE)` in `setup()` keeps
  ESP-IDF logs out of the command responses.
