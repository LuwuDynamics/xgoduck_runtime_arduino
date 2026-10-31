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
static struct k_mutex commandMu, stateMu, hostMu, imuMu;
static struct k_sem reportReady;
K_THREAD_STACK_DEFINE(controlStack, 4096);
K_THREAD_STACK_DEFINE(reportStack, 4096);
K_THREAD_STACK_DEFINE(imuStack, 4096);
K_THREAD_STACK_DEFINE(bridgeStack, 4096);
static struct k_thread controlThread, reportThread, imuThread, bridgeThread;
static StatePacket snapshot = {};
static RawPacket rawSnapshot = {};
static bool rawSnapshotEveryTick = false;
static DiagnosticPacket diagnosticSnapshot = {};
static atomic_t bridgeReady = ATOMIC_INIT(0), bridgeStage = ATOMIC_INIT(0);
static atomic_t bridgeAttempts = ATOMIC_INIT(0), bridgeFailures = ATOMIC_INIT(0);
static atomic_t notifyDrops = ATOMIC_INIT(0), bridgeReconnects = ATOMIC_INIT(0);
static atomic_t bridgeReadyUs = ATOMIC_INIT(0);
static atomic_t lastHostRxUs = ATOMIC_INIT(0);
static ImuSnapshot imuSnapshot = {};
static CommandPacket latest = {};
static bool pending = false;
static uint32_t invalidCommands = 0, reportDrops = 0;

static HostBusMode hostMode = HOST_NORMAL;
static CalPacket pendingCal = {};
static bool calPending = false;
static CalReplyPacket calReply = {};
static bool calReplyReady = false;
static uint16_t calHeldMask = 0;
static uint8_t calIndex = 0xFF, calMidCount = 0;
static uint32_t calNextUs = 0, calSeq = 0;
static constexpr uint8_t CAL_MID_REPEATS = 5;
static constexpr uint32_t CAL_MID_INTERVAL_US = 10000;
static constexpr uint32_t CAL_MID_SETTLE_US = 100000;
static ServoPacket pendingServo = {};
static bool servoPending = false;
static ServoReplyPacket servoReply = {};
static bool servoReplyReady = false;
static uint8_t servoMidId = 0xFF, servoMidCount = 0;
static uint32_t servoMidNextUs = 0;
static bool rawNotifyEveryTick = false;

static ImuSnapshot readImuSnapshot() {
  ImuSnapshot out;
  k_mutex_lock(&imuMu, K_FOREVER);
  out = imuSnapshot;
  k_mutex_unlock(&imuMu);
  return out;
}

static void imuTask(void*, void*, void*) {
  ImuSnapshot out = {};
  uint32_t deadline = micros(), previousSampleUs = 0, nextInitUs = 0;
  unsigned failedReads = 0;
  bool configured = false;
  for (;;) {
    if (!configured && (int32_t)(micros() - nextInitUs) >= 0) {
      ++out.initAttempts;
      configured = initImu();
      failedReads = 0;
      deadline = micros();
      previousSampleUs = 0;
      if (!configured) { ++out.errors; nextInitUs = micros() + 1000000u; }
    }
    const uint32_t start = micros();
    if (configured && updateImu()) {
      out.sample = imu;
      out.sampleUs = micros();
      out.periodUs = previousSampleUs ? out.sampleUs - previousSampleUs : TICK_US;
      previousSampleUs = out.sampleUs;
      ++out.seq;
      failedReads = 0;
    } else {
      out.sample.ok = 0;
      if (configured) {
        ++out.errors;
        if (++failedReads >= 3) {
          configured = false;
          qmiAddr = 0;
          nextInitUs = micros() + 1000000u;
        }
      }
    }
    out.workUs = micros() - start;
    k_mutex_lock(&imuMu, K_FOREVER);
    imuSnapshot = out;
    k_mutex_unlock(&imuMu);
    deadline += TICK_US;
    const uint32_t now = micros();
    if ((int32_t)(deadline - now) <= 0) {
      const uint32_t missed = (now - deadline) / TICK_US + 1;
      out.overruns += missed;
      deadline += missed * TICK_US;
    }
    const int32_t remaining = (int32_t)(deadline - micros());
    if (remaining > 0) k_sleep(K_USEC(remaining));
  }
}

static void receiveCommand(MsgPack::bin_t<uint8_t> bytes) {
  CommandPacket cmd;
  bool valid = bytes.size() == sizeof(cmd);
  if (valid) {
    memcpy(&cmd, bytes.data(), sizeof(cmd));
    valid = memcmp(cmd.magic, "DQC1", 4) == 0 && cmd.mode <= HOLD &&
            !cmd.reserved && !cmd.reserved2;
    for (unsigned i = 0; i < NUM_MOTORS; ++i)
      valid = valid && isfinite(cmd.target[i]);
  }
  k_mutex_lock(&commandMu, K_FOREVER);
  if (valid && cmd.mode != DISARM && !atomic_get(&bridgeReady)) valid = false;
  if (valid) { latest = cmd; pending = true; atomic_set(&lastHostRxUs, micros()); }
  else ++invalidCommands;
  k_mutex_unlock(&commandMu);
}

static void receiveCal(MsgPack::bin_t<uint8_t> bytes) {
  CalPacket pkt;
  bool valid = bytes.size() == sizeof(pkt);
  if (valid) {
    memcpy(&pkt, bytes.data(), sizeof(pkt));
    valid = memcmp(pkt.magic, "DQL2", 4) == 0 && pkt.op <= CAL_CENTER_ONE;
    if (pkt.op == CAL_CENTER_ONE) valid = valid && pkt.index < NUM_MOTORS;
    if (!atomic_get(&bridgeReady) && pkt.op != CAL_EXIT) valid = false;
  }
  k_mutex_lock(&hostMu, K_FOREVER);
  if (valid) { pendingCal = pkt; calPending = true; atomic_set(&lastHostRxUs, micros()); }
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
    valid = memcmp(pkt.magic, "DQV2", 4) == 0 && pkt.op <= SERVO_SET_PROTECTION_CURRENT && pkt.op != 4;
    if (pkt.op == SERVO_SET_PROTECTION || pkt.op == SERVO_SET_PROTECTION_CURRENT) {
      valid = valid && pkt.targetId == 0xFE && !pkt.newId && !pkt.kp && !pkt.kd;
      if (pkt.op == SERVO_SET_PROTECTION) {
        valid = valid && !pkt.protectionCurrent &&
                (pkt.protectionFlags == PROTECTION_ENABLED || pkt.protectionFlags == PROTECTION_DISABLED);
      } else {
        valid = valid && !pkt.protectionFlags;
      }
    } else {
      valid = valid && pkt.targetId <= 253 && !pkt.reserved && !pkt.reserved2;
    }
    if (pkt.op == SERVO_SET_ID) valid = valid && pkt.newId <= 253;
    if (!atomic_get(&bridgeReady) && pkt.op != SERVO_EXIT) valid = false;
  }
  k_mutex_lock(&hostMu, K_FOREVER);
  if (valid) { pendingServo = pkt; servoPending = true; atomic_set(&lastHostRxUs, micros()); }
  else {
    k_mutex_lock(&commandMu, K_FOREVER); ++invalidCommands; k_mutex_unlock(&commandMu);
  }
  k_mutex_unlock(&hostMu);
}

static void fillRawPacket(RawPacket &r) {
  memcpy(r.magic, "DQR2", 4);
  r.mask = servoRxMask;
  k_mutex_lock(&motorsMu, K_FOREVER);
  r.heldMask = calHeldMask;
  for (unsigned i = 0; i < NUM_MOTORS; ++i) {
    r.pos[i] = motors[i].fbPos;
  }
  k_mutex_unlock(&motorsMu);
}

static void publishCalReply(uint32_t seq, uint8_t index, bool ok) {
  k_mutex_lock(&hostMu, K_FOREVER);
  memcpy(calReply.magic, "DQA1", 4);
  calReply.seq = seq;
  calReply.index = index;
  calReply.ok = ok ? 1 : 0;
  calReplyReady = true;
  k_mutex_unlock(&hostMu);
}

static void applyCalOp(const CalPacket &pkt) {
  switch (pkt.op) {
    case CAL_ENTER:
      hostMode = HOST_CAL;
      rawNotifyEveryTick = true;
      calIndex = 0xFF;
      k_mutex_lock(&motorsMu, K_FOREVER);
      calHeldMask = 0;
      k_mutex_unlock(&motorsMu);
      setTorqueEnable(false);
      break;
    case CAL_EXIT:
      if (hostMode != HOST_CAL) break;
      hostMode = HOST_NORMAL;
      rawNotifyEveryTick = false;
      calIndex = 0xFF;
      k_mutex_lock(&motorsMu, K_FOREVER);
      calHeldMask = 0;
      k_mutex_unlock(&motorsMu);
      setTorqueEnable(false);
      setMouthKpKd();
      break;
    case CAL_CENTER_ONE:
      if (hostMode != HOST_CAL || calIndex != 0xFF ||
          (calHeldMask & (1u << pkt.index)) || !(servoRxMask & (1u << pkt.index))) {
        publishCalReply(pkt.seq, pkt.index, false);
        break;
      }
      scsWriteByte(MOTOR_IDS[pkt.index], ADDR_TORQUE, 0);
      Serial1.flush();
      k_mutex_lock(&motorsMu, K_FOREVER);
      calHeldMask &= ~(1u << pkt.index);
      k_mutex_unlock(&motorsMu);
      calIndex = pkt.index;
      calSeq = pkt.seq;
      calMidCount = 0;
      calNextUs = micros();
      break;
  }
}

static void advanceCalibration() {
  if (hostMode != HOST_CAL || calIndex == 0xFF ||
      (int32_t)(micros() - calNextUs) < 0) return;
  const uint8_t id = MOTOR_IDS[calIndex];
  if (calMidCount < CAL_MID_REPEATS) {
    servoMid(id);
    ++calMidCount;
    calNextUs = micros() + (calMidCount == CAL_MID_REPEATS
        ? CAL_MID_SETTLE_US : CAL_MID_INTERVAL_US);
    return;
  }
  // Only this servo is loaded. No goals are written for other joints.
  // Keep the servo's existing gains while holding its calibrated center.
  scsWriteByte(id, ADDR_TORQUE, 1);
  writeSingleGoalRaw(id, CAL_HOLD_POS);
  Serial1.flush();
  k_mutex_lock(&motorsMu, K_FOREVER);
  calHeldMask |= 1u << calIndex;
  k_mutex_unlock(&motorsMu);
  publishCalReply(calSeq, calIndex, true);
  calIndex = 0xFF;
}

static void publishServoReply(const ServoReplyPacket &reply) {
  k_mutex_lock(&hostMu, K_FOREVER);
  servoReply = reply;
  servoReplyReady = true;
  k_mutex_unlock(&hostMu);
}

static void advanceServoMid() {
  if (hostMode != HOST_SERVO_DEBUG) { servoMidId = 0xFF; return; }
  if (servoMidId == 0xFF || (int32_t)(micros() - servoMidNextUs) < 0) return;
  servoMid(servoMidId);
  if (++servoMidCount < 3) {
    servoMidNextUs = micros() + 10000;
    return;
  }
  ServoReplyPacket reply = {};
  memcpy(reply.magic, "DQS3", 4);
  reply.op = SERVO_RESET_MID;
  reply.targetId = servoMidId;
  reply.centerPos = SERVO_CENTER_POS;
  reply.ok = 1;
  servoMidId = 0xFF;
  publishServoReply(reply);
}

static void applyServoOp(const ServoPacket &pkt) {
  ServoReplyPacket reply = {};
  memcpy(reply.magic, "DQS3", 4);
  reply.op = pkt.op;
  reply.targetId = pkt.targetId;
  reply.centerPos = SERVO_CENTER_POS;
  reply.ok = 1;
  if (servoMidId != 0xFF && pkt.op != SERVO_ENTER && pkt.op != SERVO_EXIT) {
    reply.ok = 0;
    publishServoReply(reply);
    return;
  }
  switch (pkt.op) {
    case SERVO_ENTER:
      hostMode = HOST_SERVO_DEBUG;
      rawNotifyEveryTick = true;
      servoMidId = 0xFF;
      setTorqueEnable(false);
      setMouthKpKd();
      break;
    case SERVO_EXIT:
      if (hostMode != HOST_SERVO_DEBUG) break;
      hostMode = HOST_NORMAL;
      rawNotifyEveryTick = false;
      servoMidId = 0xFF;
      setTorqueEnable(false);
      setMouthKpKd();
      break;
    case SERVO_UNLOCK:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoUnlock(pkt.targetId);
      break;
    case SERVO_SET_ID:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      setTorqueEnable(false);
      servoSetId(pkt.targetId, pkt.newId);
      break;
    case SERVO_RESET_MID:
      if (hostMode != HOST_SERVO_DEBUG || !pingServo(pkt.targetId)) { reply.ok = 0; break; }
      scsWriteByte(pkt.targetId, ADDR_TORQUE, 0);
      Serial1.flush();
      servoMidId = pkt.targetId;
      servoMidCount = 0;
      servoMidNextUs = micros();
      return; // Reply only after all three mid commands have been sent.
    case SERVO_SET_PERM_KP_KD:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoSetPermKpKd(pkt.targetId, pkt.kp, pkt.kd);
      break;
    case SERVO_SET_PROTECTION:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoSetProtection(pkt.protectionFlags);
      reply.protectionFlags = pkt.protectionFlags;
      break;
    case SERVO_SET_PROTECTION_CURRENT:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoSetProtectionCurrent(pkt.protectionCurrent);
      reply.protectionCurrent = pkt.protectionCurrent;
      break;
    case SERVO_PING:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      reply.ok = pingServo(pkt.targetId) ? 1 : 0;
      break;
    case SERVO_READ: {
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      uint16_t pos = 0;
      uint8_t kp = 0, kd = 0;
      reply.ok = readServoPresent(pkt.targetId, pos) && readServoGains(pkt.targetId, kp, kd);
      reply.rawPos = pos;
      reply.kp = kp; reply.kd = kd;
      break;
    }
    default:
      reply.ok = 0;
      break;
  }
  publishServoReply(reply);
}

static void controlTask(void*, void*, void*) {
  Serial1.begin(SERVO_BAUD);
  enableUartFifo(reinterpret_cast<USART_TypeDef*>(DT_REG_ADDR(DT_PHANDLE_BY_IDX(DT_PATH(zephyr_user), serials, 0))));
  for (unsigned i=0; i<3; ++i) { setTorqueEnable(false); k_msleep(5); }
  setMouthKpKd();
  // Read temporary KP/KD once at boot (address 50, 2 bytes). Missing IDs stay unset.
  readAllRegisters(ADDR_TEMP_KP, 2); Serial1.flush();
  const uint32_t pdStart=micros();
  while ((uint32_t)(micros()-pdStart)<20000) { parseServoRx(); k_sleep(K_USEC(100)); }
  uint32_t deadline = micros(), previous = deadline, seq = 0, lastCommandUs = 0;
  uint32_t acceptedSeq = 0, overruns = 0;
  uint32_t servoWrites = 0, servoReads = 0;
  bool enabled = false, timedOut = false, haveCommand = false;
  for (;;) {
    const uint32_t start = micros(), period = start - previous;
    previous = start;
    ImuSnapshot sampledImu = readImuSnapshot();
    const bool imuFresh = sampledImu.sample.ok && sampledImu.seq &&
                         (uint32_t)(start - sampledImu.sampleUs) <= IMU_STALE_US;

    CalPacket calPkt;
    ServoPacket servoPkt;
    bool gotCal = false, gotServo = false;
    k_mutex_lock(&hostMu, K_FOREVER);
    if (calPending) { calPkt = pendingCal; calPending = false; gotCal = true; }
    if (servoPending) { servoPkt = pendingServo; servoPending = false; gotServo = true; }
    k_mutex_unlock(&hostMu);
    if (gotCal) applyCalOp(calPkt);
    if (gotServo) applyServoOp(servoPkt);
    advanceCalibration();
    advanceServoMid();

    CommandPacket cmd;
    k_mutex_lock(&commandMu, K_FOREVER);
    const bool got = pending;
    if (got) { cmd = latest; pending = false; }
    const uint32_t bad = invalidCommands;
    k_mutex_unlock(&commandMu);

    // Servo operations above can sleep while Bridge receives a newer heartbeat.
    // Read its timestamp before sampling "now" to avoid unsigned underflow.
    const uint32_t hostRxUs = (uint32_t)atomic_get(&lastHostRxUs);
    const uint32_t hostCheckUs = micros();
    if (hostMode != HOST_NORMAL &&
        (!atomic_get(&bridgeReady) ||
         (uint32_t)(hostCheckUs - hostRxUs) > HOST_HEARTBEAT_US)) {
      // Exclusive modes retain their separate loss-of-host protection.
      hostMode = HOST_NORMAL;
      rawNotifyEveryTick = false;
      calIndex = servoMidId = 0xFF;
      k_mutex_lock(&motorsMu, K_FOREVER);
      calHeldMask = 0;
      k_mutex_unlock(&motorsMu);
      enabled = false; timedOut = true;
      setTorqueEnable(false);
      setMouthKpKd();
    }
    const bool busTaken = hostMode != HOST_NORMAL;
    if (got && !busTaken) {
      const bool fresh = cmd.mode == DISARM || (uint32_t)(start - cmd.sourceUs) <= MAX_SOURCE_AGE_US;
      const bool ordered = !haveCommand || cmd.mode == DISARM || (int32_t)(cmd.seq - acceptedSeq) > 0;
      if (fresh && ordered) {
        acceptedSeq = cmd.seq; haveCommand = true; lastCommandUs = start;
        if (cmd.mode == DISARM) { enabled = false; timedOut = false; }
        if (cmd.mode == ARM && !enabled && atomic_get(&bridgeReady) && imuFresh &&
            (uint32_t)(start-sampledImu.sampleUs) < 50000) {
          enabled = true; timedOut = false;
          setMouthKpKd();
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
    } else if (enabled && (!imuFresh || !atomic_get(&bridgeReady))) {
      enabled = false; timedOut = true;
    }
    static bool previousEnabled = false;
    if (enabled) {
      k_mutex_lock(&motorsMu, K_FOREVER); sendMotorDesPos(); k_mutex_unlock(&motorsMu);
      Serial1.flush();
      ++servoWrites;
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
    ++servoReads;
    const uint32_t readStart = micros();
    while ((uint32_t)(micros()-readStart) < SERVO_REPLY_US) {
      parseServoRx();
      if (servoRxMask == 0x7FFFu) break;
      k_sleep(K_USEC(100));
    }
    parseServoRx();
    const uint32_t servoWaitUs = micros() - readStart;
    sampledImu = readImuSnapshot();
    const uint32_t sampleUs = micros();
    const bool publishImuOk = sampledImu.sample.ok && sampledImu.seq &&
                             (uint32_t)(sampleUs - sampledImu.sampleUs) <= IMU_STALE_US;
    StatePacket s = {};
    memcpy(s.magic, "DQS1", 4); s.version = 1;
    s.flags = (publishImuOk ? 1 : 0) | (enabled ? 2 : 0) | (timedOut ? 4 : 0) |
              ((hostMode == HOST_CAL) ? 8 : 0) | ((hostMode == HOST_SERVO_DEBUG) ? 16 : 0);
    s.servoMask = servoRxMask; s.seq = ++seq; s.sampleUs = sampleUs; s.imuUs = sampledImu.sampleUs;
    s.commandSeq = acceptedSeq; s.commandAgeUs = haveCommand ? start-lastCommandUs : UINT32_MAX;
    s.periodUs = period; s.overruns = overruns; s.invalidCommands = bad;
    memcpy(s.acc, sampledImu.sample.accRaw, sizeof(s.acc));
    memcpy(s.gyro, sampledImu.sample.gyroRaw, sizeof(s.gyro));
    k_mutex_lock(&motorsMu, K_FOREVER);
    for (unsigned i = 0; i < NUM_MOTORS; ++i) {
      s.angle[i] = motors[i].fbAngleDeg; s.velocity[i] = motors[i].fbVelDeg;
      s.target[i] = motors[i].desAngleDeg;
    }
    k_mutex_unlock(&motorsMu);
    s.workUs = micros()-start;
    RawPacket raw;
    fillRawPacket(raw);
    DiagnosticPacket diagnostic = {};
    memcpy(diagnostic.magic, "DQD1", 4);
    diagnostic.bridgeState = atomic_get(&bridgeStage);
    diagnostic.bridgeAttempts = atomic_get(&bridgeAttempts);
    diagnostic.bridgeFailures = atomic_get(&bridgeFailures);
    diagnostic.notifyDrops = atomic_get(&notifyDrops);
    diagnostic.imuSeq = sampledImu.seq; diagnostic.imuUs = sampledImu.sampleUs;
    diagnostic.imuPeriodUs = sampledImu.periodUs; diagnostic.imuWorkUs = sampledImu.workUs;
    diagnostic.imuOverruns = sampledImu.overruns; diagnostic.imuErrors = sampledImu.errors;
    diagnostic.imuInitAttempts = sampledImu.initAttempts;
    diagnostic.servoWrites = servoWrites; diagnostic.servoReads = servoReads;
    diagnostic.servoWaitUs = servoWaitUs;
    diagnostic.reconnects = atomic_get(&bridgeReconnects);
    diagnostic.bridgeReadyUs = (uint32_t)atomic_get(&bridgeReadyUs);
    k_mutex_lock(&stateMu, K_FOREVER);
    if (k_sem_count_get(&reportReady)) ++reportDrops;
    s.reportDrops = reportDrops; snapshot = s;
    rawSnapshot = raw; rawSnapshotEveryTick = rawNotifyEveryTick;
    diagnosticSnapshot = diagnostic;
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
    RawPacket raw;
    DiagnosticPacket diagnostic;
    bool rawEveryTick;
    k_mutex_lock(&stateMu, K_FOREVER);
    memcpy(bytes.data(), &snapshot, sizeof(snapshot));
    raw = rawSnapshot; diagnostic = diagnosticSnapshot;
    rawEveryTick = rawSnapshotEveryTick;
    k_mutex_unlock(&stateMu);
    if (!atomic_get(&bridgeReady)) continue;
    if (!Bridge.tryNotify("duck_state", bytes)) { atomic_inc(&notifyDrops); continue; }
    const bool wantRaw = rawEveryTick || ((reports % 20) == 0);
    if (wantRaw) {
      MsgPack::bin_t<uint8_t> rawBytes;
      rawBytes.resize(sizeof(raw));
      memcpy(rawBytes.data(), &raw, sizeof(raw));
      if (!Bridge.tryNotify("duck_raw", rawBytes)) atomic_inc(&notifyDrops);
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
      if (!Bridge.tryNotify("duck_servo_reply", rb)) {
        atomic_inc(&notifyDrops);
        k_mutex_lock(&hostMu, K_FOREVER);
        if (!servoReplyReady) { servoReply = reply; servoReplyReady = true; }
        k_mutex_unlock(&hostMu);
      }
    }
    CalReplyPacket cr = {};
    bool calReady = false;
    k_mutex_lock(&hostMu, K_FOREVER);
    if (calReplyReady) { cr = calReply; calReplyReady = false; calReady = true; }
    k_mutex_unlock(&hostMu);
    if (calReady) {
      MsgPack::bin_t<uint8_t> rb;
      rb.resize(sizeof(cr));
      memcpy(rb.data(), &cr, sizeof(cr));
      if (!Bridge.tryNotify("duck_cal_reply", rb)) {
        atomic_inc(&notifyDrops);
        k_mutex_lock(&hostMu, K_FOREVER);
        if (!calReplyReady) { calReply = cr; calReplyReady = true; }
        k_mutex_unlock(&hostMu);
      }
    }
    if ((reports % 10) == 0) {
      MsgPack::bin_t<uint8_t> db;
      db.resize(sizeof(diagnostic));
      memcpy(db.data(), &diagnostic, sizeof(diagnostic));
      if (!Bridge.tryNotify("duck_diagnostics", db)) atomic_inc(&notifyDrops);
    }
    if ((++reports % 100) == 0) {
      MsgPack::bin_t<uint8_t> pd;
      pd.resize(32);
      pd[0]=servoPdMask & 255; pd[1]=servoPdMask >> 8;
      memcpy(pd.data()+2, servoBootP, 15); memcpy(pd.data()+17, servoBootD, 15);
      if (!Bridge.tryNotify("duck_boot_pd", pd)) atomic_inc(&notifyDrops);
    }
  }
}

static void bridgeTask(void*, void*, void*) {
  uint32_t backoffMs = 250;
  for (;;) {
    atomic_clear(&bridgeReady);
    atomic_set(&bridgeStage, 1);
    atomic_inc(&bridgeAttempts);
    // setup() already initialized this UART and enabled its hardware FIFO.
    bool ready = Bridge.begin(BRIDGE_BAUD, true);
    if (ready) {
      atomic_set(&bridgeStage, 2);
      ready = Bridge.provide("duck_command", receiveCommand) &&
              Bridge.provide("duck_cal", receiveCal) &&
              Bridge.provide("duck_servo", receiveServo);
    }
    if (!ready) {
      atomic_inc(&bridgeFailures);
      atomic_set(&bridgeStage, 4);
      Bridge.invalidate();
      k_msleep(backoffMs);
      backoffMs = backoffMs < 2000 ? backoffMs * 2 : 2000;
      continue;
    }
    atomic_set(&bridgeReadyUs, micros());
    atomic_set(&bridgeStage, 3);
    atomic_set(&bridgeReady, 1);
    backoffMs = 250;
    for (;;) {
      k_msleep(2000);
      MsgPack::str_t version;
      if (!Bridge.getRouterVersion(version)) break;
    }
    atomic_clear(&bridgeReady);
    atomic_inc(&bridgeFailures);
    atomic_inc(&bridgeReconnects);
    atomic_set(&bridgeStage, 4);
    k_mutex_lock(&commandMu, K_FOREVER);
    pending = false;
    k_mutex_unlock(&commandMu);
    Bridge.invalidate();
    k_msleep(backoffMs);
  }
}

void setup() {
  k_mutex_init(&motorsMu);
  k_mutex_init(&commandMu);
  k_mutex_init(&stateMu);
  k_mutex_init(&hostMu);
  k_mutex_init(&imuMu);
  k_sem_init(&reportReady, 0, 1);
  for (unsigned i = 0; i < NUM_MOTORS; ++i) {
    motors[i] = {}; motors[i].id = MOTOR_IDS[i];
    motors[i].fbPos = SERVO_CENTER_POS;
    motors[i].sign = MOTOR_SIGN[i];
    motors[i].desPos = SERVO_CENTER_POS;
  }
  // Start the watchdog/hardware loop even if Linux Bridge startup stalls.
  k_thread_create(&controlThread, controlStack, K_THREAD_STACK_SIZEOF(controlStack),
                  controlTask, nullptr, nullptr, nullptr, 3, 0, K_NO_WAIT);
  k_thread_name_set(&controlThread, "duck_control100");
  k_thread_create(&imuThread, imuStack, K_THREAD_STACK_SIZEOF(imuStack),
                  imuTask, nullptr, nullptr, nullptr, 4, 0, K_NO_WAIT);
  k_thread_name_set(&imuThread, "duck_imu100");
  ARDUINO_ROUTER_SERIAL.begin(BRIDGE_BAUD);
  enableUartFifo(reinterpret_cast<USART_TypeDef*>(DT_REG_ADDR(ARDUINO_ROUTER_PHANDLE)));
  k_thread_create(&bridgeThread, bridgeStack, K_THREAD_STACK_SIZEOF(bridgeStack),
                  bridgeTask, nullptr, nullptr, nullptr, 6, 0, K_NO_WAIT);
  k_thread_name_set(&bridgeThread, "duck_bridge_init");
  k_thread_create(&reportThread, reportStack, K_THREAD_STACK_SIZEOF(reportStack),
                  reportTask, nullptr, nullptr, nullptr, 7, 0, K_NO_WAIT);
  k_thread_name_set(&reportThread, "duck_report100");
  // GCC 14 otherwise emits a tail B.W to a flash syscall from RAM, outside
  // Thumb JUMP24 range; the Uno Q extension loader rejects it with ENOEXEC.
  asm volatile("nop" ::: "memory");
}
void loop() { delay(100); }
