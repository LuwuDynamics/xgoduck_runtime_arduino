"""Execute the actual MCU lease state machine on the host, without motors."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = shutil.which('c++') or shutil.which('clang++') or shutil.which('g++')


@unittest.skipUnless(COMPILER, 'C++ compiler required for MCU lease behavioral check')
class ControlLeaseTest(unittest.TestCase):
    def test_actual_mcu_pause_timeout_resume_and_packet_boundaries(self):
        source = r'''
#include <assert.h>
#include "control_lease.h"
CommandPacket packet(uint8_t mode, uint32_t seq, uint32_t at) {
  CommandPacket p = {};
  p.mode = mode; p.seq = seq; p.sourceUs = at; p.ttlUs = COMMAND_TTL_US;
  return p;
}
int main() {
  ControlLease l(true); // Keep the guarded build covered as a rollback path.
  assert(!l.expire(1000000,0)); // Never auto-arm, including absent IMU.
  assert(l.accept(packet(PAUSE,1,0),1000000,false)==ControlLease::KEEP_TARGETS);
  assert(!l.enabled && !l.paused);
  assert(l.accept(packet(ARM,2,1000000),1000000,false)==ControlLease::REJECTED);
  assert(!l.enabled);
  assert(l.accept(packet(ARM,2,1000000),1000000,true)==ControlLease::APPLY_TARGETS);
  assert(l.enabled && !l.paused);
  assert(!l.expire(1250000,1250000)); // TTL boundary is inclusive.
  assert(l.expire(1250001,1250001));
  assert(l.enabled && l.paused); // Timeout retains torque.
  for (uint32_t s=3; s<103; ++s) {
    assert(l.accept(packet(POLICY,s,1300000),1300000,true)==ControlLease::KEEP_TARGETS);
    assert(l.enabled && l.paused); // No late gait replay after reconnection.
  }
  assert(l.accept(packet(HOLD,103,1300000),1300000,true)==ControlLease::KEEP_TARGETS);
  assert(l.accept(packet(ARM,104,1300000),1300000,true)==ControlLease::APPLY_TARGETS);
  assert(l.enabled && !l.paused);
  assert(l.expire(1400001,1300000)); // IMU age 100 ms also freezes, never disarms.
  assert(l.enabled && l.paused);
  assert(l.accept(packet(ARM,105,1400001),1400001,false)==ControlLease::REJECTED);
  assert(l.enabled && l.paused);
  assert(l.accept(packet(DISARM,1,0),9000000,false)==ControlLease::KEEP_TARGETS);
  assert(!l.enabled && !l.paused); // Explicit off works with stale clock/sequence.
  assert(l.accept(packet(POLICY,2,9000000),9000000,true)==ControlLease::KEEP_TARGETS);
  assert(!l.enabled);
  assert(l.accept(packet(ARM,3,9000000),9150000,true)==ControlLease::APPLY_TARGETS);
  assert(l.accept(packet(POLICY,4,9000000),9150001,true)==ControlLease::REJECTED);
  assert(l.accept(packet(PAUSE,4,0),9150001,false)==ControlLease::KEEP_TARGETS);
  assert(l.enabled && l.paused); // Explicit pause ignores age, never target freshness.
  l.takeBus(); assert(!l.enabled && !l.paused);
  ControlLease wrap(true);
  assert(wrap.accept(packet(ARM,0xffffffff,0xfffffff0),0xfffffff0,true)==ControlLease::APPLY_TARGETS);
  assert(wrap.accept(packet(POLICY,0,5),5,true)==ControlLease::APPLY_TARGETS);
  assert(!wrap.expire(10,5));
  assert(wrap.accept(packet(POLICY,0,10),10,true)==ControlLease::REJECTED);
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'check.cpp').write_text(source)
            build = subprocess.run([COMPILER, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-I', str(ROOT / 'sketch'), str(root / 'check.cpp'), '-o', str(root / 'check')],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(root / 'check')], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_diagnostic_build_does_not_expire_but_explicit_pause_off_and_ordering_work(self):
        source = r'''
#include <assert.h>
#include "control_lease.h"
CommandPacket packet(uint8_t mode, uint32_t seq, uint32_t at) {
  CommandPacket p = {};
  p.mode = mode; p.seq = seq; p.sourceUs = at; p.ttlUs = COMMAND_TTL_US;
  return p;
}
int main() {
  static_assert(!MOTION_TIMEOUT_CHECKS_ENABLED, "diagnostic build must disable expiry");
  static_assert(TIMEOUT_CHECKS_DISABLED == 64, "host capability must match");
  ControlLease l;
  assert(!l.expire(9000000,0));
  assert(!l.enabled); // Disabling timers never enables motors on its own.
  assert(l.accept(packet(ARM,1,100),100,false)==ControlLease::REJECTED);
  assert(l.accept(packet(ARM,1,100),100,true)==ControlLease::APPLY_TARGETS);
  assert(!l.expire(250101,0));
  assert(!l.expire(9000000,0)); // Both command and IMU gaps exceed their old timers.
  assert(l.enabled && !l.paused);
  assert(l.accept(packet(POLICY,2,100),9000000,false)==ControlLease::APPLY_TARGETS);
  assert(l.accept(packet(POLICY,2,100),9000001,false)==ControlLease::REJECTED);
  assert(l.accept(packet(POLICY,1,100),9000002,false)==ControlLease::REJECTED);
  assert(l.accept(packet(PAUSE,3,0),9000003,false)==ControlLease::KEEP_TARGETS);
  assert(l.enabled && l.paused);
  assert(l.accept(packet(POLICY,4,100),9000004,true)==ControlLease::KEEP_TARGETS);
  assert(l.accept(packet(ARM,5,9000005),9000005,true)==ControlLease::APPLY_TARGETS);
  assert(l.enabled && !l.paused);
  assert(l.accept(packet(DISARM,1,0),9000006,false)==ControlLease::KEEP_TARGETS);
  assert(!l.enabled && !l.paused);
  assert(l.accept(packet(POLICY,2,0),9000007,true)==ControlLease::KEEP_TARGETS);
  assert(!l.enabled);
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'check.cpp').write_text(source)
            build = subprocess.run([COMPILER, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-I', str(ROOT / 'sketch'), str(root / 'check.cpp'), '-o', str(root / 'check')],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)
            run = subprocess.run([str(root / 'check')], capture_output=True, text=True, timeout=5)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)


if __name__ == '__main__':
    unittest.main()
