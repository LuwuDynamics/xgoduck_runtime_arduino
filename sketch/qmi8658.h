#pragma once

#include <Wire.h>

// QMI8658 on Wire (D20/SDA, D21/SCL). Probe 0x6A, then 0x6B. WHO_AM_I is 0x05.
// Mounting axes are applied below. Do not log on the Bridge UART.

static const uint8_t QMI8658C_REG_WHO_AM_I = 0x00;
static const uint8_t QMI8658C_WHO_AM_I_VALUE = 0x05;
static const uint8_t QMI8658C_REG_CTRL1 = 0x02;
static const uint8_t QMI8658C_REG_CTRL2 = 0x03;
static const uint8_t QMI8658C_REG_CTRL3 = 0x04;
static const uint8_t QMI8658C_REG_CTRL5 = 0x06;
static const uint8_t QMI8658C_REG_CTRL7 = 0x08;
static const uint8_t QMI8658C_REG_ACC_X_L = 0x35;
static const uint8_t QMI8658C_I2C_ADDRS[2] = {0x6A, 0x6B};

static uint8_t qmiAddr = 0;

struct ImuSample {
  uint8_t ok;
  int16_t accRaw[3];
  int16_t gyroRaw[3];
};

static ImuSample imu = {};

static bool qmiWrite(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(qmiAddr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

static bool qmiRead(uint8_t reg, uint8_t *buf, uint8_t len) {
  Wire.beginTransmission(qmiAddr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) {
    return false;
  }
  uint8_t got = (uint8_t)Wire.requestFrom((int)qmiAddr, (int)len);
  if (got != len) {
    return false;
  }
  for (uint8_t i = 0; i < len; i++) {
    buf[i] = (uint8_t)Wire.read();
  }
  return true;
}

static bool initImu() {
  Wire.begin();
  Wire.setClock(400000);
  delay(20);

  bool found = false;
  for (uint8_t i = 0; i < 2; i++) {
    uint8_t who = 0;
    Wire.beginTransmission(QMI8658C_I2C_ADDRS[i]);
    Wire.write(QMI8658C_REG_WHO_AM_I);
    if (Wire.endTransmission(false) != 0 ||
        Wire.requestFrom((int)QMI8658C_I2C_ADDRS[i], 1) != 1) {
      continue;
    }
    who = (uint8_t)Wire.read();
    if (who == QMI8658C_WHO_AM_I_VALUE) {
      qmiAddr = QMI8658C_I2C_ADDRS[i];
      found = true;
      break;
    }
  }

  imu.ok = 0;
  if (!found) {
    qmiAddr = 0;
    return false;
  }

  delay(10);
  // Reset persistent configuration when restarting the MCU without power cycling IMU.
  if (!qmiWrite(0x60, 0xB0)) { qmiAddr=0; return false; }
  delay(20);
  uint8_t resetResult=0;
  if (!qmiRead(0x4D, &resetResult, 1) || resetResult!=0x80) {
    qmiAddr=0; return false;
  }
  // CTRL1 enable, CTRL7 accel+gyro, CTRL2 ±2 g, CTRL3 ±1024 dps (32 LSB/dps), ODR code 4, CTRL5 LPF off.
  if (!qmiWrite(QMI8658C_REG_CTRL7, 0x00) || !qmiWrite(QMI8658C_REG_CTRL1, 0x40) ||
      !qmiWrite(QMI8658C_REG_CTRL2, 0x04) || !qmiWrite(QMI8658C_REG_CTRL3, 0x64) ||
      !qmiWrite(QMI8658C_REG_CTRL5, 0x00) || !qmiWrite(QMI8658C_REG_CTRL7, 0x03)) {
    qmiAddr = 0;
    return false;
  }
  delay(250);
  uint8_t config[7];
  if (!qmiRead(QMI8658C_REG_CTRL1, config, sizeof(config)) || config[0]!=0x40 ||
      config[1]!=0x04 || config[2]!=0x64 || config[4]!=0x00 || config[6]!=0x03) {
    qmiAddr=0; return false;
  }
  imu.ok = 1;
  return true;
}

static bool updateImu() {
  if (qmiAddr == 0) {
    static uint32_t lastProbeMs = 0;
    const uint32_t now = millis();
    if ((uint32_t)(now - lastProbeMs) >= 1000u) {
      lastProbeMs = now;
      initImu();
    }
    return false;
  }

  uint8_t data[12] = {0};
  if (!qmiRead(QMI8658C_REG_ACC_X_L, data, 12)) {
    imu.ok = 0;
    return false;
  }

  int16_t ax_raw = (int16_t)(data[1] << 8 | data[0]);
  int16_t ay_raw = (int16_t)(data[3] << 8 | data[2]);
  int16_t az_raw = (int16_t)(data[5] << 8 | data[4]);
  int16_t gx_raw = (int16_t)(data[7] << 8 | data[6]);
  int16_t gy_raw = (int16_t)(data[9] << 8 | data[8]);
  int16_t gz_raw = (int16_t)(data[11] << 8 | data[10]);

  int16_t accx = az_raw;
  int16_t accy = ax_raw;
  int16_t accz = (int16_t)(-ay_raw);
  int16_t gyrox = (int16_t)(-gz_raw);
  int16_t gyroy = (int16_t)(-gx_raw);
  int16_t gyroz = gy_raw;

  imu.ok = 1;
  imu.accRaw[0] = accx;
  imu.accRaw[1] = accy;
  imu.accRaw[2] = accz;
  imu.gyroRaw[0] = gyrox;
  imu.gyroRaw[1] = gyroy;
  imu.gyroRaw[2] = gyroz;

  return true;
}
