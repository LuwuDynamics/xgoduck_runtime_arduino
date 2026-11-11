#pragma once
#include <stdint.h>

constexpr uint32_t BRIDGE_BAUD = 2000000;
constexpr uint32_t TICK_US = 10000;
constexpr uint32_t HOST_HEARTBEAT_US = 250000; // Calibration/debug modes only.
constexpr uint32_t IMU_STALE_US = 500000; // Mirrored by wire.py IMU_STALE_S.
constexpr uint32_t MAX_SOURCE_AGE_US = 150000;
constexpr uint32_t VOLTAGE_STALE_US = 2000000;
enum CommandMode : uint8_t { DISARM = 0, ARM = 1, POLICY = 2, HOLD = 3 };

enum HostBusMode : uint8_t { HOST_NORMAL = 0, HOST_CAL = 1, HOST_SERVO_DEBUG = 2 };

enum CalOp : uint8_t {
  CAL_ENTER = 0,
  CAL_EXIT = 1,
  CAL_CENTER_ONE = 2
};

enum ServoOp : uint8_t {
  SERVO_ENTER = 0,
  SERVO_EXIT = 1,
  SERVO_UNLOCK = 2,
  SERVO_SET_ID = 3,
  // 4 was the removed arbitrary-position operation.
  SERVO_SET_PERM_KP_KD = 5, // Legacy opcode: write and verify EEPROM + live KP/KD.
  SERVO_READ = 6,
  SERVO_PING = 7,
  SERVO_RESET_MID = 8,
  SERVO_SET_PROTECTION = 9,
  SERVO_SET_PROTECTION_CURRENT = 10,
  SERVO_APPLY_RUN_GAINS = 11, // Retired fixed profile; requests are rejected.
  SERVO_READ_RUN_GAINS = 12,
  SERVO_TEST_START = 13,
  SERVO_TEST_MOVE = 14,
  SERVO_TEST_STOP = 15,
  SERVO_READ_PROTECTION = 16,
  SERVO_SET_PROTECTION_SWITCH = 17
};
constexpr uint8_t SERVO_TEST_OFFSET_DEG = 30;
constexpr uint8_t SERVO_TEST_OFFSET_BIAS = 10; // Preserve the original move encoding.

constexpr int32_t servoTestOffsetDeg(uint8_t encoded) {
  return (encoded < 128 ? (int32_t)encoded : (int32_t)encoded - 256) - SERVO_TEST_OFFSET_BIAS;
}

// Little endian, binary payload inside MessagePack RPC. ABI shared with wire.py.
struct __attribute__((packed)) CommandPacket {
  char magic[4];
  uint32_t seq, sourceUs, ttlUs;
  // ttlUs is retained for wire compatibility; normal commands do not expire.
  uint8_t mode, reserved;
  uint16_t reserved2;
  float target[15];
};
struct __attribute__((packed)) StatePacket {
  char magic[4];
  uint8_t version, flags;
  uint16_t servoMask;
  uint32_t seq, sampleUs, imuUs, commandSeq, commandAgeUs;
  uint32_t periodUs, workUs, overruns, reportDrops, invalidCommands;
  int16_t acc[3], gyro[3];
  float angle[15], velocity[15], target[15];
};
struct __attribute__((packed)) DiagnosticPacket {
  char magic[4];
  uint32_t bridgeState, bridgeAttempts, bridgeFailures, notifyDrops;
  uint32_t imuSeq, imuUs, imuPeriodUs, imuWorkUs, imuOverruns, imuErrors, imuInitAttempts;
  uint32_t servoWrites, servoReads, servoWaitUs, reconnects, bridgeReadyUs;
  uint32_t servoBusVoltageMv;
};
struct __attribute__((packed)) VoltagePacket {
  char magic[4];
  uint32_t seq, sampleUs;
  uint16_t servoMask;
  uint8_t deciV[15];
};
struct __attribute__((packed)) CalPacket {
  char magic[4];
  uint32_t seq;
  uint8_t op, index;
};
struct __attribute__((packed)) CalReplyPacket {
  char magic[4];
  uint32_t seq;
  uint8_t index, ok;
};
struct __attribute__((packed)) ServoPacket {
  char magic[4];
  uint8_t op, targetId;
  union { uint8_t newId; uint8_t testOffset; uint8_t protectionMask; };
  uint8_t kp, kd;
  union { uint8_t reserved; uint8_t protectionFlags; };
  union { uint16_t reserved2; uint16_t protectionCurrent; uint16_t testSession; };
};
struct __attribute__((packed)) RawPacket {
  char magic[4];
  uint16_t mask, heldMask;
  uint16_t pos[15];
};
struct __attribute__((packed)) ServoReplyPacket {
  char magic[4];
  uint8_t op, targetId, ok;
  union { uint8_t reserved; uint8_t protectionFlags; uint8_t testFeedbackLimit; };
  union { uint16_t rawPos; uint16_t protectionCurrent; };
  union { uint16_t centerPos; uint16_t testSession; };
  union { uint8_t kp; uint8_t testOffsetLimitDeg; };
  union { uint8_t kd; uint8_t testIdleTimeoutSec; };
};
static_assert(sizeof(CommandPacket) == 80, "Command ABI");
static_assert(sizeof(StatePacket) == 240, "State ABI");
static_assert(sizeof(DiagnosticPacket) == 72, "Diagnostic ABI");
static_assert(sizeof(VoltagePacket) == 29, "Voltage ABI");
static_assert(sizeof(CalPacket) == 10, "Cal ABI");
static_assert(sizeof(CalReplyPacket) == 10, "Cal reply ABI");
static_assert(sizeof(ServoPacket) == 12, "Servo ABI");
static_assert(sizeof(RawPacket) == 38, "Raw ABI");
static_assert(sizeof(ServoReplyPacket) == 14, "Servo reply ABI");
