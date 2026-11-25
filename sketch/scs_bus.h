#pragma once

#include <string.h>
#include "duck_config.h"
#include "protocol.h"

extern struct k_mutex motorsMu;
static uint16_t servoRxMask = 0;
static ServoReplyDetail servoReplyDetail = {};
static bool collectingStateReplies = false, abnormalStateReply = false;

static void beginStateRead() {
  servoRxMask = 0;
  servoReplyDetail = {};
  abnormalStateReply = false;
  collectingStateReplies = true;
}
static uint8_t servoVoltage[NUM_MOTORS] = {};
static uint16_t servoPdMask = 0;
static uint8_t servoBootP[NUM_MOTORS] = {}, servoBootD[NUM_MOTORS] = {};
static uint8_t servoParserFlag = 0;
static uint8_t servoRxBuffer[32];
static uint8_t servoRxLen = 0;
static uint8_t servoRxDataLen = 0;
// Only the hardware task accesses these per-request results. 0xFF means idle;
// ID 0 is a valid servo address, just like IDs 1..253.
static uint8_t servoCalAckId = 0xFF;
static bool servoCalAckSeen = false, servoCalAckOk = false;
static uint8_t servoReadId = 0xFF;
static bool servoReadSeen = false;
static uint16_t servoReadPos = 0;
static uint8_t servoReadAlarm = 0;
static uint8_t servoByteReadId = 0xFF, servoByteValue = 0;
static bool servoByteSeen = false;
static uint8_t servoParamsId = 0xFF;
static bool servoParamsSeen = false;
static uint8_t servoParamKp = 0, servoParamKd = 0;
static constexpr uint8_t SERVO_PROTECTION_REG_LEN = ADDR_PROTECTION_CURRENT - ADDR_UNLOAD_CONDITION + 2;
static uint8_t servoProtectionId = 0xFF;
static bool servoProtectionSeen = false;
static uint8_t servoProtectionFlags = 0;
static uint16_t servoProtectionCurrent = 0;

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

static void scsWriteBuf(uint8_t id, uint8_t fun, const uint8_t *nDat, uint8_t nLen,
                        bool prefixFF = false) {
  uint8_t wire[1 + 8 + NUM_MOTORS * 7];
  wire[0] = 0xFF;
  uint8_t *buf = wire + 1;
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
  Serial1.write(prefixFF ? wire : buf, nLen + 6 + prefixFF);
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

static void servoSetKpKd(uint8_t id, uint8_t kp, uint8_t kd) {
  const uint8_t data[3] = {ADDR_PERM_KP_KD, kp, kd};
  servoUnlock(id);
  for (unsigned i = 0; i < 3; ++i) {
    scsWriteBuf(id, 0x03, data, 3);
    Serial1.flush();
    k_msleep(10); // Let the final EEPROM write finish before applying live gains.
  }
  const uint8_t live[3] = {ADDR_TEMP_KP, kp, kd};
  scsWriteBuf(id, 0x03, live, 3);
  Serial1.flush();
}

static void servoSetProtection(uint8_t id, uint8_t flags) {
  // Only change the unload conditions; leave the current threshold untouched.
  servoUnlock(id);
  for (unsigned i = 0; i < 3; ++i) {
    scsWriteByte(id, ADDR_UNLOAD_CONDITION, flags);
    Serial1.flush();
    if (i < 2) k_msleep(10);
  }
}

static void servoSetProtectionCurrent(uint8_t id, uint16_t current) {
  // Only change the current threshold; preserve the enabled protection bits.
  servoUnlock(id);
  for (unsigned i = 0; i < 3; ++i) {
    scsWriteWord(id, ADDR_PROTECTION_CURRENT, current);
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

static void applyMotorFb(uint8_t id, int16_t pos, int32_t vel, uint8_t voltageDeciV = 0) {
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
  if (voltageDeciV) {
    motors[idx].fbVoltageDeciV = voltageDeciV;
    motors[idx].fbVoltageUs = micros();
  }
  k_mutex_unlock(&motorsMu);
}

static uint32_t servoBusVoltageMv() {
  uint8_t sorted[NUM_MOTORS];
  unsigned count = 0;
  for (unsigned i = 0; i < NUM_MOTORS; ++i) {
    const uint8_t value = servoVoltage[i];
    if (!value || value == 0xff) continue;
    unsigned j = count++;
    for (; j > 0 && sorted[j - 1] > value; --j) sorted[j] = sorted[j - 1];
    sorted[j] = value;
  }
  return count ? 100u * sorted[(count - 1) / 2] : 0;
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
        // 0xFF is reserved. Stay synchronized across a prefixed request echo.
        if (res == 0xFF) break;
        servoRxBuffer[2] = res;
        servoParserFlag = 3;
        break;
      case 3:
        servoRxBuffer[3] = res;
        if (res == 0x02 || res == 0x04 || res == 0x08 || res == 0x09 || res == 0x0B ||
            (res == 3 && servoByteReadId <= 253) ||
            (collectingStateReplies && motorIndexById(servoRxBuffer[2]) >= 0 &&
             res >= 2 && res <= sizeof(servoRxBuffer) - 4) ||
            (res == SERVO_PROTECTION_REG_LEN + 2 && servoProtectionId <= 253)) {
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
          const uint8_t sid = servoRxBuffer[2], dataLen = servoRxDataLen;
          const int idx = motorIndexById(sid);
          // Look only inside the synchronous position read. A servo can return
          // a short error-only status in place of its normal register data.
          const bool stateReply = collectingStateReplies && idx >= 0;
          if (check != servoRxBuffer[3 + servoRxDataLen]) {
            if (stateReply) {
              servoReplyDetail.badChecksumMask |= 1u << idx;
              abnormalStateReply = true;
            }
            break;
          }
          if (stateReply && servoRxBuffer[4] != 0) {
            servoReplyDetail.error[idx] = servoRxBuffer[4];
            abnormalStateReply = true;
          }
          if (stateReply && dataLen != 8 && dataLen != 9) {
            servoReplyDetail.unexpectedLengthMask |= 1u << idx;
            abnormalStateReply = true;
          }
          if (dataLen == 2 && servoCalAckId <= 253 && sid == servoCalAckId) {
            servoCalAckSeen = true;
            servoCalAckOk = servoRxBuffer[4] == 0;
            break;
          }
          if (dataLen == 3) {
            if (servoByteReadId <= 253 && sid == servoByteReadId && servoRxBuffer[4] == 0) {
              servoByteValue = servoRxBuffer[5];
              servoByteSeen = true;
            }
            break;
          }
          if (dataLen == SERVO_PROTECTION_REG_LEN + 2) {
            if (sid == servoProtectionId && servoRxBuffer[4] == 0) {
              const uint8_t offset = 5 + ADDR_PROTECTION_CURRENT - ADDR_UNLOAD_CONDITION;
              servoProtectionFlags = servoRxBuffer[5];
              servoProtectionCurrent = (uint16_t)hostWord(servoRxBuffer[offset], servoRxBuffer[offset + 1]);
              servoProtectionSeen = true;
            }
            break;
          }
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
          // Six status bytes give motion feedback; the seventh adds voltage.
          if (dataLen != 8 && dataLen != 9) break;
          if (dataLen == 9 && idx >= 0)
            servoVoltage[idx] = servoRxBuffer[5 + ADDR_VOLTAGE - ADDR_PRESENT];
          const uint16_t pos = (uint16_t)hostWord(servoRxBuffer[5], servoRxBuffer[6]);
          if (pos > POS_MAX) {
            if (stateReply) {
              servoReplyDetail.invalidPositionMask |= 1u << idx;
              abnormalStateReply = true;
            }
            break;
          }
          if (servoReadId <= 253 && sid == servoReadId) {
            servoReadSeen = true;
            servoReadPos = pos;
            servoReadAlarm = servoRxBuffer[4];
          }
          applyMotorFb(sid, pos,
                       hostVelSignMag(servoRxBuffer[7], servoRxBuffer[8]),
                       dataLen == 9 ? servoRxBuffer[5 + ADDR_VOLTAGE - ADDR_PRESENT] : 0);
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

static bool servoCalibrateChecked(uint8_t id, bool parameterized, uint16_t position) {
  drainServoRx();
  servoCalAckId = id;
  servoCalAckSeen = servoCalAckOk = false;
  if (parameterized) {
    uint8_t data[2];
    packWordLE(data, position);
    scsWriteBuf(id, 0x0B, data, sizeof(data));
    Serial1.flush();
  } else {
    servoMid(id);
  }
  const uint32_t started = micros();
  while (!servoCalAckSeen && (uint32_t)(micros() - started) < SERVO_REPLY_US) {
    parseServoRx();
    if (!servoCalAckSeen) k_sleep(K_USEC(100));
  }
  parseServoRx();
  servoCalAckId = 0xFF;
  return servoCalAckSeen && servoCalAckOk;
}

// Some HD-1910s miss a read after idle unless the header has a leading 0xFF.
// Keep a standard-header fallback for servos that require exactly two 0xFFs.
static constexpr unsigned SETUP_READ_ATTEMPTS = 10;
static void readRegister(uint8_t id, uint8_t address, uint8_t length,
                         bool &seen, unsigned attempts) {
  drainServoRx();
  seen = false;
  const uint8_t data[] = {address, length};
  for (unsigned attempt = 0; attempt < attempts; ++attempt) {
    const bool prefixFF = attempt + 1 < attempts;
    scsWriteBuf(id, 0x02, data, sizeof(data), prefixFF);
    Serial1.flush();
    if (attempt + 1 == attempts) {
      // Includes ordinary single-shot reads. Allow their full response window.
      waitServoReply();
      return;
    }
    const uint32_t start = micros();
    do {
      parseServoRx();
      if (seen) {
        // A delayed reply to an earlier copy must not leak into the next register read.
        if (attempt > 0) waitServoReply();
        return;
      }
      k_sleep(K_USEC(100));
    } while ((uint32_t)(micros() - start) < 1500);
  }
}

static bool readServoPresent(uint8_t id, uint16_t &pos, bool requireNoAlarm = false,
                             unsigned attempts = 1) {
  servoReadId = id;
  servoReadPos = 0;
  servoReadAlarm = 0;
  readRegister(id, ADDR_PRESENT, PRESENT_LEN, servoReadSeen, attempts);
  pos = servoReadPos;
  servoReadId = 0xFF;
  return servoReadSeen && (!requireNoAlarm || servoReadAlarm == 0);
}

static bool readServoByte(uint8_t id, uint8_t address, uint8_t &value, unsigned attempts = 1) {
  servoByteReadId = id;
  readRegister(id, address, 1, servoByteSeen, attempts);
  servoByteReadId = 0xFF;
  if (!servoByteSeen) return false;
  value = servoByteValue;
  return true;
}

static bool pingServo(uint8_t id) {
  // A half-duplex bus may echo the query. A real ID read returns one data byte.
  uint8_t reportedId = 0;
  return id <= 253 && readServoByte(id, ADDR_ID, reportedId, SETUP_READ_ATTEMPTS) && reportedId == id;
}

static bool readServoTorque(uint8_t id, uint8_t &torque) {
  return readServoByte(id, ADDR_TORQUE, torque);
}

static bool readServoGains(uint8_t id, uint8_t &kp, uint8_t &kd,
                           uint8_t address = ADDR_PERM_KP_KD, unsigned attempts = 1) {
  servoParamsId = id;
  readRegister(id, address, 2, servoParamsSeen, attempts);
  kp = servoParamKp; kd = servoParamKd;
  servoParamsId = 0xFF;
  return servoParamsSeen;
}

static bool readServoProtection(uint8_t id, uint8_t &flags, uint16_t &current, unsigned attempts = 1) {
  servoProtectionId = id;
  readRegister(id, ADDR_UNLOAD_CONDITION, SERVO_PROTECTION_REG_LEN, servoProtectionSeen, attempts);
  servoProtectionId = 0xFF;
  if (!servoProtectionSeen) return false;
  flags = servoProtectionFlags;
  current = servoProtectionCurrent;
  return true;
}

static bool initServoIdProtection(uint8_t id, uint8_t &flags, uint16_t &current) {
  constexpr uint16_t current2A = 308; // Nearest register value at 6.5 mA per count.
  if (id > 253 || !readServoProtection(id, flags, current, SETUP_READ_ATTEMPTS)) return false;
  const uint8_t wanted = flags | PROTECTION_VOLTAGE | PROTECTION_OVERCURRENT;
  if (current != current2A) {
    servoSetProtectionCurrent(id, current2A);
    k_msleep(10);
    if (!readServoProtection(id, flags, current, SETUP_READ_ATTEMPTS) || current != current2A) return false;
  }
  if (flags != wanted) {
    servoSetProtection(id, wanted);
    k_msleep(10);
    if (!readServoProtection(id, flags, current, SETUP_READ_ATTEMPTS)) return false;
  }
  return flags == wanted && current == current2A;
}
