"""ToF validity/staleness and actual chunked I2C transport failure checks."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('read_tof', ROOT / 'tools/read-tof.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)
COMPILER = shutil.which('c++') or shutil.which('clang++') or shutil.which('g++')


def packet(state=3, seq=4, age=50, distances=None, statuses=None, targets=None):
    return reader.PACKET.pack(b'DTF1', 1, 8, 8, state, 0x29, 0xf0, 2, 0,
                              seq, 1234, age, 45000, 0, 0,
                              *(distances or list(range(100, 164))),
                              *(statuses or [5]*64), *(targets or [1]*64))


class TofSnapshotTest(unittest.TestCase):
    def test_real_zone_order_center_and_status_filter(self):
        d, s, n = list(range(100, 164)), [5]*64, [1]*64
        s[0], s[1], s[2], n[3], d[4] = 9, 6, 255, 0, -1
        result = reader.decode(packet(distances=d, statuses=s, targets=n))
        self.assertTrue(result['fresh'])
        self.assertEqual(result['valid_zones'], 60)
        self.assertEqual(result['min_mm'], 100)
        self.assertEqual(result['center_mm'], 131.5)
        self.assertEqual(result['grid_mm'][0], [107,106,105,None,None,None,None,100])
        self.assertEqual(result['distance_mm'][4], -1)  # Keep raw diagnostics.

    def test_old_or_uninitialized_or_failed_frames_are_never_distances(self):
        for state, seq, age in [(3,4,1001),(3,0,0),(2,0,0xffffffff),(4,4,50),(1,0,0),(5,4,50)]:
            with self.subTest(state=state, seq=seq, age=age):
                data = reader.decode(packet(state=state, seq=seq, age=age))
                self.assertFalse(data['fresh'])
                self.assertEqual(data['valid_zones'], 0)
                self.assertIsNone(data['center_mm'])
                self.assertIsNone(data['min_mm'])

    def test_malformed_or_future_packets_fail_closed(self):
        good = packet()
        for data in [None, list(good), good[:-1], good+b'\0', b'BAD!'+good[4:]]:
            with self.assertRaises(ValueError):
                reader.decode(data)
        for offset, value in [(4,2),(5,4),(6,4),(7,6),(8,0x52),(11,1)]:
            bad = bytearray(good); bad[offset] = value
            with self.assertRaises(ValueError):
                reader.decode(bad)

    @unittest.skipUnless(COMPILER, 'C++ compiler required for actual optional sensor task check')
    def test_actual_sensor_task_absent_unplug_short_read_and_no_frames_exit_locally(self):
        program = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <vector>
#include <string>
// Substitute hardware operations; execute the unmodified product task itself.
#define __SparkFun_VL53L5CX_Library__
struct {
  bool attached = false, failBegin = false, failRead = false, noFrames = false;
  unsigned polls = 0, begins = 0, readIndex = 0, priority = 0;
  uint32_t now = 10;
} sim;
struct TwoWire {
  void begin() {}
  void setClock(unsigned) {}
  void beginTransmission(uint8_t) { sim.readIndex = 0; }
  void write(uint8_t) {}
  uint8_t endTransmission(bool = true) { return sim.attached ? 0 : 1; }
  size_t requestFrom(uint8_t, size_t n) { return sim.attached ? n : 0; }
  int read() { return sim.readIndex++ ? 2 : 0xf0; }
} Wire1;
enum class SF_VL53L5CX_ERROR_TYPE { NO_ERROR, I2C_ERROR };
struct VL53L5CX_ResultsData {
  int16_t distance_mm[64]; uint8_t target_status[64], nb_target_detected[64];
};
struct SparkFun_VL53L5CX {
  struct {
    SF_VL53L5CX_ERROR_TYPE lastErrorCode = SF_VL53L5CX_ERROR_TYPE::NO_ERROR;
    uint32_t lastErrorValue = 0;
  } lastError;
  bool begin(uint8_t, TwoWire&) { ++sim.begins; return !sim.failBegin; }
  bool setResolution(unsigned) { return true; }
  bool setRangingFrequency(unsigned) { return true; }
  unsigned getResolution() { return 64; }
  unsigned getRangingFrequency() { return 5; }
  bool startRanging() { return true; }
  bool isDataReady() {
    if (sim.noFrames) return false;
    if (++sim.polls == 1) return true;
    lastError.lastErrorCode = SF_VL53L5CX_ERROR_TYPE::I2C_ERROR;
    return false;
  }
  bool getRangingData(VL53L5CX_ResultsData* r) {
    if (sim.failRead) return false;
    for (unsigned i=0;i<64;++i) {
      r->distance_mm[i]=440; r->target_status[i]=5; r->nb_target_detected[i]=1;
    }
    return true;
  }
};
struct k_mutex {};
struct k_thread {};
#define K_FOREVER 0
#define K_NO_WAIT 0
#define K_THREAD_STACK_DEFINE(name,size) static uint8_t name[size]
#define K_THREAD_STACK_SIZEOF(name) sizeof(name)
static void k_mutex_init(k_mutex*) {}
static void k_mutex_lock(k_mutex*,int) {}
static void k_mutex_unlock(k_mutex*) {}
static void k_msleep(uint32_t n) { sim.now += n; }
static void k_thread_create(k_thread*,void*,size_t,void(*)(void*,void*,void*),
                            void*,void*,void*,unsigned priority,int,int) { sim.priority=priority; }
static void k_thread_name_set(k_thread*,const char*) {}
static uint32_t millis() { return sim.now; }
static uint32_t micros() { return sim.now*1000; }
namespace MsgPack { template<class T> using bin_t = std::vector<T>; }
static struct {
  std::string route;
  void provide(const char* name, MsgPack::bin_t<uint8_t>(*)()) { route=name; }
} Bridge;
#include "tof_sensor.h"
static void reset() {
  sim = {}; sim.now = 10; tofSnapshot = {}; tofSensor = {};
  startTof(); assert(sim.priority==8);
  // The reconnecting Bridge task registers this independently of sensor startup.
  Bridge.provide("duck_tof", readTof); assert(Bridge.route=="duck_tof");
}
int main() {
  reset(); tofTask(nullptr,nullptr,nullptr);
  assert(tofSnapshot.state==TOF_ABSENT && sim.begins==0 && tofSnapshot.seq==0);
  assert(sim.now==10); // No sensor: no initialization or long retry loop.
  auto packet=readTof(); assert(packet.size()==292);
  TofPacket absent; memcpy(&absent,packet.data(),sizeof(absent));
  assert(absent.ageMs==UINT32_MAX && memcmp(absent.magic,"DTF1",4)==0);
  reset(); sim.attached=true; tofTask(nullptr,nullptr,nullptr);
  assert(tofSnapshot.seq==1 && tofSnapshot.distanceMm[27]==440);
  assert(tofSnapshot.state==TOF_OFFLINE && tofSnapshot.errors==1);
  assert((tofSnapshot.lastError>>24)==8); // Unplug after a valid frame.
  assert(sim.now<100);
  reset(); sim.attached=true; sim.failRead=true; tofTask(nullptr,nullptr,nullptr);
  assert(tofSnapshot.state==TOF_OFFLINE && tofSnapshot.seq==0);
  assert((tofSnapshot.lastError>>24)==7); // Partial frame is never published.
  reset(); sim.attached=true; sim.noFrames=true; tofTask(nullptr,nullptr,nullptr);
  assert(tofSnapshot.state==TOF_OFFLINE && sim.now<=5030);
  assert((tofSnapshot.lastError>>24)==9); // Bounded local timeout.
  reset(); sim.attached=true; sim.failBegin=true; tofTask(nullptr,nullptr,nullptr);
  assert(tofSnapshot.state==TOF_ERROR && tofSnapshot.seq==0);
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'check.cpp').write_text(program)
            build = subprocess.run([COMPILER, '-std=c++17', '-Wall', '-Wextra',
                                    '-I', str(ROOT/'sketch'), str(folder/'check.cpp'),
                                    '-o', str(folder/'check')], capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout+build.stderr)
            result = subprocess.run([str(folder/'check')], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    @unittest.skipUnless(COMPILER, 'C++ compiler required for actual I2C transport check')
    def test_actual_driver_chunks_reads_and_propagates_nack_and_short_read(self):
        arduino = '''#pragma once
#include <cstdint>
#include <cstddef>
using byte = uint8_t;
inline uint8_t highByte(uint16_t n) { return n >> 8; }
inline uint8_t lowByte(uint16_t n) { return n & 255; }
'''
        wire = '''#pragma once
#include <vector>
#include <cstdint>
#include <cstddef>
class TwoWire {
public:
  std::vector<uint8_t> tx, rx;
  std::vector<uint16_t> registers;
  unsigned offset = 0, next = 0, fail_at = 0xffffffff;
  bool nack = false;
  void beginTransmission(uint8_t) { tx.clear(); rx.clear(); offset = 0; }
  void write(uint8_t b) { tx.push_back(b); }
  uint8_t endTransmission(bool = true) {
    if (nack) return 2;
    if (tx.size() >= 2) { next = (tx[0]<<8)|tx[1]; registers.push_back(next); }
    return 0;
  }
  size_t requestFrom(uint8_t, size_t count) {
    if (next >= fail_at) --count;
    for (size_t i=0;i<count;++i) rx.push_back((next+i)&255);
    return count;
  }
  int available() { return rx.size()-offset; }
  int read() { return offset < rx.size() ? rx[offset++] : -1; }
};
'''
        program = '''#include <cassert>
#include <cstring>
#include "SparkFun_VL53L5CX_IO.h"
int main() {
  TwoWire bus; SparkFun_VL53L5CX_IO io;
  assert(io.begin(0x29,bus));
  uint8_t result[777] = {};
  assert(io.readMultipleBytes(0x1234,result,sizeof(result)) == 0);
  for (unsigned i=0;i<sizeof(result);++i) assert(result[i] == ((0x1234+i)&255));
  assert(bus.registers.size() == 25);
  assert(bus.registers[1] == 0x1254 && bus.registers.back() == 0x1534);
  bus.nack = true;
  assert(io.readMultipleBytes(0x1234,result,64) == 2);
  bus.nack = false; bus.fail_at = 0x1254;
  memset(result,0xee,sizeof(result));
  assert(io.readMultipleBytes(0x1234,result,64) != 0);
  assert(result[32] == 0xee); // A partial second chunk must not become data.
  bus.fail_at = 0;
  assert(io.readMultipleBytes(0x1234,result,1) != 0);
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder/'Arduino.h').write_text(arduino)
            (folder/'Wire.h').write_text(wire)
            (folder/'check.cpp').write_text(program)
            source = ROOT/'sketch/src/vl53l5cx'
            build = subprocess.run([COMPILER, '-std=c++17', '-Wall', '-Wextra',
                                    '-I', str(folder), '-I', str(source),
                                    str(folder/'check.cpp'), str(source/'SparkFun_VL53L5CX_IO.cpp'),
                                    '-o', str(folder/'check')], capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stdout+build.stderr)
            result = subprocess.run([str(folder/'check')], capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
