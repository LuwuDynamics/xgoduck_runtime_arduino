#include <Arduino_RouterBridge.h>
#include <zephyr/kernel.h>
#include <math.h>
#include <string.h>
#include "duck_config.h"
#include "protocol.h"
#include "scs_bus.h"
#include "qmi8658.h"
#include "uart_fifo.h"

Motor motors[NUM_MOTORS];
struct k_mutex motorsMu;
// App sketches are Zephyr loadable extensions: initialize kernel objects at runtime.
// Avoid iterable kernel-object sections in the loadable sketch.
static struct k_mutex commandMu, stateMu, hostMu;
static struct k_sem reportReady;
K_THREAD_STACK_DEFINE(controlStack, 4096);
K_THREAD_STACK_DEFINE(reportStack, 4096);
static struct k_thread controlThread, reportThread;
static StatePacket snapshot = {};
static CommandPacket latest = {};
static bool pending = false;
static uint32_t invalidCommands = 0, reportDrops = 0;

static HostBusMode hostMode = HOST_NORMAL;
static CalPacket pendingCal = {};
static bool calPending = false;
static ServoPacket pendingServo = {};
static bool servoPending = false;
static ServoReplyPacket servoReply = {};
static bool servoReplyReady = false;
static bool rawNotifyEveryTick = false;

static void receiveCommand(MsgPack::bin_t<uint8_t> bytes) {
  CommandPacket cmd;
  bool valid = bytes.size() == sizeof(cmd);
  if (valid) {
    memcpy(&cmd, bytes.data(), sizeof(cmd));
    valid = memcmp(cmd.magic, "DQC1", 4) == 0 && cmd.mode <= HOLD &&
            !cmd.reserved && !cmd.reserved2 &&
            cmd.ttlUs > 0 && cmd.ttlUs <= COMMAND_TTL_US;
    for (unsigned i = 0; i < NUM_MOTORS; ++i)
      valid = valid && isfinite(cmd.target[i]);
  }
  k_mutex_lock(&commandMu, K_FOREVER);
  if (valid) { latest = cmd; pending = true; }
  else ++invalidCommands;
  k_mutex_unlock(&commandMu);
}

static void receiveCal(MsgPack::bin_t<uint8_t> bytes) {
  CalPacket pkt;
  bool valid = bytes.size() == sizeof(pkt);
  if (valid) {
    memcpy(&pkt, bytes.data(), sizeof(pkt));
    valid = memcmp(pkt.magic, "DQL1", 4) == 0 && pkt.op <= CAL_SET_ZEROS;
    if (pkt.op == CAL_FINISH_ONE) valid = valid && pkt.index < NUM_MOTORS;
    if (pkt.op == CAL_SET_RAW || pkt.op == CAL_SET_ZEROS) {
      for (unsigned i = 0; i < NUM_MOTORS; ++i) {
        if (pkt.mask & (1u << i))
          valid = valid && pkt.raw[i] <= POS_MAX;
      }
    }
  }
  k_mutex_lock(&hostMu, K_FOREVER);
  if (valid) { pendingCal = pkt; calPending = true; }
  else {
    k_mutex_lock(&commandMu, K_FOREVER); ++invalidCommands; k_mutex_unlock(&commandMu);
  }
  k_mutex_unlock(&hostMu);
}

static void receiveServo(MsgPack::bin_t<uint8_t> bytes) {
  ServoPacket pkt;
  bool valid = bytes.size() == sizeof(pkt);
  if (valid) {
    memcpy(&pkt, bytes.data(), sizeof(pkt));
    valid = memcmp(pkt.magic, "DQV1", 4) == 0 && pkt.op <= SERVO_READ &&
            pkt.targetId != 0 && pkt.targetId != 0xFE;
    if (pkt.op == SERVO_GOTO) valid = valid && pkt.rawPos <= POS_MAX;
    if (pkt.op == SERVO_SET_ID) valid = valid && pkt.newId != 0 && pkt.newId != 0xFE;
  }
  k_mutex_lock(&hostMu, K_FOREVER);
  if (valid) { pendingServo = pkt; servoPending = true; }
  else {
    k_mutex_lock(&commandMu, K_FOREVER); ++invalidCommands; k_mutex_unlock(&commandMu);
  }
  k_mutex_unlock(&hostMu);
}

static void fillRawPacket(RawPacket &r) {
  memcpy(r.magic, "DQR1", 4);
  r.mask = servoRxMask;
  k_mutex_lock(&motorsMu, K_FOREVER);
  for (unsigned i = 0; i < NUM_MOTORS; ++i) {
    r.pos[i] = motors[i].fbPos;
    r.zero[i] = motors[i].zeroPos;
  }
  k_mutex_unlock(&motorsMu);
}

static void applyCalOp(const CalPacket &pkt) {
  switch (pkt.op) {
    case CAL_ENTER:
      hostMode = HOST_CAL;
      rawNotifyEveryTick = true;
      k_mutex_lock(&motorsMu, K_FOREVER);
      for (unsigned i = 0; i < NUM_MOTORS; ++i) motors[i].desPos = CAL_CENTER_POS;
      k_mutex_unlock(&motorsMu);
      setTempKpKd(TEMP_KP_CAL, TEMP_KD_CAL);
      setTorqueEnable(true);
      k_mutex_lock(&motorsMu, K_FOREVER);
      sendMotorRawPos();
      k_mutex_unlock(&motorsMu);
      Serial1.flush();
      break;
    case CAL_EXIT:
      hostMode = HOST_NORMAL;
      rawNotifyEveryTick = false;
      setTorqueEnable(false);
      setRunKpKd();
      break;
    case CAL_SET_RAW:
      if (hostMode != HOST_CAL) break;
      k_mutex_lock(&motorsMu, K_FOREVER);
      for (unsigned i = 0; i < NUM_MOTORS; ++i) {
        if (pkt.mask & (1u << i)) motors[i].desPos = satPos(pkt.raw[i]);
      }
      k_mutex_unlock(&motorsMu);
      break;
    case CAL_FINISH_ONE:
      if (hostMode != HOST_CAL || pkt.index >= NUM_MOTORS) break;
      k_mutex_lock(&motorsMu, K_FOREVER);
      motors[pkt.index].zeroPos = motors[pkt.index].fbPos;
      k_mutex_unlock(&motorsMu);
      break;
    case CAL_SET_ZEROS:
      k_mutex_lock(&motorsMu, K_FOREVER);
      for (unsigned i = 0; i < NUM_MOTORS; ++i) {
        if (pkt.mask & (1u << i)) motors[i].zeroPos = satPos(pkt.raw[i]);
      }
      k_mutex_unlock(&motorsMu);
      break;
  }
}

static void applyServoOp(const ServoPacket &pkt) {
  ServoReplyPacket reply = {};
  memcpy(reply.magic, "DQS2", 4);
  reply.op = pkt.op;
  reply.targetId = pkt.targetId;
  reply.ok = 1;
  switch (pkt.op) {
    case SERVO_ENTER:
      hostMode = HOST_SERVO_DEBUG;
      rawNotifyEveryTick = true;
      setTorqueEnable(false);
      setRunKpKd();
      break;
    case SERVO_EXIT:
      hostMode = HOST_NORMAL;
      rawNotifyEveryTick = false;
      setTorqueEnable(false);
      setRunKpKd();
      break;
    case SERVO_UNLOCK:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoUnlock(pkt.targetId);
      break;
    case SERVO_SET_ID:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoUnlock(pkt.targetId);
      k_msleep(2);
      servoSetId(pkt.targetId, pkt.newId);
      break;
    case SERVO_GOTO:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      setRunKpKd();
      setTorqueEnable(true);
      writeSingleGoalRaw(pkt.targetId, pkt.rawPos);
      Serial1.flush();
      break;
    case SERVO_SET_PERM_KP_KD:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoUnlock(pkt.targetId);
      k_msleep(2);
      servoSetPermKpKd(pkt.targetId, pkt.kp, pkt.kd);
      break;
    case SERVO_READ:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      drainServoRx();
      readSinglePresent(pkt.targetId);
      Serial1.flush();
      {
        const uint32_t start = micros();
        while ((uint32_t)(micros() - start) < SERVO_REPLY_US) {
          parseServoRx();
          k_sleep(K_USEC(100));
        }
        parseServoRx();
      }
      {
        const int idx = motorIndexById(pkt.targetId);
        if (idx >= 0) {
          k_mutex_lock(&motorsMu, K_FOREVER);
          reply.rawPos = motors[idx].fbPos;
          reply.zeroPos = motors[idx].zeroPos;
          k_mutex_unlock(&motorsMu);
          reply.ok = (servoRxMask & (1u << idx)) ? 1 : 0;
        } else {
          // The ID is not in MOTOR_IDS yet. Bench programming still reports success.
          reply.ok = 1;
          reply.rawPos = 0;
        }
      }
      break;
  }
  k_mutex_lock(&hostMu, K_FOREVER);
  servoReply = reply;
  servoReplyReady = true;
  k_mutex_unlock(&hostMu);
}

static void controlTask(void*, void*, void*) {
  Serial1.begin(SERVO_BAUD);
  enableUartFifo(reinterpret_cast<USART_TypeDef*>(DT_REG_ADDR(DT_PHANDLE_BY_IDX(DT_PATH(zephyr_user), serials, 0))));
  for (unsigned i=0; i<3; ++i) { setTorqueEnable(false); k_msleep(5); }
  setRunKpKd();
  // Read temporary KP/KD once at boot (address 50, 2 bytes). Missing IDs stay unset.
  readAllRegisters(ADDR_TEMP_KP, 2); Serial1.flush();
  const uint32_t pdStart=micros();
  while ((uint32_t)(micros()-pdStart)<20000) { parseServoRx(); k_sleep(K_USEC(100)); }
  initImu();
  uint32_t deadline = micros(), previous = deadline, seq = 0, lastCommandUs = 0;
  uint32_t acceptedSeq = 0, ttl = COMMAND_TTL_US, overruns = 0, lastImuUs = 0;
  uint32_t lastHealthyUs = 0;
  bool enabled = false, timedOut = false, haveCommand = false;
  for (;;) {
    const uint32_t start = micros(), period = start - previous;
    previous = start;

    CalPacket calPkt;
    ServoPacket servoPkt;
    bool gotCal = false, gotServo = false;
    k_mutex_lock(&hostMu, K_FOREVER);
    if (calPending) { calPkt = pendingCal; calPending = false; gotCal = true; }
    if (servoPending) { servoPkt = pendingServo; servoPending = false; gotServo = true; }
    k_mutex_unlock(&hostMu);
    if (gotCal) applyCalOp(calPkt);
    if (gotServo) applyServoOp(servoPkt);

    CommandPacket cmd;
    k_mutex_lock(&commandMu, K_FOREVER);
    const bool got = pending;
    if (got) { cmd = latest; pending = false; }
    const uint32_t bad = invalidCommands;
    k_mutex_unlock(&commandMu);

    const bool busTaken = hostMode != HOST_NORMAL;
    if (got && !busTaken) {
      const bool fresh = cmd.mode == DISARM || (uint32_t)(start - cmd.sourceUs) <= MAX_SOURCE_AGE_US;
      const bool ordered = !haveCommand || cmd.mode == DISARM || (int32_t)(cmd.seq - acceptedSeq) > 0;
      if (fresh && ordered) {
        acceptedSeq = cmd.seq; haveCommand = true; lastCommandUs = start; ttl = cmd.ttlUs;
        if (cmd.mode == DISARM) { enabled = false; timedOut = false; }
        if (cmd.mode == ARM && !enabled && imu.ok &&
            (uint32_t)(start-lastImuUs) < 50000) {
          enabled = true; timedOut = false;
          lastHealthyUs = start;
          setRunKpKd();
        }
        k_mutex_lock(&motorsMu, K_FOREVER);
        for (unsigned i = 0; i < NUM_MOTORS; ++i) motors[i].desAngleDeg = cmd.target[i];
        k_mutex_unlock(&motorsMu);
      } else {
        k_mutex_lock(&commandMu, K_FOREVER); ++invalidCommands; k_mutex_unlock(&commandMu);
      }
    }
    if (busTaken) {
      enabled = false;
      timedOut = false;
    } else if (enabled && ((uint32_t)(start-lastCommandUs) > ttl ||
                           (uint32_t)(start-lastHealthyUs) > 100000)) {
      enabled = false; timedOut = true;
    }
    static bool previousEnabled = false;
    if (hostMode == HOST_CAL) {
      k_mutex_lock(&motorsMu, K_FOREVER); sendMotorRawPos(); k_mutex_unlock(&motorsMu);
      Serial1.flush();
    } else if (enabled) {
      k_mutex_lock(&motorsMu, K_FOREVER); sendMotorDesPos(); k_mutex_unlock(&motorsMu);
      Serial1.flush();
    }
    if (!busTaken) {
      if (enabled != previousEnabled || !enabled) setTorqueEnable(enabled);
      previousEnabled = enabled;
    } else {
      previousEnabled = false;
    }
    drainServoRx();
    servoRxMask = 0;
    readAllState(); Serial1.flush();
    const uint32_t readStart = micros();
    if (updateImu()) lastImuUs = micros();
    while ((uint32_t)(micros()-readStart) < SERVO_REPLY_US) {
      parseServoRx(); k_sleep(K_USEC(100));
    }
    parseServoRx();
    if (imu.ok) lastHealthyUs = micros();
    StatePacket s = {};
    memcpy(s.magic, "DQS1", 4); s.version = 1;
    s.flags = (imu.ok ? 1 : 0) | (enabled ? 2 : 0) | (timedOut ? 4 : 0) |
              ((hostMode == HOST_CAL) ? 8 : 0) | ((hostMode == HOST_SERVO_DEBUG) ? 16 : 0);
    s.servoMask = servoRxMask; s.seq = ++seq; s.sampleUs = start; s.imuUs = lastImuUs;
    s.commandSeq = acceptedSeq; s.commandAgeUs = haveCommand ? start-lastCommandUs : UINT32_MAX;
    s.periodUs = period; s.overruns = overruns; s.invalidCommands = bad;
    memcpy(s.acc, imu.accRaw, sizeof(s.acc)); memcpy(s.gyro, imu.gyroRaw, sizeof(s.gyro));
    k_mutex_lock(&motorsMu, K_FOREVER);
    for (unsigned i = 0; i < NUM_MOTORS; ++i) {
      s.angle[i] = motors[i].fbAngleDeg; s.velocity[i] = motors[i].fbVelDeg;
      s.target[i] = motors[i].desAngleDeg;
    }
    k_mutex_unlock(&motorsMu);
    s.workUs = micros()-start;
    k_mutex_lock(&stateMu, K_FOREVER);
    if (k_sem_count_get(&reportReady)) ++reportDrops;
    s.reportDrops = reportDrops; snapshot = s;
    k_mutex_unlock(&stateMu);
    k_sem_give(&reportReady);
    deadline += TICK_US;
    const uint32_t now = micros();
    if ((int32_t)(deadline-now) <= 0) {
      const uint32_t missed = (now-deadline)/TICK_US+1;
      overruns += missed; deadline += missed*TICK_US;
    }
    const int32_t remaining = (int32_t)(deadline-micros());
    if (remaining > 0) k_sleep(K_USEC(remaining));
  }
}

static void reportTask(void*, void*, void*) {
  MsgPack::bin_t<uint8_t> bytes;
  unsigned reports = 0;
  bytes.resize(sizeof(StatePacket));
  for (;;) {
    k_sem_take(&reportReady, K_FOREVER);
    k_mutex_lock(&stateMu, K_FOREVER);
    memcpy(bytes.data(), &snapshot, sizeof(snapshot));
    k_mutex_unlock(&stateMu);
    Bridge.notify("duck_state", bytes);
    const bool wantRaw = rawNotifyEveryTick || ((reports % 20) == 0);
    if (wantRaw) {
      RawPacket raw = {};
      fillRawPacket(raw);
      MsgPack::bin_t<uint8_t> rawBytes;
      rawBytes.resize(sizeof(raw));
      memcpy(rawBytes.data(), &raw, sizeof(raw));
      Bridge.notify("duck_raw", rawBytes);
    }
    bool replyReady = false;
    ServoReplyPacket reply = {};
    k_mutex_lock(&hostMu, K_FOREVER);
    if (servoReplyReady) { reply = servoReply; servoReplyReady = false; replyReady = true; }
    k_mutex_unlock(&hostMu);
    if (replyReady) {
      MsgPack::bin_t<uint8_t> rb;
      rb.resize(sizeof(reply));
      memcpy(rb.data(), &reply, sizeof(reply));
      Bridge.notify("duck_servo_reply", rb);
    }
    if ((++reports % 100) == 0) {
      MsgPack::bin_t<uint8_t> pd;
      pd.resize(32);
      pd[0]=servoPdMask & 255; pd[1]=servoPdMask >> 8;
      memcpy(pd.data()+2, servoBootP, 15); memcpy(pd.data()+17, servoBootD, 15);
      Bridge.notify("duck_boot_pd", pd);
    }
  }
}

void setup() {
  k_mutex_init(&motorsMu);
  k_mutex_init(&commandMu);
  k_mutex_init(&stateMu);
  k_mutex_init(&hostMu);
  k_sem_init(&reportReady, 0, 1);
  for (unsigned i = 0; i < NUM_MOTORS; ++i) {
    motors[i] = {}; motors[i].id = MOTOR_IDS[i]; motors[i].zeroPos = ZERO_POS[i];
    motors[i].fbPos = ZERO_POS[i];
    motors[i].sign = MOTOR_SIGN[i]; motors[i].desAngleDeg = DEFAULT_DEG[i];
    motors[i].desPos = CAL_CENTER_POS;
  }
  // Start the watchdog/hardware loop even if Linux Bridge startup stalls.
  k_thread_create(&controlThread, controlStack, K_THREAD_STACK_SIZEOF(controlStack),
                  controlTask, nullptr, nullptr, nullptr, 4, 0, K_NO_WAIT);
  k_thread_name_set(&controlThread, "duck_control100");
  ARDUINO_ROUTER_SERIAL.begin(BRIDGE_BAUD);
  enableUartFifo(reinterpret_cast<USART_TypeDef*>(DT_REG_ADDR(ARDUINO_ROUTER_PHANDLE)));
  Bridge.begin(BRIDGE_BAUD);
  Bridge.provide("duck_command", receiveCommand);
  Bridge.provide("duck_cal", receiveCal);
  Bridge.provide("duck_servo", receiveServo);
  k_thread_create(&reportThread, reportStack, K_THREAD_STACK_SIZEOF(reportStack),
                  reportTask, nullptr, nullptr, nullptr, 6, 0, K_NO_WAIT);
  k_thread_name_set(&reportThread, "duck_report100");
  // GCC 14 otherwise emits a tail B.W to a flash syscall from RAM, outside
  // Thumb JUMP24 range; the Uno Q extension loader rejects it with ENOEXEC.
  asm volatile("nop" ::: "memory");
}
void loop() { delay(100); }
