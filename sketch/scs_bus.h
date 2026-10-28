#pragma once

#include <string.h>
#include "duck_config.h"

extern struct k_mutex motorsMu;
static uint16_t servoRxMask = 0;
static uint16_t servoPdMask = 0;
static uint8_t servoBootP[NUM_MOTORS] = {}, servoBootD[NUM_MOTORS] = {};
static uint8_t servoParserFlag = 0;
static uint8_t servoRxBuffer[32];
static uint8_t servoRxLen = 0;
static uint8_t servoRxDataLen = 0;
// Only the hardware task accesses these per-request results. 0xFF means idle;
// ID 0 is a valid servo address, just like IDs 1..253.
static uint8_t servoPingId = 0xFF;
static bool servoPingSeen = false;
static uint8_t servoReadId = 0xFF;
static bool servoReadSeen = false;
static uint16_t servoReadPos = 0;
static uint8_t servoParamsId = 0xFF;
static bool servoParamsSeen = false;
static uint8_t servoParamKp = 0, servoParamKd = 0;

static int motorIndexById(uint8_t id) {
  for (uint8_t i = 0; i < NUM_MOTORS; i++) {
    if (motors[i].id == id) {
      return i;
    }
  }
  return -1;
}

static void packWordLE(uint8_t *p, uint16_t v) {
  p[0] = (uint8_t)(v & 0xFF);
  p[1] = (uint8_t)(v >> 8);
}

static int16_t hostWord(uint8_t lo, uint8_t hi) {
  return (int16_t)(((uint16_t)hi << 8) | lo);
}

static int32_t hostVelSignMag(uint8_t lo, uint8_t hi) {
  /* Bit 15 is a direction flag, not a two's-complement sign. */
  uint16_t raw = (uint16_t)lo | ((uint16_t)hi << 8);
  int32_t speed = (int32_t)(raw & 0x7FFFu);
  if (raw & 0x8000u) {
    speed = -speed;
  }
  return speed;
}

static uint16_t satPos(int32_t v) {
  if (v < (int32_t)POS_MIN) {
    return POS_MIN;
  }
  if (v > (int32_t)POS_MAX) {
    return POS_MAX;
  }
  return (uint16_t)v;
}

static void scsWriteBuf(uint8_t id, uint8_t fun, const uint8_t *nDat, uint8_t nLen) {
  uint8_t buf[8 + NUM_MOTORS * 7];
  uint8_t check = 0;
  buf[0] = 0xFF;
  buf[1] = 0xFF;
  buf[2] = id;
  buf[3] = nLen + 2;
  buf[4] = fun;
  for (uint8_t i = 0; i < nLen; i++) {
    buf[5 + i] = nDat[i];
    check = (uint8_t)(check + nDat[i]);
  }
  check = (uint8_t)(check + buf[2] + buf[3] + buf[4]);
  buf[nLen + 5] = (uint8_t)~check;
  Serial1.write(buf, nLen + 6);
}

static void scsWriteByte(uint8_t id, uint8_t addr, uint8_t value) {
  const uint8_t data[2] = {addr, value};
  scsWriteBuf(id, 0x03, data, 2);
}

static void scsWriteWord(uint8_t id, uint8_t addr, uint16_t value) {
  uint8_t data[3] = {addr, 0, 0};
  packWordLE(data + 1, value);
  scsWriteBuf(id, 0x03, data, 3);
}

static void setTorqueEnable(bool enabled) {
  scsWriteByte(0xFE, ADDR_TORQUE, enabled ? 1 : 0);
  Serial1.flush();
}

static void setTempKpKd(uint8_t kp, uint8_t kd) {
  scsWriteByte(0xFE, ADDR_TEMP_KP, kp);
  Serial1.flush();
  scsWriteByte(0xFE, ADDR_TEMP_KD, kd);
  Serial1.flush();
}

static void setRunKpKd() {
  setTempKpKd(TEMP_KP_RUN, TEMP_KD_RUN);
  scsWriteByte(MOUTH_MOTOR_ID, ADDR_TEMP_KP, TEMP_KP_MOUTH);
  Serial1.flush();
}

static void syncWriteGoalsFromDesPos() {
  uint8_t order[NUM_MOTORS * 7];
  for (uint8_t i = 0; i < NUM_MOTORS; i++) {
    motors[i].desPos = satPos(motors[i].desPos);
    order[i * 7] = motors[i].id;
    packWordLE(order + i * 7 + 1, motors[i].desPos);
    packWordLE(order + i * 7 + 3, 0);
    packWordLE(order + i * 7 + 5, 0);
  }
  uint8_t sync[2 + NUM_MOTORS * 7];
  sync[0] = ADDR_GOAL_POS;
  sync[1] = 6;
  memcpy(sync + 2, order, NUM_MOTORS * 7);
  scsWriteBuf(0xFE, 0x83, sync, (uint8_t)(NUM_MOTORS * 7 + 2));
}

static void sendMotorDesPos() {
  for (uint8_t i = 0; i < NUM_MOTORS; i++) {
    int32_t pos = (int32_t)SERVO_CENTER_POS +
                  (int32_t)(motors[i].desAngleDeg / M_A * M_N * motors[i].sign);
    motors[i].desPos = satPos(pos);
  }
  syncWriteGoalsFromDesPos();
}

static void writeSingleGoalRaw(uint8_t id, uint16_t raw) {
  uint8_t data[7];
  data[0] = ADDR_GOAL_POS;
  packWordLE(data + 1, satPos(raw));
  packWordLE(data + 3, 0);
  packWordLE(data + 5, 0);
  scsWriteBuf(id, 0x03, data, 7);
}

static void servoMid(uint8_t id) {
  // Same six-byte command as the servo debug mid.py program.
  scsWriteBuf(id, 0x0B, nullptr, 0);
  Serial1.flush();
}

static void servoUnlock(uint8_t id) {
  for (unsigned i = 0; i < 3; ++i) {
    scsWriteByte(id, ADDR_UNLOCK, 0x00);
    Serial1.flush();
    k_msleep(10);
  }
}

static void servoSetId(uint8_t id, uint8_t newId) {
  servoUnlock(id);
  for (unsigned i = 0; i < 3; ++i) {
    scsWriteByte(id, ADDR_ID, newId);
    Serial1.flush();
    if (i < 2) k_msleep(10);
  }
}

static void servoSetPermKpKd(uint8_t id, uint8_t kp, uint8_t kd) {
  const uint8_t data[3] = {ADDR_PERM_KP_KD, kp, kd};
  servoUnlock(id);
  for (unsigned i = 0; i < 3; ++i) {
    scsWriteBuf(id, 0x03, data, 3);
    Serial1.flush();
    if (i < 2) k_msleep(10);
  }
}

static void servoSetProtection(uint8_t flags) {
  // Only change the unload conditions; leave the current threshold untouched.
  servoUnlock(0xFE);
  for (unsigned i = 0; i < 3; ++i) {
    scsWriteByte(0xFE, ADDR_UNLOAD_CONDITION, flags);
    Serial1.flush();
    if (i < 2) k_msleep(10);
  }
}

static void servoSetProtectionCurrent(uint16_t current) {
  // Only change the current threshold; preserve the enabled protection bits.
  servoUnlock(0xFE);
  for (unsigned i = 0; i < 3; ++i) {
    scsWriteWord(0xFE, ADDR_PROTECTION_CURRENT, current);
    Serial1.flush();
    if (i < 2) k_msleep(10);
  }
}

static void readAllRegisters(uint8_t address, uint8_t length) {
  uint8_t buf[8 + NUM_MOTORS];
  uint8_t check = 0;
  buf[0] = 0xFF;
  buf[1] = 0xFF;
  buf[2] = 0xFE;
  buf[3] = NUM_MOTORS + 4;
  buf[4] = 0x82;
  buf[5] = address;
  buf[6] = length;
  for (uint8_t i = 0; i < NUM_MOTORS; i++) {
    buf[7 + i] = motors[i].id;
    check = (uint8_t)(check + motors[i].id);
  }
  check = (uint8_t)(check + buf[2] + buf[3] + buf[4] + buf[5] + buf[6]);
  buf[7 + NUM_MOTORS] = (uint8_t)~check;
  Serial1.write(buf, (uint8_t)(8 + NUM_MOTORS));
}

static void readAllState() { readAllRegisters(ADDR_PRESENT, PRESENT_LEN); }

static void readSinglePresent(uint8_t id) {
  const uint8_t data[2] = {ADDR_PRESENT, PRESENT_LEN};
  scsWriteBuf(id, 0x02, data, 2);
}

static void applyMotorFb(uint8_t id, int16_t pos, int32_t vel) {
  int idx = motorIndexById(id);
  if (idx < 0) {
    return;
  }
  servoRxMask |= (1u << idx);
  k_mutex_lock(&motorsMu, K_FOREVER);
  motors[idx].fbPos = (uint16_t)pos;
  motors[idx].fbAngleDeg =
      (float)(pos - SERVO_CENTER_POS) * M_A / M_N * motors[idx].sign;
  motors[idx].fbVelDeg =
      (float)vel * SERVO_SPEED_UNIT * (360.0f / 4096.0f) * motors[idx].sign;
  k_mutex_unlock(&motorsMu);
}

static void resetServoParser() {
  servoParserFlag = 0;
  servoRxLen = 0;
  servoRxDataLen = 0;
}

static void drainServoRx() {
  while (Serial1.available()) {
    (void)Serial1.read();
  }
  resetServoParser();
}

static void parseServoRx() {
  while (Serial1.available()) {
    uint8_t res = (uint8_t)Serial1.read();
    switch (servoParserFlag) {
      case 0:
        if (res == 0xFF) {
          servoParserFlag = 1;
          servoRxBuffer[0] = 0xFF;
        }
        break;
      case 1:
        if (res == 0xFF) {
          servoParserFlag = 2;
          servoRxBuffer[1] = 0xFF;
        } else {
          servoParserFlag = 0;
        }
        break;
      case 2:
        servoRxBuffer[2] = res;
        servoParserFlag = 3;
        break;
      case 3:
        servoRxBuffer[3] = res;
        if (res == 0x02 || res == 0x04 || res == 0x08 || res == 0x09 || res == 0x0B) {
          servoParserFlag = 4;
          servoRxLen = 0;
          servoRxDataLen = res;
        } else {
          servoParserFlag = 0;
        }
        break;
      case 4:
        if (4 + servoRxLen >= sizeof(servoRxBuffer)) {
          servoParserFlag = 0;
          break;
        }
        servoRxBuffer[4 + servoRxLen] = res;
        servoRxLen++;
        if (servoRxLen == servoRxDataLen) {
          uint8_t check = 0;
          servoParserFlag = 0;
          for (uint8_t i = 0; i < (uint8_t)(1 + servoRxDataLen); i++) {
            check = (uint8_t)(check + servoRxBuffer[2 + i]);
          }
          check = (uint8_t)~check;
          if (check != servoRxBuffer[3 + servoRxDataLen]) {
            break;
          }
          uint8_t sid = servoRxBuffer[2];
          uint8_t dataLen = servoRxDataLen;
          if (dataLen == 2 && servoPingId <= 253 && sid == servoPingId) {
            // Even a device error is evidence that this address responded.
            servoPingSeen = true;
            break;
          }
          const int idx = motorIndexById(sid);
          if (dataLen == 4 && servoRxBuffer[4] == 0) {
            if (servoParamsId <= 253 && sid == servoParamsId) {
              servoParamKp = servoRxBuffer[5]; servoParamKd = servoRxBuffer[6];
              servoParamsSeen = true;
            } else if (servoParamsId == 0xFF && idx >= 0) {
              servoBootP[idx]=servoRxBuffer[5]; servoBootD[idx]=servoRxBuffer[6];
              servoPdMask |= 1u << idx;
            }
            break;
          }
          // Only accept the requested 6-byte status, with no device error.
          if (dataLen != 8 || servoRxBuffer[4] != 0) {
            break;
          }
          if (servoReadId <= 253 && sid == servoReadId) {
            servoReadSeen = true;
            servoReadPos = (uint16_t)hostWord(servoRxBuffer[5], servoRxBuffer[6]);
          }
          applyMotorFb(sid, hostWord(servoRxBuffer[5], servoRxBuffer[6]),
                       hostVelSignMag(servoRxBuffer[7], servoRxBuffer[8]));
        }
        break;
      default:
        servoParserFlag = 0;
        break;
    }
  }
}

static void waitServoReply() {
  const uint32_t start = micros();
  while ((uint32_t)(micros() - start) < SERVO_REPLY_US) {
    parseServoRx();
    k_sleep(K_USEC(100));
  }
  parseServoRx();
}

static bool pingServo(uint8_t id) {
  drainServoRx();
  servoPingId = id;
  servoPingSeen = false;
  scsWriteBuf(id, 0x01, nullptr, 0);
  Serial1.flush();
  waitServoReply();
  servoPingId = 0xFF;
  return servoPingSeen;
}

static bool readServoPresent(uint8_t id, uint16_t &pos) {
  drainServoRx();
  servoReadId = id;
  servoReadSeen = false;
  servoReadPos = 0;
  readSinglePresent(id);
  Serial1.flush();
  waitServoReply();
  pos = servoReadPos;
  servoReadId = 0xFF;
  return servoReadSeen;
}

static bool readServoGains(uint8_t id, uint8_t &kp, uint8_t &kd) {
  drainServoRx();
  servoParamsId = id;
  servoParamsSeen = false;
  const uint8_t data[2] = {ADDR_PERM_KP_KD, 2};
  scsWriteBuf(id, 0x02, data, 2);
  Serial1.flush();
  waitServoReply();
  kp = servoParamKp; kd = servoParamKd;
  servoParamsId = 0xFF;
  return servoParamsSeen;
}
