# Hardware wiring

Board: **ESP32-C3 Super Mini**. Powered from its own USB (which is also the
data/serial port). **No external supply needed** for a small OLED, LED and a
small piezo buzzer.

## Pin summary

| Signal | GPIO | Connects to |
|--------|------|-------------|
| OLED SDA | 8 | display SDA |
| OLED SCL | 9 | display SCL |
| OLED VCC | — | **3V3** (do not use 5V) |
| OLED GND | — | GND |
| Red LED (+) | 3 | 220–330 Ω resistor → LED anode |
| Buzzer (+) | 4 | buzzer + terminal |
| LED/buzzer (−) | — | GND |

> ⚠️ **GPIO8** is also the board's onboard LED — it may flicker slightly with
> I2C traffic. That is normal and harmless.

## Diagram

```
            ESP32-C3 Super Mini
          ┌──────────────────────┐
   3V3 ───┤ 3V3              GND ├─── GND  (common ground)
          │                      │
   SDA ───┤ GPIO8         GPIO3 ├───[220Ω]──►|── LED ──┐
   SCL ───┤ GPIO9         GPIO4 ├───┤ buzzer + │       │
          │                      │   │ buzzer − ├───────┤
          │      GPIO18/19 = USB │   └──────────┘       │
          └──────────────────────┘                     GND
   OLED:  VCC→3V3  GND→GND  SDA→GPIO8  SCL→GPIO9
```

### OLED (I2C, 4 wires)
The 0.96"/1.3" modules with 4 pins (VCC, GND, SCL, SDA) already include pull-up
resistors — **nothing extra needed**. I2C address: `0x3C` (`0x78` in 8-bit form).

### Red LED
`GPIO3 → 220–330 Ω resistor → anode (+, longer leg) → cathode (−) → GND`.
Lights on a high level (`LED ON` command). Without the resistor the LED burns out.

### Buzzer
- **Small passive piezo buzzer:** can connect directly between **GPIO4 and GND**.
  It plays the frequencies of the `BEEP`/`BUZZER` commands.
- **Magnetic/active buzzer (higher current):** use an NPN transistor
  (e.g. 2N2222/BC547): `GPIO4 →[1kΩ]→ base`, `emitter → GND`,
  `collector → buzzer −`, `buzzer + → 5V`, plus a flyback diode (1N4148) across
  the buzzer.

## Free GPIOs (for expansion)

In use: 3, 4, 8, 9. Reserved by USB: 18, 19. **Free and safe:**
`0, 1, 2, 5, 6, 7, 10, 20, 21` (GPIO2 is a strapping pin — prefer using it as an
input). In short: **plenty of pins left** for an RGB LED.

## Optional: RGB LED

Two options (both fit the free pins):

**A) Addressable WS2812 / NeoPixel — a single data pin**
```
WS2812:  VCC → 5V (board 5V pin)   GND → GND   DIN → GPIO10
```
Full color with one GPIO. Needs a firmware library (Adafruit NeoPixel) and new
commands (e.g. `RGB <r> <g> <b>`).

**B) Analog RGB LED (common cathode) — 3 pins + 3 resistors**
```
R → [330Ω] → GPIO5
G → [330Ω] → GPIO6     common cathode → GND
B → [330Ω] → GPIO7
```
Color mixing via PWM (LEDC). Simple firmware, no extra libraries.

> The current firmware uses the simple red LED. If you choose an RGB LED, the
> firmware support (commands + final wiring) can be added.
