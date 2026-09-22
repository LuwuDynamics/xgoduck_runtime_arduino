#pragma once

#include <Arduino.h>

static const uint8_t NUM_MOTORS = 15;
static const uint32_t SERVO_BAUD = 1000000;
static const uint32_t SERVO_REPLY_US = 6000;

static const float M_N = 4095.0f;
static const float M_A = 360.0f;
// Present speed: bit 15 is direction, bits 0-14 are magnitude, 50 steps/s per unit.
static const float SERVO_SPEED_UNIT = 50.0f;
static const uint16_t POS_MIN = 0;
static const uint16_t POS_MAX = 4095;
static const uint16_t CAL_CENTER_POS = 2047;

static const uint8_t ADDR_GOAL_POS = 0x2A;
static const uint8_t ADDR_PRESENT = 0x38;
static const uint8_t PRESENT_LEN = 6;
static const uint8_t ADDR_TORQUE = 40;
static const uint8_t ADDR_TEMP_KP = 50;
static const uint8_t ADDR_TEMP_KD = 51;
static const uint8_t ADDR_PERM_KP_KD = 0x15;
static const uint8_t ADDR_UNLOCK = 0x37;
static const uint8_t ADDR_ID = 0x05;

static const uint8_t TEMP_KP_RUN = 6;
static const uint8_t TEMP_KD_RUN = 20;
static const uint8_t TEMP_KP_MOUTH = 10;
static const uint8_t MOUTH_MOTOR_ID = 34;
static const uint8_t TEMP_KP_CAL = 3;
static const uint8_t TEMP_KD_CAL = 0;

static const uint8_t MOTOR_IDS[NUM_MOTORS] = {
    10, 11, 12, 13, 14,
    20, 21, 22, 23, 24,
    30, 31, 32, 33, 34};

static const uint16_t ZERO_POS[NUM_MOTORS] = {
    2236, 2005, 1912, 1866, 1945,
    2127, 2135, 1914, 2228, 2206,
    2550, 2060, 2128, 2034, 2190};

// UNO Q hardware direction: all 15 motors are negative.
static const float MOTOR_SIGN[NUM_MOTORS] = {
    -1, -1, -1, -1, -1,
    -1, -1, -1, -1, -1,
    -1, -1, -1, -1, -1};

static const float DEFAULT_DEG[NUM_MOTORS] = {
    0.0f, -5.0f, -24.0f, 0.0f, 24.0f,
    0.0f, 5.0f, 24.0f, 0.0f, -24.0f,
    20.0f, 20.0f, 0.0f, 0.0f, 0.0f};

struct Motor {
  uint8_t id;
  uint16_t zeroPos;
  uint16_t fbPos;
  float sign;
  float desAngleDeg;
  uint16_t desPos;
  float fbAngleDeg;
  float fbVelDeg;
};

extern Motor motors[NUM_MOTORS];
