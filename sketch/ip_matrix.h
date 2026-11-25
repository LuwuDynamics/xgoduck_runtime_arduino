#pragma once

#include <Arduino_LED_Matrix.h>
#include <stdio.h>
#include <zephyr/sys/atomic.h>

// Publish the four octets together and ignore subsequent startup retries.
static atomic_t startupIpClaimed = ATOMIC_INIT(0);
static atomic_t startupIpReady = ATOMIC_INIT(0);
static uint8_t startupIp[4];

static bool receiveStartupIp(int a, int b, int c, int d) {
  const int octets[] = {a, b, c, d};
  for (int value : octets) {
    if (value < 0 || value > 255) return false;
  }
  if (atomic_cas(&startupIpClaimed, 0, 1)) {
    for (int i = 0; i < 4; ++i) startupIp[i] = octets[i];
    atomic_set(&startupIpReady, 1);
  }
  return true;
}

// Up to 12 digits (three columns each), three dots and 14 spaces.
static uint8_t ipColumns[12 * 3 + 3 + 14];

static int buildIpColumns() {
  static constexpr uint8_t digits[10][5] = {
    {7, 5, 5, 5, 7}, {2, 6, 2, 2, 7}, {7, 1, 7, 4, 7}, {7, 1, 7, 1, 7}, {5, 5, 7, 1, 1},
    {7, 4, 7, 1, 7}, {7, 4, 7, 5, 7}, {7, 1, 1, 1, 1}, {7, 5, 7, 5, 7}, {7, 5, 7, 1, 7}
  };
  char address[16];
  snprintf(address, sizeof(address), "%u.%u.%u.%u", unsigned(startupIp[0]),
           unsigned(startupIp[1]), unsigned(startupIp[2]), unsigned(startupIp[3]));
  int width = 0;
  for (const char *ch = address; *ch; ++ch) {
    if (ch != address) ipColumns[width++] = 0;
    if (*ch == '.') {
      ipColumns[width++] = 1u << 4;
      continue;
    }
    for (int col = 0; col < 3; ++col) {
      uint8_t bits = 0;
      for (int row = 0; row < 5; ++row) {
        if (digits[*ch - '0'][row] & (4u >> col)) bits |= 1u << row;
      }
      ipColumns[width++] = bits;
    }
  }
  return width;
}

static void renderIpFrame(Arduino_LED_Matrix& matrix, int width, int left) {
  uint8_t pixels[8][13] = {};
  for (int x = 0; x < 13; ++x) {
    const int col = x - left;
    if (col < 0 || col >= width) continue;
    for (int row = 0; row < 5; ++row) {
      pixels[row + 1][x] = (ipColumns[col] >> row) & 1;
    }
  }
  // The 104-pixel UNO Q frame needs four words. The core's bitmap helper owns only three.
  uint32_t frame[4];
  Arduino_LED_Matrix::loadPixelsToBuffer(&pixels[0][0], sizeof(pixels), frame);
  matrix.loadFrame(frame);
}

// Called by the Arduino loop, outside the motion and Bridge threads.
static void serviceStartupIpDisplay() {
  static Arduino_LED_Matrix matrix;
  static bool shown = false, visible = false;
  static int width = 0;
  static uint32_t shownAt = 0;
  if (!shown && atomic_get(&startupIpReady)) {
    width = buildIpColumns();
    matrix.begin();
    shownAt = millis();
    shown = visible = true;
  }
  if (!visible) return;
  const uint32_t elapsed = millis() - shownAt;
  if (elapsed >= 30000u) {
    matrix.clear();
    // Let the scanner drive the LEDs off before stopping it.
    delay(2);
    matrix.end();
    visible = false;
    return;
  }

  // The rightmost column enters first, with one blank frame between passes.
  const int step = (elapsed / 100u) % (width + 13);
  const int left = 1 - width + step;
  static int previousLeft = 13;
  if (left != previousLeft) {
    renderIpFrame(matrix, width, left);
    previousLeft = left;
  }
}
