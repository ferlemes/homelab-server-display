// Hand-written boot chime: an ascending C-major run (C5 E5 G5 E5 G5 C6) — the
// "system started" signal. {frequency_hz, ms}; 0 Hz = a rest.
// Edit freely, or regenerate from audio with tools/mp3_to_tune.py.
#pragma once
#include <stdint.h>

static const uint16_t BOOT_TUNE[][2] = {
  {523,120}, {0,18}, {659,120}, {0,18}, {784,110}, {0,18},
  {659,90}, {0,18}, {784,90}, {0,18}, {1047,320},
};
static const uint16_t BOOT_TUNE_LEN = 11;
