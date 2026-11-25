"""Run the firmware's IMU sampling task against a simulated QMI8658 register bus."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
COMPILER = shutil.which('c++')

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <vector>
#include "protocol.h"

static uint32_t nowUs = 1;
static uint32_t micros() { return nowUs; }
static void delay(unsigned ms) { nowUs += ms * 1000; }
enum Reading { Gravity, GyroOnly, Zero, ReadError };
static std::vector<Reading> inputs;
static unsigned reads = 0, resets = 0;
struct {
    uint8_t reg = 0, registers[256] = {};
    unsigned writeCount = 0;
    void begin() {}
    void setClock(unsigned) {}
    void beginTransmission(uint8_t) { writeCount = 0; }
    void write(uint8_t value) {
        if (writeCount++ == 0) reg = value;
        else {
            registers[reg] = value;
            if (reg == 0x60 && value == 0xb0) {
                ++resets;
                registers[0x4d] = 0x80;
            }
        }
    }
    int endTransmission(bool = true) { return 0; }
    int requestFrom(int, int len) {
        if (reg == 0x35) {
            assert(len == 12 && reads < inputs.size());
            Reading value = inputs[reads++];
            if (value == ReadError) return 0;
            for (int i = 0; i < 12; ++i) registers[reg+i] = 0;
            if (value == Gravity) registers[reg+1] = 0x40;
            if (value == GyroOnly) registers[reg+11] = 1;
        }
        return len;
    }
    uint8_t read() { return registers[reg++]; }
} Wire;

#include "qmi8658.h"
static ImuSnapshot imuSnapshot;
static int imuMu;
static constexpr int K_FOREVER = -1;
static void k_mutex_lock(int*, int) {}
static void k_mutex_unlock(int*) {}
#define K_USEC(us) (us)

struct Snapshot { ImuSnapshot imu; unsigned resets; };
static std::vector<Snapshot> results;
struct Finished {};
static void k_sleep(uint32_t us) {
    nowUs += us;
    if (reads > results.size()) results.push_back({imuSnapshot, resets});
    if (reads == inputs.size()) throw Finished();
    assert(nowUs < 15000000);
}
'''

SCENARIO = r'''
int main() {
    Wire.registers[0] = 0x05;
    inputs.assign(12, Gravity);
    inputs.insert(inputs.end(), 9, Zero);
    inputs.push_back(GyroOnly); // Even just the last byte nonzero breaks the streak.
    inputs.insert(inputs.end(), 10, Zero);
    inputs.insert(inputs.end(), 15, Gravity);
    inputs.insert(inputs.end(), 3, ReadError);
    inputs.insert(inputs.end(), 2, Gravity);
    inputs.push_back(Zero);
    inputs.insert(inputs.end(), 3, Gravity);
    try { imuTask(nullptr, nullptr, nullptr); }
    catch (Finished&) {}

    assert(results.size() == inputs.size());
    const Snapshot baseline = results[11];
    assert(baseline.imu.sample.ok && baseline.resets == 1 && baseline.imu.zeroResets == 0);
    for (unsigned i = 12; i <= 20; ++i) {
        assert(results[i].imu.sample.ok && results[i].resets == 1);
        assert(results[i].imu.sampleUs == baseline.imu.sampleUs);
        assert(results[i].imu.seq == baseline.imu.seq);
    }
    const Snapshot fresh = results[21];
    assert(fresh.imu.sample.ok && fresh.imu.seq > baseline.imu.seq);
    assert(fresh.imu.sampleUs > baseline.imu.sampleUs && fresh.resets == 1);
    for (unsigned i = 22; i <= 30; ++i) {
        assert(results[i].imu.sample.ok && results[i].resets == 1);
        assert(results[i].imu.sampleUs == fresh.imu.sampleUs);
        assert(results[i].imu.seq == fresh.imu.seq);
    }
    assert(!results[31].imu.sample.ok && results[31].imu.seq == fresh.imu.seq);
    assert(results[31].imu.zeroResets == 1);
    assert(!results[32].imu.sample.ok && !results[33].imu.sample.ok);
    assert(results[34].imu.sample.ok && results[34].resets == 2);
    assert(results[46].imu.sample.ok && results[46].resets == 2);
    // An all-zero read during recovery breaks a promising sequence of two good reads.
    for (unsigned i = 49; i <= 54; ++i) assert(!results[i].imu.sample.ok);
    assert(results.back().imu.sample.ok && results.back().resets == 3);
    assert(results.back().imu.zeroResets == 1);
}
'''


CONTROL_HARNESS = r'''
#include <cassert>
#include <cstring>
#include "protocol.h"
uint32_t clockUs = 3000000, seenZeroResets = 0, acceptedSeq = 0, lastCommandUs = 0;
struct { uint32_t sampleUs = 0, zeroResets = 0; } sampledImu;
bool enabled = true, haveCommand = true, timedOut = false, imuNeedsDisarm = false;
HostBusMode hostMode = HOST_NORMAL;
bool rawNotifyEveryTick;
int calHeldMask, servoMidId, calIndex, servoTest, calBatch;
int motorsMu, commandMu, invalidCommands, bridgeReady = 1;
constexpr int K_FOREVER = -1, NUM_MOTORS = 15;
struct { float desAngleDeg; } motors[NUM_MOTORS];
bool gotCal, gotServo;
CalPositionsPacket calPkt;
ServoPacket servoPkt;
int calRejected = 0, servoRejected = 0, unloads = 0;
void k_mutex_lock(int*, int) {}
void k_mutex_unlock(int*) {}
int atomic_get(int* v) { return *v; }
bool bridgeSessionReady() { return bridgeReady != 0; }
void setTorqueEnable(bool on) { assert(!on); ++unloads; }
void publishCalReply(uint32_t, uint8_t, bool ok) { assert(!ok); ++calRejected; }
void publishServoReply(const ServoReplyPacket& reply) { assert(!reply.ok); ++servoRejected; }

void tick(CommandMode mode, uint32_t sequence, bool imuFresh, uint32_t sourceAge = 0) {
    const uint32_t start = clockUs += TICK_US;
    sampledImu.sampleUs = start;
    CommandPacket cmd = {};
    cmd.mode = mode;
    cmd.sourceUs = start - sourceAge;
    cmd.seq = sequence;
    bool got = true;
'''

CONTROL_SCENARIO = r'''
}
int main() {
    // The queued ARM, calibration, and single-joint test coincide with the fault.
    gotCal = gotServo = true;
    calPkt.command.op = CAL_CENTER_ONE;
    servoPkt.op = SERVO_TEST_START;
    calHeldMask = 3;
    hostMode = HOST_SERVO_DEBUG;
    ++sampledImu.zeroResets;
    tick(ARM, 10, false);
    assert(imuNeedsDisarm && !enabled && unloads == 1);
    assert(hostMode == HOST_NORMAL && calHeldMask == 0);
    assert(!gotCal && !gotServo && calRejected == 1 && servoRejected == 1);
    tick(DISARM, 11, false);
    tick(ARM, 12, true);
    tick(HOLD, 13, true);
    tick(POLICY, 14, true);
    assert(imuNeedsDisarm && !enabled);
    tick(DISARM, 15, true, MAX_SOURCE_AGE_US + 1);
    tick(ARM, 16, true);
    assert(imuNeedsDisarm && !enabled);
    tick(DISARM, 17, true);
    assert(!imuNeedsDisarm && !enabled);
    // Reordered old ARM and normally scheduled policy packets cannot resume torque.
    tick(ARM, 16, true);
    tick(POLICY, 18, true);
    assert(!enabled);
    tick(ARM, 19, true);
    assert(enabled);
}
'''


@unittest.skipUnless(COMPILER, 'A host C++ compiler is required')
class ImuRecoveryTest(unittest.TestCase):
    def test_zero_samples_and_i2c_errors_reinitialize_the_sensor(self):
        sketch = (ROOT / 'sketch/sketch.ino').read_text()
        start = sketch.index('static bool imuAxesAreZero(')
        end = sketch.index('\n}\n', sketch.index('static void imuTask(', start)) + 2
        self.run_firmware(HARNESS + sketch[start:end] + SCENARIO)

    def test_recovered_imu_requires_fresh_disarm_then_new_arm(self):
        sketch = (ROOT / 'sketch/sketch.ino').read_text()
        task = sketch[sketch.index('static void controlTask('):]
        fault = task[task.index('    if (sampledImu.zeroResets != seenZeroResets)'):
                     task.index('    if (gotCal) applyCalOp')]
        gate = task[task.index('    if (imuNeedsDisarm && got)'):
                    task.index('    // Servo operations above')]
        motion = task[task.index('    const bool busTaken ='):
                      task.index('    static bool previousEnabled')]
        self.run_firmware(CONTROL_HARNESS + fault + gate + motion + CONTROL_SCENARIO)

    def run_firmware(self, source):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            (work / 'Wire.h').touch()
            program = work / 'imu.cpp'
            program.write_text(source)
            executable = work / 'imu'
            subprocess.run([COMPILER, '-std=c++17', '-I', str(work), '-I', str(ROOT / 'sketch'),
                            str(program), '-o', str(executable)], check=True)
            subprocess.run([str(executable)], check=True, timeout=10, cwd=work)


if __name__ == '__main__':
    unittest.main()
