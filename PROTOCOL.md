# display-ctl — serial protocol (v1.0)

Controls a 128×64 OLED display (plus a LED and a buzzer) attached to an
ESP32-C3 over USB. The ESP32 is **a display device only**: it has no buttons;
all control comes from the host computer over the serial port. This document is
the reference for writing a client program.

## 1. Transport

| Item | Value |
|------|-------|
| Port | USB serial (CDC). On Linux usually `/dev/ttyACM0` |
| Speed | `115200` 8N1 |
| Framing | One **command line** at a time, terminated by `\n` (LF) |
| `\r` | Ignored (you may send `\r\n`) |
| Text encoding | UTF-8 (Latin accents are supported by the fonts) |
| Limit | 256 characters per line |

On power-up/reset, the device emits a banner:

```
# display-ctl 1.0 ready
```

## 2. Line format

```
[@<id>] <COMMAND> [args...]
```

- **`@<id>` (optional):** a host-chosen identifier (e.g. `@42`). If present, it
  is **echoed** in the response — handy to match request/response.
- **`<COMMAND>`:** case-insensitive (`cls` = `CLS`).
- Arguments are space-separated. For text commands, **the text is the rest of
  the line** (it may contain spaces).
- Lines starting with `#` are **comments** (ignored, no response).
- Empty lines are ignored (no response).

## 3. Responses (bidirectional protocol)

Every processed command produces **exactly one** response line (comments and
empty lines produce none):

| Response | Meaning |
|----------|---------|
| `OK` | Command accepted/executed |
| `OK <data>` | Accepted, with data (e.g. `VERSION`) |
| `ERR <code> <message>` | Failure |
| `PONG` | Reply to `PING` |

With `@id`, the response is prefixed: `@42 OK`, `@42 ERR 4 invalid font`, etc.

### Error codes

| Code | Meaning |
|------|---------|
| 1 | unknown command |
| 2 | missing argument |
| 3 | invalid argument / out of range |
| 4 | invalid font |
| 5 | unknown image |
| 6 | line too long |

## 4. Screen model

The display has an **offscreen buffer**. Drawing commands (`TEXT`, `PRINT`)
modify the buffer but **do not appear** until a `SHOW`. This lets you compose a
whole screen without flicker. Typical sequence:

```
CLS                 # clear the buffer
FONT MED
TEXT 0 C web-01
TEXT 2 L CPU 42%
SHOW                # now it appears on screen
```

Exception: `IMG` draws and **shows immediately** (no `SHOW` needed), since it is
a full-screen alert/branding image.

### Lines and fonts

`TEXT <line>` is positioned by the current font height. Approximate capacity:

| Font | Size | Lines (0..N) | Columns approx. |
|------|------|--------------|-----------------|
| `SMALL` | 6×10 | 0..5 (6 lines) | ~21 |
| `MED` | 7×13 | 0..3 (4 lines) | ~18 |
| `BIG` | 10×20 | 0..2 (3 lines) | ~12 |

## 5. Commands

### Screen
| Command | Description |
|---------|-------------|
| `CLS` | Clear the buffer (does not show; use `SHOW`) |
| `SHOW` | Render the buffer to the display |
| `FONT <SMALL\|MED\|BIG>` | Set the font for following writes |
| `TEXT <line> <L\|C\|R> <text>` | Write `text` on `line`, aligned left (`L`), center (`C`) or right (`R`) |
| `PRINT <x> <y> <text>` | Write `text` at a pixel position (`y` = font baseline) |
| `CONTRAST <0-255>` | Display brightness |
| `IMG <splash\|warning\|panic>` | Show a full-screen image (immediate) |

The alignment accepts the short form `L`/`C`/`R` or the full form
`LEFT`/`CENTER`/`RIGHT` (only the first letter is read).

### Peripherals
| Command | Description |
|---------|-------------|
| `LED <ON\|OFF\|BLINK [ms]>` | Red LED. `BLINK` blinks (period `ms`, default 500) |
| `BEEP <freq_hz> <ms>` | Sound the buzzer for `ms` milliseconds (non-blocking) |
| `BUZZER <ON\|OFF>` | Turn the buzzer on/off continuously |

### System
| Command | Description |
|---------|-------------|
| `PING` | Replies `PONG` (liveness check) |
| `VERSION` | Replies `OK display-ctl <version>` |
| `RESET` | Clear the screen, turn LED and buzzer off, reset to font `SMALL` |

## 6. Example session

Host sends (`>`) / device replies (`<`):

```
< # display-ctl 1.0 ready
> @1 VERSION
< @1 OK display-ctl 1.0
> CLS
< OK
> FONT MED
< OK
> TEXT 0 C web-01
< OK
> TEXT 2 L CPU 42%  RAM 71%
< OK
> SHOW
< OK
```

Signal a problem:

```
> IMG warning
< OK
> LED BLINK 250
< OK
> BEEP 2000 400
< OK
```

Critical state:

```
> IMG panic
< OK
> BUZZER ON
< OK
```

## 7. Pinout (hardware reference)

| Function | GPIO |
|----------|------|
| OLED SDA | 8 |
| OLED SCL | 9 |
| Red LED | 3 (active high) |
| Buzzer | 4 (passive recommended) |

See [WIRING.md](WIRING.md) for full wiring details.

## 8. Implementation notes for the client

- Send a command, **read the response line** before sending the next one (the
  device processes synchronously and replies quickly).
- Ignore lines starting with `#` (comments/banner).
- `SHOW`/`IMG` take ~30 ms; the other commands are practically instantaneous.
- Images are fixed in the firmware. To change them, replace the project PNGs and
  run `tools/gen_images.py` (see the README).
