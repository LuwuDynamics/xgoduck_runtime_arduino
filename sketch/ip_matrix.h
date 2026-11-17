#pragma once

#include <Arduino_LED_Matrix.h>
#include <zephyr/sys/atomic.h>

static atomic_t startupIpOctet = ATOMIC_INIT(-1);

static bool receiveStartupIp(int octet) {
  if (octet < 0 || octet > 255) return false;
  // A retry or a restart of the Linux voice process must not restart the timer.
  atomic_cas(&startupIpOctet, -1, octet);
  return true;
}

static void renderIpOctet(Arduino_LED_Matrix& matrix, unsigned octet) {
  static constexpr uint8_t digits[10][5] = {
    {7, 5, 5, 5, 7}, {2, 6, 2, 2, 7}, {7, 1, 7, 4, 7}, {7, 1, 7, 1, 7}, {5, 5, 7, 1, 1},
    {7, 4, 7, 1, 7}, {7, 4, 7, 5, 7}, {7, 1, 1, 1, 1}, {7, 5, 7, 5, 7}, {7, 5, 7, 1, 7}
  };
  uint8_t pixels[8][13] = {};
  const unsigned count = octet >= 100 ? 3 : (octet >= 10 ? 2 : 1);
  const unsigned left = (13 - (count * 4 - 1)) / 2;
  for (unsigned i = count; i > 0; --i, octet /= 10) {
    const unsigned x = left + (i - 1) * 4;
    for (unsigned row = 0; row < 5; ++row) {
      for (unsigned col = 0; col < 3; ++col) {
        pixels[1 + row][x + col] = (digits[octet % 10][row] >> (2 - col)) & 1;
      }
    }
  }
  matrix.renderBitmap(pixels, 8, 13);
}

// Called by the Arduino loop, outside the motion and Bridge threads.
static void serviceStartupIpDisplay() {
  static Arduino_LED_Matrix matrix;
  static bool shown = false, visible = false;
  static uint32_t shownAt = 0;
  const int octet = atomic_get(&startupIpOctet);
  if (!shown && octet >= 0) {
    matrix.begin();
    renderIpOctet(matrix, octet);
    shownAt = millis();
    shown = visible = true;
  } else if (visible && uint32_t(millis() - shownAt) >= 60000u) {
    matrix.clear();
    matrix.end();
    visible = false;
  }
}
