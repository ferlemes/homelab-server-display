#!/usr/bin/env python3
"""Play a little monophonic tune on the ESP32 buzzer using ONLY the display-ctl
protocol (the `BEEP <freq> <ms>` command). The ESP32 stays a dumb display
device — every bit of timing lives here on the host (see PROTOCOL.md).

Usage:
    python tools/play.py [PORT] [TUNE] [--bpm N]
    TUNE: one of the built-in names (default: ode); pass an unknown name to list.

Note: stop the monitor first if it is running, or it will fight for the buzzer.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from displayctl import DisplayCtl  # noqa: E402

# note name -> semitone offset from A; freq via equal temperament, A4 = 440 Hz
_A4 = 440.0
_SEMI = {"C": -9, "C#": -8, "Db": -8, "D": -7, "D#": -6, "Eb": -6, "E": -5,
         "F": -4, "F#": -3, "Gb": -3, "G": -2, "G#": -1, "Ab": -1, "A": 0,
         "A#": 1, "Bb": 1, "B": 2}


def freq(note):
    """'A4', 'C#5', ... -> Hz. 'R' (rest) -> 0."""
    if note == "R":
        return 0.0
    name, octave = note[:-1], int(note[-1])
    semis = _SEMI[name] + (octave - 4) * 12
    return _A4 * (2.0 ** (semis / 12.0))


# tunes: list of (note, beats); 1 beat = a quarter note. All public domain.
TUNES = {
    # Beethoven — Ode to Joy
    "ode": [("E4", 1), ("E4", 1), ("F4", 1), ("G4", 1), ("G4", 1), ("F4", 1),
            ("E4", 1), ("D4", 1), ("C4", 1), ("C4", 1), ("D4", 1), ("E4", 1),
            ("E4", 1.5), ("D4", .5), ("D4", 2)],
    # Twinkle Twinkle Little Star
    "twinkle": [("C4", 1), ("C4", 1), ("G4", 1), ("G4", 1), ("A4", 1), ("A4", 1),
                ("G4", 2), ("F4", 1), ("F4", 1), ("E4", 1), ("E4", 1), ("D4", 1),
                ("D4", 1), ("C4", 2)],
    # a rising major scale — handy for a quick buzzer check
    "scale": [(n, .5) for n in ("C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5")],
    # a short "all good" chirp and an "uh-oh" — fun status jingles
    "ok": [("C5", .5), ("E5", .5), ("G5", .75)],
    "uhoh": [("G4", .5), ("C4", 1)],
}


def play(d, tune, bpm=120, staccato=0.12):
    """Play `tune` (list of (note, beats)) at `bpm`. `staccato` is the fraction
    of each beat left silent so repeated/adjacent notes stay distinct."""
    beat_ms = 60000.0 / bpm
    for note, beats in tune:
        dur = beats * beat_ms
        f = freq(note)
        if f >= 50:                              # firmware BEEP floor is 50 Hz
            d.beep(int(round(f)), max(1, int(dur * (1 - staccato))))
        time.sleep(dur / 1000.0)
    d.buzzer("OFF")


def main():
    ap = argparse.ArgumentParser(description="Play a tune on the ESP32 buzzer")
    ap.add_argument("port", nargs="?", default="/dev/ttyACM0")
    ap.add_argument("tune", nargs="?", default="ode")
    ap.add_argument("--bpm", type=float, default=120.0)
    args = ap.parse_args()
    if args.tune not in TUNES:
        print("unknown tune %r; available: %s" % (args.tune, ", ".join(sorted(TUNES))),
              file=sys.stderr)
        sys.exit(2)
    d = DisplayCtl(args.port)
    try:
        play(d, TUNES[args.tune], bpm=args.bpm)
    finally:
        d.close()


if __name__ == "__main__":
    main()
