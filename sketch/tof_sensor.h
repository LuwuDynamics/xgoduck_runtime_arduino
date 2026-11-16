#pragma once

// Optional board Qwiic sensor. Wire1 is independent from the Wire IMU bus.
// No motor state, torque command or motion policy is changed by this task.
#include "src/vl53l5cx/SparkFun_VL53L5CX_Library.h"

struct TofPacket {
  char magic[4];
  uint8_t version, width, height, state, address, device, revision, reserved;
  uint32_t seq, sampleMs, ageMs, readUs, errors, lastError;
  int16_t distanceMm[64];
  uint8_t targetStatus[64], targetCount[64];
};
static_assert(sizeof(TofPacket) == 292, "ToF packet ABI");

enum TofState : uint8_t { TOF_STARTING, TOF_ABSENT, TOF_INITIALIZING,
                         TOF_RANGING, TOF_ERROR, TOF_OFFLINE };
static struct k_mutex tofMu;
static TofPacket tofSnapshot = {};
static SparkFun_VL53L5CX tofSensor;
static VL53L5CX_ResultsData tofResult;
K_THREAD_STACK_DEFINE(tofStack, 8192);
static struct k_thread tofThread;

static bool tofReadIdentity(uint8_t &device, uint8_t &revision) {
  Wire1.beginTransmission(0x29);
  Wire1.write((uint8_t)0x7f); Wire1.write((uint8_t)0xff); Wire1.write((uint8_t)0);
  if (Wire1.endTransmission() != 0) return false;
  Wire1.beginTransmission(0x29);
  Wire1.write((uint8_t)0); Wire1.write((uint8_t)0);
  if (Wire1.endTransmission(false) != 0 ||
      Wire1.requestFrom((uint8_t)0x29, (size_t)2) != 2) return false;
  device = Wire1.read(); revision = Wire1.read();
  Wire1.beginTransmission(0x29);
  Wire1.write((uint8_t)0x7f); Wire1.write((uint8_t)0xff); Wire1.write((uint8_t)2);
  return Wire1.endTransmission() == 0;
}

static void tofFail(uint32_t stage, bool disconnected = false) {
  k_mutex_lock(&tofMu, K_FOREVER);
  tofSnapshot.state = disconnected ? TOF_OFFLINE : TOF_ERROR;
  ++tofSnapshot.errors;
  tofSnapshot.lastError = (stage << 24) |
      ((uint32_t)tofSensor.lastError.lastErrorCode << 16) |
      (tofSensor.lastError.lastErrorValue & 0xffff);
  k_mutex_unlock(&tofMu);
}

static void tofTask(void*, void*, void*) {
  Wire1.begin();
  Wire1.setClock(400000);
  uint8_t device = 0, revision = 0;
  const bool present = tofReadIdentity(device, revision);
  k_mutex_lock(&tofMu, K_FOREVER);
  tofSnapshot.device = device; tofSnapshot.revision = revision;
  tofSnapshot.state = present ? TOF_INITIALIZING : TOF_ABSENT;
  k_mutex_unlock(&tofMu);
  if (!present) return;
  if (device != 0xf0 || revision != 0x02) { tofFail(1); return; }
  if (!tofSensor.begin(0x29, Wire1)) { tofFail(2); return; }
  if (!tofSensor.setResolution(64)) { tofFail(3); return; }
  if (!tofSensor.setRangingFrequency(5)) { tofFail(4); return; }
  if (tofSensor.getResolution() != 64 || tofSensor.getRangingFrequency() != 5) {
    tofFail(5); return;
  }
  if (!tofSensor.startRanging()) { tofFail(6); return; }
  uint32_t lastFrame = millis();
  for (;;) {
    if (tofSensor.isDataReady()) {
      const uint32_t start = micros();
      if (!tofSensor.getRangingData(&tofResult)) { tofFail(7, true); return; }
      const uint32_t readUs = micros() - start;
      lastFrame = millis();
      k_mutex_lock(&tofMu, K_FOREVER);
      tofSnapshot.state = TOF_RANGING;
      ++tofSnapshot.seq; tofSnapshot.sampleMs = lastFrame;
      tofSnapshot.readUs = readUs;
      memcpy(tofSnapshot.distanceMm, tofResult.distance_mm, sizeof(tofSnapshot.distanceMm));
      memcpy(tofSnapshot.targetStatus, tofResult.target_status, sizeof(tofSnapshot.targetStatus));
      memcpy(tofSnapshot.targetCount, tofResult.nb_target_detected, sizeof(tofSnapshot.targetCount));
      k_mutex_unlock(&tofMu);
    } else if (tofSensor.lastError.lastErrorCode != SF_VL53L5CX_ERROR_TYPE::NO_ERROR) {
      tofFail(8, true); return;
    }
    if ((uint32_t)(millis() - lastFrame) > 5000) { tofFail(9, true); return; }
    k_msleep(10);
  }
}

// The RPC only copies the latest snapshot. Sensor I/O never blocks Bridge.
static MsgPack::bin_t<uint8_t> readTof() {
  TofPacket data;
  k_mutex_lock(&tofMu, K_FOREVER);
  data = tofSnapshot;
  k_mutex_unlock(&tofMu);
  data.ageMs = data.seq ? (uint32_t)(millis() - data.sampleMs) : UINT32_MAX;
  MsgPack::bin_t<uint8_t> bytes;
  bytes.resize(sizeof(data));
  memcpy(bytes.data(), &data, sizeof(data));
  return bytes;
}

static void startTof() {
  k_mutex_init(&tofMu);
  memcpy(tofSnapshot.magic, "DTF1", 4);
  tofSnapshot.version = 1;
  tofSnapshot.width = tofSnapshot.height = 8;
  tofSnapshot.address = 0x29;
  k_thread_create(&tofThread, tofStack, K_THREAD_STACK_SIZEOF(tofStack),
                  tofTask, nullptr, nullptr, nullptr, 8, 0, K_NO_WAIT);
  k_thread_name_set(&tofThread, "duck_tof5");
}
