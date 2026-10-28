#pragma once
#include "protocol.h"

// The diagnostic build retains torque/targets through command and IMU gaps.
// Explicit PAUSE/DISARM and packet/sequence validation remain available.
struct ControlLease {
  enum Result { REJECTED, KEEP_TARGETS, APPLY_TARGETS };
  bool enabled = false, paused = false, haveCommand = false;
  uint32_t acceptedSeq = 0, lastCommandUs = 0, ttlUs = COMMAND_TTL_US;
  const bool timeoutChecksEnabled;

  explicit ControlLease(bool checks = MOTION_TIMEOUT_CHECKS_ENABLED)
      : timeoutChecksEnabled(checks) {}

  bool expire(uint32_t now, uint32_t lastHealthyUs) {
    if (!timeoutChecksEnabled || !enabled || paused) return false;
    if ((uint32_t)(now - lastCommandUs) <= ttlUs &&
        (uint32_t)(now - lastHealthyUs) <= 100000) return false;
    paused = true;
    return true;
  }

  Result accept(const CommandPacket &cmd, uint32_t now, bool imuReady) {
    const bool safeStop = cmd.mode == DISARM || cmd.mode == PAUSE;
    if ((timeoutChecksEnabled && !safeStop && (uint32_t)(now - cmd.sourceUs) > MAX_SOURCE_AGE_US) ||
        (haveCommand && cmd.mode != DISARM && (int32_t)(cmd.seq - acceptedSeq) <= 0) ||
        (cmd.mode == ARM && !imuReady)) return REJECTED;
    acceptedSeq = cmd.seq;
    lastCommandUs = now;
    ttlUs = cmd.ttlUs;
    haveCommand = true;
    if (cmd.mode == DISARM) {
      enabled = paused = false;
      return KEEP_TARGETS;
    }
    if (cmd.mode == PAUSE) {
      paused = enabled;
      return KEEP_TARGETS;
    }
    if (cmd.mode == ARM) {
      enabled = true;
      paused = false;
      return APPLY_TARGETS;
    }
    return enabled && !paused ? APPLY_TARGETS : KEEP_TARGETS;
  }

  void takeBus() { enabled = paused = false; }
};
