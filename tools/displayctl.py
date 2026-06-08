#!/usr/bin/env python3
"""Reference client for the display-ctl protocol (see PROTOCOL.md).

As a library:
    from displayctl import DisplayCtl
    d = DisplayCtl("/dev/ttyACM0")
    d.cls(); d.font("MED"); d.text(0, "C", "web-01"); d.show()

As a demo CLI:
    python displayctl.py /dev/ttyACM0
"""
import re
import sys
import time
import serial

# ESP-IDF log lines, e.g. "E (1234) ledc: ..." — never a protocol response
_LOG_RE = re.compile(r"^[EWIDV] \(\d+\)")


class DisplayError(Exception):
    pass


class DisplayCtl:
    def __init__(self, port, baud=115200, timeout=2.0, settle=1.8):
        self.ser = serial.Serial(port, baud, timeout=timeout)
        time.sleep(settle)            # the ESP32-C3 may reset when the port opens
        self.ser.reset_input_buffer()

    # --- core: send one line and read the response (skip comments/logs) ---
    def cmd(self, line):
        self.ser.write((line + "\n").encode("utf-8"))
        self.ser.flush()
        while True:
            resp = self.ser.readline().decode("utf-8", "replace").strip()
            if resp == "":
                raise DisplayError("timeout waiting for response to: %r" % line)
            if resp.startswith("#") or _LOG_RE.match(resp):  # banner/comment/log
                continue
            if resp.startswith("ERR"):
                raise DisplayError("%s  (command: %r)" % (resp, line))
            return resp                # "OK", "OK ...", "PONG"

    # --- high-level helpers ---
    def cls(self):                 return self.cmd("CLS")
    def show(self):                return self.cmd("SHOW")
    def font(self, size):          return self.cmd("FONT %s" % size)
    def text(self, line, align, s):return self.cmd("TEXT %d %s %s" % (line, align, s))
    def printxy(self, x, y, s):    return self.cmd("PRINT %d %d %s" % (x, y, s))
    def img(self, name):           return self.cmd("IMG %s" % name)
    def contrast(self, v):         return self.cmd("CONTRAST %d" % v)
    def led(self, state, ms=None): return self.cmd("LED %s%s" % (state, "" if ms is None else " %d" % ms))
    def beep(self, freq, ms):      return self.cmd("BEEP %d %d" % (freq, ms))
    def buzzer(self, state):       return self.cmd("BUZZER %s" % state)
    def reset(self):               return self.cmd("RESET")
    def ping(self):                return self.cmd("PING")
    def version(self):             return self.cmd("VERSION")

    def close(self):
        self.ser.close()


def demo(port):
    d = DisplayCtl(port)
    log = lambda c, r: print("> %-28s< %s" % (c, r))

    log("VERSION", d.version())
    log("PING", d.ping())

    # composed status screen
    d.cls()
    d.font("MED")
    log("TEXT 0 C web-01", d.text(0, "C", "web-01"))
    d.font("SMALL")
    log("TEXT 2 L ...", d.text(2, "L", "CPU 42%  RAM 71%"))
    log("TEXT 3 L ...", d.text(3, "L", "disk 88%  up 12d"))
    log("TEXT 5 R online", d.text(5, "R", "online"))
    log("SHOW", d.show())
    time.sleep(2.5)

    # warning
    log("IMG warning", d.img("warning"))
    log("LED BLINK 250", d.led("BLINK", 250))
    log("BEEP 2000 250", d.beep(2000, 250))
    time.sleep(2.5)

    # critical
    log("IMG panic", d.img("panic"))
    log("BEEP 1200 400", d.beep(1200, 400))
    time.sleep(2.5)

    # back to normal
    log("LED OFF", d.led("OFF"))
    log("BUZZER OFF", d.buzzer("OFF"))
    d.cls(); d.font("SMALL")
    d.text(2, "C", "all ok :)")
    log("SHOW", d.show())
    d.close()
    print("demo done.")


if __name__ == "__main__":
    demo(sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyACM0")
