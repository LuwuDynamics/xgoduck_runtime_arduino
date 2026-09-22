#pragma once
#include <stdint.h>

constexpr uint32_t BRIDGE_BAUD = 2000000;
constexpr uint32_t TICK_US = 10000;
constexpr uint32_t COMMAND_TTL_US = 250000;
constexpr uint32_t MAX_SOURCE_AGE_US = 150000;
enum CommandMode : uint8_t { DISARM = 0, ARM = 1, POLICY = 2, HOLD = 3 };

enum HostBusMode : uint8_t { HOST_NORMAL = 0, HOST_CAL = 1, HOST_SERVO_DEBUG = 2 };

enum CalOp : uint8_t {
  CAL_ENTER = 0,
  CAL_EXIT = 1,
  CAL_SET_RAW = 2,
  CAL_FINISH_ONE = 3,
  CAL_SET_ZEROS = 4
};

enum ServoOp : uint8_t {
  SERVO_ENTER = 0,
  SERVO_EXIT = 1,
  SERVO_UNLOCK = 2,
  SERVO_SET_ID = 3,
  SERVO_GOTO = 4,
  SERVO_SET_PERM_KP_KD = 5,
  SERVO_READ = 6
};

// Little endian, binary payload inside MessagePack RPC. ABI shared with wire.py.
struct __attribute__((packed)) CommandPacket {
  char magic[4];
  uint32_t seq, sourceUs, ttlUs;
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
struct __attribute__((packed)) CalPacket {
  char magic[4];
  uint8_t op, index;
  uint16_t mask;
  uint16_t raw[15];
};
struct __attribute__((packed)) ServoPacket {
  char magic[4];
  uint8_t op, targetId, newId, kp, kd, reserved;
  uint16_t rawPos;
};
struct __attribute__((packed)) RawPacket {
  char magic[4];
  uint16_t mask;
  uint16_t pos[15];
  uint16_t zero[15];
};
struct __attribute__((packed)) ServoReplyPacket {
  char magic[4];
  uint8_t op, targetId, ok, reserved;
  uint16_t rawPos;
  uint16_t zeroPos;
};
static_assert(sizeof(CommandPacket) == 80, "Command ABI");
static_assert(sizeof(StatePacket) == 240, "State ABI");
static_assert(sizeof(CalPacket) == 38, "Cal ABI");
static_assert(sizeof(ServoPacket) == 12, "Servo ABI");
static_assert(sizeof(RawPacket) == 66, "Raw ABI");
static_assert(sizeof(ServoReplyPacket) == 12, "Servo reply ABI");
