"""Run the real MCU Bridge supervisor against a Router losing its registry."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which('c++'), 'A host C++ compiler is required')
class BridgeRoutesTest(unittest.TestCase):
    def test_registry_loss_is_repaired_even_when_router_version_still_answers(self):
        source = (ROOT / 'sketch/sketch.ino').read_text()
        start = source.index('static void bridgeTask(')
        supervisor = source[start:source.index('\nvoid setup()', start)]
        harness = r'''
#include <cassert>
#include <set>
#include <string>
#include <stdexcept>
#include <cstdint>
namespace MsgPack { using str_t = std::string; }
struct Finished {};
int bridgeReady, bridgeStage, bridgeAttempts, bridgeFailures, bridgeReconnects;
int bridgeReadyUs, commandMu;
bool pending = true;
constexpr int K_FOREVER = -1, BRIDGE_BAUD = 2000000;
void atomic_clear(int *p) { *p = 0; }
void atomic_set(int *p, int value) { *p = value; }
void atomic_inc(int *p) { ++*p; }
void k_mutex_lock(int *, int) {}
void k_mutex_unlock(int *) {}
uint32_t micros() { return 100; }
void receiveCommand() {}
void receiveCal() {}
void receiveServo() {}
void readTof() {}
int scenario, polls;
struct Router {
    std::set<std::string> methods;
    bool failRegister = false, failVersion = false;
    int invalidations = 0;
    bool begin(int, bool) { methods.clear(); return true; }
    template<typename F> bool provide(const std::string &name, F) {
        if (failRegister && name == "duck_cal") {
            failRegister = false;
            return false;
        }
        methods.insert(name);
        return true;
    }
    bool getRouterVersion(std::string &) {
        if (failVersion) { failVersion = false; return false; }
        return true;
    }
    void invalidate() { ++invalidations; }
} Bridge;
const std::set<std::string> expected = {
    "duck_command", "duck_cal", "duck_servo", "duck_tof"
};
void k_msleep(uint32_t ms) {
    if (ms != 2000) return;
    ++polls;
    if (polls == 1) {
        assert(bridgeReady && Bridge.methods == expected);
        if (scenario == 1) Bridge.methods.erase("duck_tof");
        else Bridge.methods.clear();
        Bridge.failRegister = scenario == 2;
        Bridge.failVersion = scenario == 3;
    } else {
        // A healthy version response is insufficient: all routes must exist.
        if (!bridgeReady || Bridge.methods != expected)
            throw std::runtime_error("Router alive but MCU methods stayed missing");
        if (scenario >= 2) {
            assert(bridgeAttempts == 2 && bridgeFailures == 1);
            assert(bridgeReconnects == 1 && Bridge.invalidations == 1);
            assert(!pending);
        } else {
            assert(bridgeAttempts == 1 && Bridge.invalidations == 0);
        }
        throw Finished{};
    }
}
''' + supervisor + r'''
int main() {
    for (scenario = 0; scenario < 4; ++scenario) {
        bridgeReady = bridgeStage = bridgeAttempts = bridgeFailures = 0;
        bridgeReconnects = bridgeReadyUs = polls = 0;
        pending = true;
        Bridge = Router{};
        try { bridgeTask(nullptr, nullptr, nullptr); }
        catch (Finished &) { continue; }
        return 2;
    }
}
'''
        with tempfile.TemporaryDirectory(prefix='duck-bridge-routes-') as directory:
            path = Path(directory)
            (path / 'test.cpp').write_text(harness)
            compiled = subprocess.run(['c++', '-std=c++17', str(path / 'test.cpp'),
                                       '-o', str(path / 'test')], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(path / 'test')], capture_output=True, text=True,
                                    timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
