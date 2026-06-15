// Hand-written boot chime: a C-major fanfare (G4 C5 E5 G5  G4 G5) — a generic
// "system started" bugle-style signal. {frequency_hz, ms}; 0 Hz = a rest.
// Edit freely, or regenerate from audio with tools/mp3_to_tune.py.
#pragma once
#include <stdint.h>

static const uint16_t BOOT_TUNE[][2] = {
  {392,110}, {0,16}, {523,110}, {0,16}, {659,120}, {0,16},
  {784,300}, {0,16}, {392,120}, {0,16}, {784,440},
};
static const uint16_t BOOT_TUNE_LEN = 11;
