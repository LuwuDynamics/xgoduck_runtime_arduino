#include <Arduino_RouterBridge.h>
#include <zephyr/kernel.h>
#include <math.h>
#include <string.h>
#include "duck_config.h"
#include "protocol.h"
#include "scs_bus.h"
#include "qmi8658.h"
#include "uart_fifo.h"
#include "tof_sensor.h"
#include "ip_matrix.h"

// The UNO Q core reinitializes this UART on each negotiated speed change.
void duckEnableRouterFifo() {
  enableUartFifo(reinterpret_cast<USART_TypeDef*>(DT_REG_ADDR(ARDUINO_ROUTER_PHANDLE)));
}

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
static ServoReplyDetail replyDetailSnapshot = {};
static bool sendReplyDetail = false;
static RawPacket rawSnapshot = {};
static bool rawSnapshotEveryTick = false;
static DiagnosticPacket diagnosticSnapshot = {};
static VoltagePacket voltageSnapshot = {};
static atomic_t bridgeReady = ATOMIC_INIT(0), bridgeStage = ATOMIC_INIT(0);
static atomic_t bridgeAttempts = ATOMIC_INIT(0), bridgeFailures = ATOMIC_INIT(0);
static atomic_t registeredSession = ATOMIC_INIT(-1);
static atomic_t notifyDrops = ATOMIC_INIT(0), bridgeReconnects = ATOMIC_INIT(0);
static atomic_t bridgeReadyUs = ATOMIC_INIT(0);
static atomic_t lastHostRxUs = ATOMIC_INIT(0);
static ImuSnapshot imuSnapshot = {};
static CommandPacket latest = {};
static bool pending = false;
static uint32_t invalidCommands = 0, reportDrops = 0;

static HostBusMode hostMode = HOST_NORMAL;
static CalPositionsPacket pendingCal = {};
static bool calPending = false;
static CalReplyPacket calReply = {};
static bool calReplyReady = false;
static uint16_t calHeldMask = 0;
static uint8_t calIndex = 0xFF, calMidCount = 0;
static uint32_t calNextUs = 0, calSeq = 0;
static constexpr uint8_t CAL_MID_REPEATS = 5;
static constexpr uint32_t CAL_MID_INTERVAL_US = 10000;
static constexpr uint32_t CAL_MID_SETTLE_US = 100000;
static constexpr uint32_t CAL_BATCH_INTERVAL_US = 100000;
static CalPositionsPacket calBatch = {};
static uint8_t calBatchIndex = 0;
static uint32_t calBatchNextUs = 0;
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

static bool imuAxesAreZero(const ImuSample &sample) {
  for (unsigned axis = 0; axis < 3; ++axis) {
    if (sample.accRaw[axis] != 0 || sample.gyroRaw[axis] != 0) return false;
  }
  return true;
}

static void imuTask(void*, void*, void*) {
  ImuSnapshot out = {};
  uint32_t deadline = micros(), previousSampleUs = 0, nextInitUs = 0;
  constexpr unsigned ZERO_FRAME_LIMIT = 10, RECOVERY_GOOD_FRAMES = 3;
  unsigned failedReads = 0, zeroFrames = 0, goodFramesNeeded = 0;
  bool configured = false;
  for (;;) {
    if (!configured && (int32_t)(micros() - nextInitUs) >= 0) {
      ++out.initAttempts;
      configured = initImu();
      failedReads = zeroFrames = 0;
      deadline = micros();
      previousSampleUs = 0;
      if (!configured) { ++out.errors; nextInitUs = micros() + 1000000u; }
    }
    const uint32_t start = micros();
    if (configured) {
      const bool readOk = updateImu();
      if (readOk && !imuAxesAreZero(imu)) {
        failedReads = zeroFrames = 0;
        if (goodFramesNeeded) --goodFramesNeeded;
        if (!goodFramesNeeded) {
          out.sample = imu;
          out.sampleUs = micros();
          out.periodUs = previousSampleUs ? out.sampleUs - previousSampleUs : TICK_US;
          previousSampleUs = out.sampleUs;
          ++out.seq;
        }
      } else {
        ++out.errors;
        if (goodFramesNeeded) goodFramesNeeded = RECOVERY_GOOD_FRAMES;
        if (readOk) {
          // Keep the last good sample and its original timestamp until the fault is confirmed.
          ++zeroFrames;
          failedReads = 0;
        } else {
          ++failedReads;
          zeroFrames = 0;
          out.sample.ok = 0;
        }
        if (zeroFrames == ZERO_FRAME_LIMIT || failedReads == 3) {
          if (zeroFrames == ZERO_FRAME_LIMIT) ++out.zeroResets;
          out.sample.ok = 0;
          goodFramesNeeded = RECOVERY_GOOD_FRAMES;
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

// session() is an atomic counter, so the control thread never waits for serial I/O.
static bool bridgeSessionReady() {
  return atomic_get(&bridgeReady) && Bridge.getLinkTransport()->session() ==
                                     (uint32_t)atomic_get(&registeredSession);
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
  if (valid && cmd.mode != DISARM && !bridgeSessionReady()) valid = false;
  if (valid) { latest = cmd; pending = true; atomic_set(&lastHostRxUs, micros()); }
  else ++invalidCommands;
  k_mutex_unlock(&commandMu);
}

static void receiveCal(MsgPack::bin_t<uint8_t> bytes) {
  CalPositionsPacket request = {};
  const bool parameterized = bytes.size() == sizeof(request);
  bool valid = parameterized || bytes.size() == sizeof(CalPacket);
  if (valid) {
    memcpy(&request, bytes.data(), bytes.size());
    const CalPacket &pkt = request.command;
    valid = memcmp(pkt.magic, "DQL2", 4) == 0 &&
            (parameterized ? pkt.op == CAL_SET_POSITIONS : pkt.op <= CAL_CENTER_ALL);
    if (pkt.op == CAL_CENTER_ONE) valid = valid && pkt.index < NUM_MOTORS;
    if (pkt.op == CAL_CENTER_ALL || pkt.op == CAL_SET_POSITIONS)
      valid = valid && pkt.index == CAL_ALL_JOINTS;
    if (parameterized) {
      for (unsigned i = 0; i < NUM_MOTORS; ++i)
        valid = valid && request.positions[i] >= POS_MIN && request.positions[i] <= POS_MAX;
    }
    if (!atomic_get(&bridgeReady) && pkt.op != CAL_EXIT) valid = false;
  }
  k_mutex_lock(&hostMu, K_FOREVER);
  if (valid) { pendingCal = request; calPending = true; atomic_set(&lastHostRxUs, micros()); }
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
    valid = memcmp(pkt.magic, "DQV2", 4) == 0 && pkt.op <= SERVO_INIT_ID_PROTECTION &&
            pkt.op != 4 && pkt.op != SERVO_APPLY_RUN_GAINS;
    if (pkt.op >= SERVO_TEST_START && pkt.op <= SERVO_TEST_STOP) {
      const int32_t offset = servoTestOffsetDeg(pkt.testOffset);
      valid = valid && motorIndexById(pkt.targetId) >= 0 && pkt.testSession &&
              !pkt.kp && !pkt.kd && !pkt.reserved &&
              (pkt.op == SERVO_TEST_MOVE ? offset >= -SERVO_TEST_OFFSET_DEG && offset <= SERVO_TEST_OFFSET_DEG
                                         : !pkt.testOffset);
    } else if (pkt.op == SERVO_SET_PROTECTION || pkt.op == SERVO_SET_PROTECTION_CURRENT) {
      valid = valid && pkt.targetId == 0xFE && !pkt.newId && !pkt.kp && !pkt.kd;
      if (pkt.op == SERVO_SET_PROTECTION) {
        valid = valid && !pkt.protectionCurrent &&
                (pkt.protectionFlags == PROTECTION_ENABLED || pkt.protectionFlags == PROTECTION_DISABLED);
      } else {
        valid = valid && !pkt.protectionFlags;
      }
    } else if (pkt.op == SERVO_SET_PROTECTION_SWITCH) {
      valid = valid && pkt.targetId <= 253 && !pkt.kp && !pkt.kd && !pkt.reserved2 &&
              (pkt.protectionMask == PROTECTION_VOLTAGE || pkt.protectionMask == PROTECTION_OVERCURRENT) &&
              (!pkt.protectionFlags || pkt.protectionFlags == pkt.protectionMask);
    } else {
      valid = valid && pkt.targetId <= 253 && !pkt.reserved && !pkt.reserved2;
    }
    if (pkt.op == SERVO_SET_ID) valid = valid && pkt.newId <= 253;
    if (pkt.op == SERVO_INIT_ID_PROTECTION) valid = valid && !pkt.newId && !pkt.kp && !pkt.kd;
    if (!atomic_get(&bridgeReady) && pkt.op != SERVO_EXIT) valid = false;
  }
  k_mutex_lock(&hostMu, K_FOREVER);
  if (valid) {
    if (!servoPending || pendingServo.op != SERVO_TEST_STOP || pkt.op != SERVO_TEST_MOVE) {
      pendingServo = pkt;
      servoPending = true;
    }
    atomic_set(&lastHostRxUs, micros());
  } else {
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

static void applyCalOp(const CalPositionsPacket &request) {
  const CalPacket &pkt = request.command;
  switch (pkt.op) {
    case CAL_ENTER:
      hostMode = HOST_CAL;
      rawNotifyEveryTick = true;
      calIndex = 0xFF;
      calBatch = {};
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
      calBatch = {};
      k_mutex_lock(&motorsMu, K_FOREVER);
      calHeldMask = 0;
      k_mutex_unlock(&motorsMu);
      setTorqueEnable(false);
      break;
    case CAL_CENTER_ONE:
      if (hostMode != HOST_CAL || calIndex != 0xFF || calBatch.command.op ||
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
    case CAL_CENTER_ALL:
    case CAL_SET_POSITIONS:
      if (hostMode != HOST_CAL || calIndex != 0xFF || calBatch.command.op || calHeldMask ||
          servoRxMask != (1u << NUM_MOTORS) - 1u) {
        publishCalReply(pkt.seq, CAL_ALL_JOINTS, false);
        break;
      }
      setTorqueEnable(false);
      calBatch = request;
      calBatchIndex = 0;
      calBatchNextUs = micros() + CAL_BATCH_INTERVAL_US;
      break;
  }
}

static void advanceBatchCalibration() {
  if (!calBatch.command.op) return;
  const uint32_t hostRxUs = (uint32_t)atomic_get(&lastHostRxUs);
  const uint32_t now = micros();
  if (hostMode != HOST_CAL || !atomic_get(&bridgeReady) ||
      (uint32_t)(now - hostRxUs) > HOST_HEARTBEAT_US) {
    calBatch = {};
    return;
  }
  if ((int32_t)(now - calBatchNextUs) < 0) return;
  if (calBatchIndex == NUM_MOTORS) {
    publishCalReply(calBatch.command.seq, CAL_ALL_JOINTS, true);
    calBatch = {};
    return;
  }
  if (!servoCalibrateChecked(MOTOR_IDS[calBatchIndex], calBatch.command.op == CAL_SET_POSITIONS,
                             calBatch.positions[calBatchIndex])) {
    publishCalReply(calBatch.command.seq, CAL_ALL_JOINTS, false);
    calBatch = {};
    return;
  }
  ++calBatchIndex;
  calBatchNextUs = micros() + CAL_BATCH_INTERVAL_US;
}

static void advanceCalibration() {
  advanceBatchCalibration();
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

constexpr uint32_t SERVO_TEST_KEEPALIVE_US = 850000;
constexpr uint8_t SERVO_TEST_IDLE_S = 10;
constexpr uint32_t SERVO_TEST_IDLE_US = SERVO_TEST_IDLE_S * 1000000u;
constexpr uint8_t SERVO_TEST_MAX_MISSED_FRAMES = 10;
struct ServoTest {
  uint8_t id = 0xFF;
  uint8_t missedFeedback = 0;
  uint16_t session = 0, anchor = 0, position = 0, goal = 0;
  uint32_t keepaliveDeadlineUs = 0, angleDeadlineUs = 0;
};
static ServoTest servoTest;

enum class ServoTestStop { None, FeedbackLost, Idle };
struct StoppedServoTest {
  uint16_t session = 0;
  uint8_t id = 0xFF;
  ServoTestStop reason = ServoTestStop::None;
};
static StoppedServoTest stoppedServoTest;

static bool unloadTestServo(uint8_t id) {
  for (unsigned attempt = 0; attempt < 3; ++attempt) {
    scsWriteByte(id, ADDR_TORQUE, 0);
    Serial1.flush();
    k_msleep(1); // Allow a write status packet to finish before reading the register.
    uint8_t torque = 0;
    if (readServoTorque(id, torque) && torque == 0) return true;
  }
  return false;
}

static bool stopServoTest(ServoTestStop reason = ServoTestStop::None) {
  if (!servoTest.session) return false;
  const uint8_t id = servoTest.id;
  stoppedServoTest = {servoTest.session, id, reason};
  servoTest = {};
  return unloadTestServo(id);
}

static void checkServoTestFeedback(uint16_t mask) {
  if (!servoTest.session) return;
  if (mask & (1u << motorIndexById(servoTest.id))) {
    servoTest.missedFeedback = 0;
  } else if (++servoTest.missedFeedback > SERVO_TEST_MAX_MISSED_FRAMES) {
    stopServoTest(ServoTestStop::FeedbackLost);
  }
}

static void expireServoTest() {
  if (!servoTest.session) return;
  const uint32_t now = micros();
  if (hostMode != HOST_SERVO_DEBUG || !atomic_get(&bridgeReady) ||
      (int32_t)(now - servoTest.keepaliveDeadlineUs) >= 0) {
    stopServoTest();
  } else if ((int32_t)(now - servoTest.angleDeadlineUs) >= 0) {
    stopServoTest(ServoTestStop::Idle);
  }
}

static void advanceServoTest() {
  expireServoTest();
  if (!servoTest.session || servoTest.missedFeedback) return;
  // Limit each 10 ms position update to two encoder counts.
  int32_t step = (int32_t)servoTest.goal - servoTest.position;
  if (!step) return;
  if (step > 2) step = 2;
  if (step < -2) step = -2;
  servoTest.position += step;
  writeSingleGoalRaw(servoTest.id, servoTest.position);
  Serial1.flush();
}

static bool applyServoTest(const ServoPacket &pkt, ServoReplyPacket &reply) {
  if (pkt.op == SERVO_TEST_STOP) {
    if (hostMode != HOST_SERVO_DEBUG || servoMidId != 0xFF) return false;
    if (servoTest.session == pkt.testSession && servoTest.id == pkt.targetId) {
      reply.testTorqueOff = stopServoTest();
    } else if (!servoTest.session && stoppedServoTest.session == pkt.testSession &&
               stoppedServoTest.id == pkt.targetId) {
      reply.testTorqueOff = unloadTestServo(pkt.targetId);
    }
    return reply.testTorqueOff;
  }
  expireServoTest();
  const bool same = servoTest.session == pkt.testSession && servoTest.id == pkt.targetId;
  if (hostMode != HOST_SERVO_DEBUG || servoMidId != 0xFF || !atomic_get(&bridgeReady)) return false;
  bool renewAngleDeadline = true;
  if (pkt.op == SERVO_TEST_START) {
    uint16_t position = 0;
    if (servoTest.session ||
        !readServoPresent(pkt.targetId, position, true, SETUP_READ_ATTEMPTS)) return false;
    stoppedServoTest = {};
    reply.testFeedbackLimit = SERVO_TEST_MAX_MISSED_FRAMES;
    reply.testOffsetLimitDeg = SERVO_TEST_OFFSET_DEG;
    reply.testIdleTimeoutSec = SERVO_TEST_IDLE_S;
    servoTest.id = pkt.targetId;
    servoTest.session = pkt.testSession;
    servoTest.anchor = servoTest.position = servoTest.goal = position;
    writeSingleGoalRaw(pkt.targetId, position);
    scsWriteByte(pkt.targetId, ADDR_TORQUE, 1);
    Serial1.flush();
  } else {
    if (!same) {
      if (stoppedServoTest.session == pkt.testSession && stoppedServoTest.id == pkt.targetId) {
        if (stoppedServoTest.reason == ServoTestStop::FeedbackLost)
          reply.testFeedbackLimit = SERVO_TEST_MAX_MISSED_FRAMES;
        if (stoppedServoTest.reason == ServoTestStop::Idle)
          reply.testIdleTimeoutSec = SERVO_TEST_IDLE_S;
      }
      return false;
    }
    const int32_t offset = servoTestOffsetDeg(pkt.testOffset);
    const uint16_t goal = satPos(servoTest.anchor + (int32_t)(offset / M_A * M_N));
    renewAngleDeadline = goal != servoTest.goal;
    servoTest.goal = goal;
  }
  const uint32_t now = micros();
  servoTest.keepaliveDeadlineUs = now + SERVO_TEST_KEEPALIVE_US;
  if (renewAngleDeadline) servoTest.angleDeadlineUs = now + SERVO_TEST_IDLE_US;
  reply.rawPos = servoTest.position;
  return true;
}

static void applyServoOp(const ServoPacket &pkt, bool torqueEnabled) {
  ServoReplyPacket reply = {};
  memcpy(reply.magic, "DQS3", 4);
  reply.op = pkt.op;
  reply.targetId = pkt.targetId;
  reply.centerPos = SERVO_CENTER_POS;
  reply.ok = 1;
  if (pkt.op >= SERVO_TEST_START && pkt.op <= SERVO_TEST_STOP) {
    reply.testSession = pkt.testSession;
    reply.ok = applyServoTest(pkt, reply);
    publishServoReply(reply);
    return;
  }
  if ((servoMidId != 0xFF || servoTest.session) && pkt.op != SERVO_ENTER && pkt.op != SERVO_EXIT) {
    reply.ok = 0;
    publishServoReply(reply);
    return;
  }
  switch (pkt.op) {
    case SERVO_ENTER:
      hostMode = HOST_SERVO_DEBUG;
      rawNotifyEveryTick = true;
      servoMidId = 0xFF;
      servoTest = {};
      setTorqueEnable(false);
      break;
    case SERVO_EXIT:
      if (hostMode != HOST_SERVO_DEBUG) break;
      hostMode = HOST_NORMAL;
      rawNotifyEveryTick = false;
      servoMidId = 0xFF;
      servoTest = {};
      setTorqueEnable(false);
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
    case SERVO_SET_PERM_KP_KD: {
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoSetKpKd(pkt.targetId, pkt.kp, pkt.kd);
      uint8_t savedKp = 0, savedKd = 0, liveKp = 0, liveKd = 0;
      const bool savedOk = readServoGains(pkt.targetId, savedKp, savedKd);
      const bool liveOk = readServoGains(pkt.targetId, liveKp, liveKd, ADDR_TEMP_KP);
      reply.ok = savedOk && liveOk && savedKp == pkt.kp && savedKd == pkt.kd &&
                 liveKp == pkt.kp && liveKd == pkt.kd;
      reply.kp = liveKp; reply.kd = liveKd;
      break;
    }
    case SERVO_SET_PROTECTION:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoSetProtection(pkt.targetId, pkt.protectionFlags);
      reply.protectionFlags = pkt.protectionFlags;
      break;
    case SERVO_SET_PROTECTION_CURRENT:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      servoSetProtectionCurrent(pkt.targetId, pkt.protectionCurrent);
      reply.protectionCurrent = pkt.protectionCurrent;
      break;
    case SERVO_INIT_ID_PROTECTION: {
      uint8_t flags = 0;
      uint16_t current = 0;
      reply.ok = hostMode == HOST_SERVO_DEBUG && !torqueEnabled &&
                 initServoIdProtection(pkt.targetId, flags, current);
      reply.protectionFlags = flags;
      reply.protectionCurrent = current;
      break;
    }
    case SERVO_READ_PROTECTION:
    case SERVO_SET_PROTECTION_SWITCH: {
      const bool idleRead = pkt.op == SERVO_READ_PROTECTION && hostMode == HOST_NORMAL && !torqueEnabled;
      if (hostMode != HOST_SERVO_DEBUG && !idleRead) { reply.ok = 0; break; }
      uint8_t flags = 0;
      uint16_t current = 0;
      const unsigned attempts = hostMode == HOST_SERVO_DEBUG && pkt.op == SERVO_READ_PROTECTION
                                  ? SETUP_READ_ATTEMPTS : 1;
      reply.ok = readServoProtection(pkt.targetId, flags, current, attempts);
      if (reply.ok && pkt.op == SERVO_SET_PROTECTION_SWITCH) {
        const uint8_t wanted = (flags & ~pkt.protectionMask) | pkt.protectionFlags;
        if (wanted != flags) {
          servoSetProtection(pkt.targetId, wanted);
          k_msleep(10); // Let the last EEPROM write finish before reading it back.
          reply.ok = readServoProtection(pkt.targetId, flags, current) && flags == wanted;
        }
      }
      reply.protectionFlags = flags;
      reply.protectionCurrent = current;
      break;
    }
    case SERVO_PING:
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      reply.ok = pingServo(pkt.targetId) ? 1 : 0;
      break;
    case SERVO_READ: {
      if (hostMode != HOST_SERVO_DEBUG) { reply.ok = 0; break; }
      uint16_t pos = 0;
      uint8_t kp = 0, kd = 0;
      reply.ok = readServoPresent(pkt.targetId, pos, false, SETUP_READ_ATTEMPTS) &&
                 readServoGains(pkt.targetId, kp, kd, ADDR_PERM_KP_KD, SETUP_READ_ATTEMPTS);
      reply.rawPos = pos;
      reply.kp = kp; reply.kd = kd;
      break;
    }
    case SERVO_READ_RUN_GAINS: {
      if (hostMode == HOST_CAL) { reply.ok = 0; break; }
      uint8_t kp = 0, kd = 0;
      reply.ok = readServoGains(pkt.targetId, kp, kd, ADDR_TEMP_KP,
                                hostMode == HOST_SERVO_DEBUG ? SETUP_READ_ATTEMPTS : 1);
      reply.kp = kp; reply.kd = kd;
      break;
    }
    default:
      reply.ok = 0;
      break;
  }
  publishServoReply(reply);
}

static void serviceServoOperation(const ServoPacket *packet, bool torqueEnabled) {
  if (packet && packet->op == SERVO_TEST_STOP) {
    applyServoOp(*packet, torqueEnabled);
    return;
  }
  advanceServoTest();
  if (packet) applyServoOp(*packet, torqueEnabled);
}

static void controlTask(void*, void*, void*) {
  Serial1.begin(SERVO_BAUD);
  enableUartFifo(reinterpret_cast<USART_TypeDef*>(DT_REG_ADDR(DT_PHANDLE_BY_IDX(DT_PATH(zephyr_user), serials, 0))));
  for (unsigned i=0; i<3; ++i) { setTorqueEnable(false); k_msleep(5); }
  // Preserve each servo's existing KP/KD, including the mouth.
  // Read temporary KP/KD once at boot (address 50, 2 bytes). Missing IDs stay unset.
  readAllRegisters(ADDR_TEMP_KP, 2); Serial1.flush();
  const uint32_t pdStart=micros();
  while ((uint32_t)(micros()-pdStart)<20000) { parseServoRx(); k_sleep(K_USEC(100)); }
  uint32_t deadline = micros(), previous = deadline, seq = 0, lastCommandUs = 0;
  uint32_t acceptedSeq = 0, overruns = 0;
  uint32_t servoWrites = 0, servoReads = 0, seenZeroResets = 0;
  bool enabled = false, timedOut = false, haveCommand = false;
  bool imuNeedsDisarm = false;
  for (;;) {
    const uint32_t start = micros(), period = start - previous;
    previous = start;
    ImuSnapshot sampledImu = readImuSnapshot();
    const bool imuFresh = sampledImu.sample.ok && sampledImu.seq &&
                         (uint32_t)(start - sampledImu.sampleUs) <= IMU_STALE_US;

    CalPositionsPacket calPkt;
    ServoPacket servoPkt;
    bool gotCal = false, gotServo = false;
    k_mutex_lock(&hostMu, K_FOREVER);
    if (calPending) { calPkt = pendingCal; calPending = false; gotCal = true; }
    if (servoPending) { servoPkt = pendingServo; servoPending = false; gotServo = true; }
    k_mutex_unlock(&hostMu);
    if (sampledImu.zeroResets != seenZeroResets) {
      seenZeroResets = sampledImu.zeroResets;
      imuNeedsDisarm = true;
      enabled = false;
      timedOut = true;
      // Also unload individual joints held by calibration or an installation test.
      hostMode = HOST_NORMAL;
      rawNotifyEveryTick = false;
      calBatch = {};
      calIndex = servoMidId = 0xFF;
      servoTest = {};
      k_mutex_lock(&motorsMu, K_FOREVER);
      calHeldMask = 0;
      k_mutex_unlock(&motorsMu);
      setTorqueEnable(false);
    }
    if (imuNeedsDisarm && gotCal && calPkt.command.op == CAL_CENTER_ONE) {
      publishCalReply(calPkt.command.seq, calPkt.command.index, false);
      gotCal = false;
    }
    if (imuNeedsDisarm && gotServo && servoPkt.op == SERVO_TEST_START) {
      ServoReplyPacket reply = {};
      memcpy(reply.magic, "DQS3", 4);
      reply.op = servoPkt.op;
      reply.targetId = servoPkt.targetId;
      reply.testSession = servoPkt.testSession;
      publishServoReply(reply);
      gotServo = false;
    }
    if (gotCal) applyCalOp(calPkt);
    serviceServoOperation(gotServo ? &servoPkt : nullptr, enabled);
    advanceCalibration();
    advanceServoMid();

    CommandPacket cmd;
    k_mutex_lock(&commandMu, K_FOREVER);
    bool got = pending;
    if (got) { cmd = latest; pending = false; }
    const uint32_t bad = invalidCommands;
    k_mutex_unlock(&commandMu);
    if (imuNeedsDisarm && got) {
      // Observe a fresh unloaded host command after valid IMU samples return.
      // An ARM retry from before the fault must never re-enable the servos.
      if (cmd.mode == DISARM && imuFresh && (uint32_t)(start - cmd.sourceUs) <= MAX_SOURCE_AGE_US) {
        imuNeedsDisarm = false;
      } else if (cmd.mode == ARM) {
        got = false;
      }
    }

    // Servo operations above can sleep while Bridge receives a newer heartbeat.
    // Read its timestamp before sampling "now" to avoid unsigned underflow.
    const uint32_t hostRxUs = (uint32_t)atomic_get(&lastHostRxUs);
    const uint32_t hostCheckUs = micros();
    if (hostMode != HOST_NORMAL &&
        (!bridgeSessionReady() ||
         (uint32_t)(hostCheckUs - hostRxUs) > HOST_HEARTBEAT_US)) {
      // Exclusive modes retain their separate loss-of-host protection.
      hostMode = HOST_NORMAL;
      rawNotifyEveryTick = false;
      calIndex = servoMidId = 0xFF;
      servoTest = {};
      k_mutex_lock(&motorsMu, K_FOREVER);
      calHeldMask = 0;
      k_mutex_unlock(&motorsMu);
      enabled = false; timedOut = true;
      setTorqueEnable(false);
    }
    const bool busTaken = hostMode != HOST_NORMAL;
    if (got && !busTaken) {
      const bool fresh = cmd.mode == DISARM || (bridgeSessionReady() && (uint32_t)(start - cmd.sourceUs) <= MAX_SOURCE_AGE_US);
      const bool ordered = !haveCommand || cmd.mode == DISARM || (int32_t)(cmd.seq - acceptedSeq) > 0;
      if (fresh && ordered) {
        acceptedSeq = cmd.seq; haveCommand = true; lastCommandUs = start;
        if (cmd.mode == DISARM) { enabled = false; timedOut = false; }
        if (cmd.mode == ARM && !enabled && atomic_get(&bridgeReady) && imuFresh &&
            (uint32_t)(start-sampledImu.sampleUs) < 50000) {
          enabled = true; timedOut = false;
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
    } else if (enabled && (!imuFresh || !bridgeSessionReady())) {
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
    beginStateRead();
    memset(servoVoltage, 0, sizeof(servoVoltage));
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
    collectingStateReplies = false;
    checkServoTestFeedback(servoRxMask);
    sampledImu = readImuSnapshot();
    const uint32_t sampleUs = micros();
    const bool publishImuOk = sampledImu.sample.ok && sampledImu.seq &&
                             (uint32_t)(sampleUs - sampledImu.sampleUs) <= IMU_STALE_US;
    StatePacket s = {};
    memcpy(s.magic, "DQS2", 4); s.version = 2;
    s.flags = (publishImuOk ? 1 : 0) | (enabled ? 2 : 0) | (timedOut ? 4 : 0) |
              ((hostMode == HOST_CAL) ? 8 : 0) | ((hostMode == HOST_SERVO_DEBUG) ? 16 : 0);
    s.servoMask = servoRxMask; s.seq = ++seq; s.sampleUs = sampleUs; s.imuUs = sampledImu.sampleUs;
    s.commandSeq = acceptedSeq; s.commandAgeUs = haveCommand ? start-lastCommandUs : UINT32_MAX;
    s.periodUs = period; s.overruns = overruns; s.invalidCommands = bad;
    memcpy(s.acc, sampledImu.sample.accRaw, sizeof(s.acc));
    memcpy(s.gyro, sampledImu.sample.gyroRaw, sizeof(s.gyro));
    VoltagePacket voltage = {};
    memcpy(voltage.magic, "DQV1", 4);
    voltage.seq = s.seq; voltage.sampleUs = sampleUs;
    k_mutex_lock(&motorsMu, K_FOREVER);
    for (unsigned i = 0; i < NUM_MOTORS; ++i) {
      s.angle[i] = motors[i].fbAngleDeg; s.velocity[i] = motors[i].fbVelDeg;
      s.target[i] = motors[i].desAngleDeg;
      voltage.deciV[i] = motors[i].fbVoltageDeciV;
      if (voltage.deciV[i] && (uint32_t)(sampleUs-motors[i].fbVoltageUs) <= VOLTAGE_STALE_US)
        voltage.servoMask |= 1u << i;
    }
    k_mutex_unlock(&motorsMu);
    s.workUs = micros()-start;
    RawPacket raw;
    fillRawPacket(raw);
    DiagnosticPacket diagnostic = {};
    memcpy(diagnostic.magic, "DQD2", 4);
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
    diagnostic.servoBusVoltageMv = servoBusVoltageMv();
    k_mutex_lock(&stateMu, K_FOREVER);
    if (k_sem_count_get(&reportReady)) ++reportDrops;
    s.reportDrops = reportDrops; snapshot = s;
    replyDetailSnapshot = servoReplyDetail;
    sendReplyDetail = abnormalStateReply || s.servoMask != 0x7FFFu;
    rawSnapshot = raw; rawSnapshotEveryTick = rawNotifyEveryTick;
    diagnosticSnapshot = diagnostic;
    voltageSnapshot = voltage;
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
  bytes.reserve(sizeof(StatePacket) + sizeof(ServoReplyDetail));
  for (;;) {
    k_sem_take(&reportReady, K_FOREVER);
    RawPacket raw;
    DiagnosticPacket diagnostic;
    VoltagePacket voltage;
    bool rawEveryTick;
    k_mutex_lock(&stateMu, K_FOREVER);
    size_t detailSize = 0;
    if (sendReplyDetail) {
      detailSize = replyDetailSnapshot.invalidPositionMask ? sizeof(ServoReplyDetail)
                                                           : offsetof(ServoReplyDetail, invalidPositionMask);
    }
    bytes.resize(sizeof(snapshot) + detailSize);
    memcpy(bytes.data(), &snapshot, sizeof(snapshot));
    if (detailSize) memcpy(bytes.data() + sizeof(snapshot), &replyDetailSnapshot, detailSize);
    raw = rawSnapshot; diagnostic = diagnosticSnapshot;
    voltage = voltageSnapshot;
    rawEveryTick = rawSnapshotEveryTick;
    k_mutex_unlock(&stateMu);
    if (!bridgeSessionReady()) continue;
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
      MsgPack::bin_t<uint8_t> vb;
      vb.resize(sizeof(voltage));
      memcpy(vb.data(), &voltage, sizeof(voltage));
      if (!Bridge.tryNotify("duck_voltage", vb)) atomic_inc(&notifyDrops);
      MsgPack::bin_t<uint8_t> pd;
      pd.resize(32);
      pd[0]=servoPdMask & 255; pd[1]=servoPdMask >> 8;
      memcpy(pd.data()+2, servoBootP, 15); memcpy(pd.data()+17, servoBootD, 15);
      if (!Bridge.tryNotify("duck_boot_pd", pd)) atomic_inc(&notifyDrops);
    }
  }
}

// Reassert the MCU routes with the reliable Router, which accepts a repeat bind from the owner.
static bool routeRegistered(const char *name) {
  bool registered = false;
  const MsgPack::str_t route(name);
  auto &&rpc = Bridge.call(BIND_METHOD, route);
  return rpc.result(registered) && registered;
}

static void bridgeTask(void*, void*, void*) {
  atomic_set(&bridgeStage, 1);
  atomic_inc(&bridgeAttempts);
  Bridge.begin(BRIDGE_MAX_BAUD, BridgeProtocol::Reliable);
  auto *link = Bridge.getLinkTransport();
  atomic_set(&bridgeStage, 2);
  auto provideRoute = [](const char *name, auto handler) {
    while (!Bridge.provide(name, handler)) {
      atomic_inc(&bridgeFailures);
      k_msleep(250);
    }
  };
  provideRoute("duck_command", receiveCommand);
  provideRoute("duck_cal", receiveCal);
  provideRoute("duck_servo", receiveServo);
  provideRoute("duck_tof", readTof);
  provideRoute("duck_show_ip", receiveStartupIp);

  // RouterBridge owns UART setup, retries and session resets. Only bind each handler
  // once: a new session restores its registrations in the library update thread.
  for (;;) {
    uint32_t session = link->session();
    const bool routesReady = link->connected() &&
        routeRegistered("duck_command") && routeRegistered("duck_cal") &&
        routeRegistered("duck_servo") && routeRegistered("duck_tof") &&
        routeRegistered("duck_show_ip") && link->session() == session;
    if (routesReady) {
      atomic_set(&registeredSession, session);
      if (!atomic_get(&bridgeReady)) atomic_set(&bridgeReadyUs, micros());
      atomic_set(&bridgeStage, 3);
      atomic_set(&bridgeReady, 1);
    }
    if (!routesReady || link->session() != session) {
      atomic_inc(&bridgeFailures);
    }
    // Invalidate queued motion promptly on a session change, even between probes.
    for (int i = 0; routesReady && i < 20; ++i) {
      if (!link->connected() || link->session() != session) break;
      k_msleep(50);
    }
    if (!routesReady || !link->connected() || link->session() != session) {
      if (atomic_get(&bridgeReady)) atomic_inc(&bridgeReconnects);
      atomic_clear(&bridgeReady);
      atomic_set(&bridgeStage, 4);
      k_mutex_lock(&commandMu, K_FOREVER);
      if (latest.mode != DISARM) pending = false;
      k_mutex_unlock(&commandMu);
      k_msleep(250);
    }
  }
}

void setup() {
  k_mutex_init(&motorsMu);
  k_mutex_init(&commandMu);
  k_mutex_init(&stateMu);
  k_mutex_init(&hostMu);
  k_mutex_init(&imuMu);
  k_sem_init(&reportReady, 0, 1);
  startTof();
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
void loop() {
  serviceStartupIpDisplay();
  delay(100);
}
