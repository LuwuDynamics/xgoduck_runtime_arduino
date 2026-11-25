"""Exercise the reliable MCU route supervisor and session gate without hardware."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <set>
#include <string>
namespace MsgPack { using str_t = std::string; }
#define BIND_METHOD "$/register"
struct Finished {};
enum Scenario { Healthy, MissingRoute, FailedBind, SessionReset, ResetWithDisarm };
Scenario scenario;
enum class BridgeProtocol { Reliable };
constexpr int BRIDGE_MAX_BAUD = 2000000, K_FOREVER = -1, DISARM = 0;
int bridgeReady, bridgeStage, bridgeAttempts, bridgeFailures, bridgeReconnects;
int registeredSession, bridgeReadyUs, commandMu, ticks, retries;
bool pending;
struct { int mode; } latest;
void atomic_clear(int *p) { *p = 0; }
void atomic_set(int *p, int value) { *p = value; }
void atomic_inc(int *p) { ++*p; }
int atomic_get(int *p) { return *p; }
void k_mutex_lock(int *, int) {}
void k_mutex_unlock(int *) {}
uint32_t micros() { return ticks * 50000 + 1; }
void receiveCommand() {}
void receiveCal() {}
void receiveServo() {}
void readTof() {}
bool receiveStartupIp(int, int, int, int) { return true; }
const std::set<std::string> expected = {
    "duck_command", "duck_cal", "duck_servo", "duck_tof", "duck_show_ip"
};
struct Reply {
    bool ok;
    bool result(bool &value) { value = ok; return ok; }
};
struct Router {
    std::set<std::string> methods, handlers;
    uint32_t currentSession = 1;
    bool fail = false;
    void begin(int baud, BridgeProtocol protocol) {
        assert(baud == 2000000 && protocol == BridgeProtocol::Reliable);
    }
    Router *getLinkTransport() { return this; }
    bool connected() { return true; }
    uint32_t session() { return currentSession; }
    template<typename F> bool provide(const std::string &name, F) {
        assert(handlers.insert(name).second);
        methods.insert(name);
        return true;
    }
    Reply call(const std::string &method, const std::string &route) {
        assert(method == BIND_METHOD);
        if (fail && route == "duck_cal") { fail = false; return {false}; }
        methods.insert(route);
        return {true};
    }
} Bridge;
static bool bridgeSessionReady();
void k_msleep(uint32_t ms) {
    if (ms == 250) {
        ++retries;
        assert(!bridgeReady && bridgeStage == 4 && bridgeReconnects == 1);
        assert(pending == (scenario == ResetWithDisarm));
        return;
    }
    assert(ms == 50 && bridgeSessionReady());
    ++ticks;
    if (ticks == 1) {
        assert(Bridge.methods == expected);
        if (scenario == MissingRoute) Bridge.methods.erase("duck_tof");
        if (scenario == FailedBind) Bridge.fail = true;
        if (scenario == SessionReset || scenario == ResetWithDisarm) {
            Bridge.currentSession++;
            Bridge.methods.clear();
            assert(!bridgeSessionReady());
        }
    } else if (ticks == 21) {
        assert(Bridge.methods == expected && Bridge.handlers == expected);
        assert(bridgeReady && bridgeStage == 3 && bridgeAttempts == 1);
        const bool interrupted = scenario == FailedBind || scenario == SessionReset ||
                                 scenario == ResetWithDisarm;
        assert(bridgeReconnects == int(interrupted) && retries == int(interrupted));
        assert(pending == (!interrupted || scenario == ResetWithDisarm));
        throw Finished{};
    }
}
'''

MAIN = r'''
int main() {
    for (Scenario test : {Healthy, MissingRoute, FailedBind, SessionReset, ResetWithDisarm}) {
        scenario = test;
        Bridge = Router{};
        registeredSession = -1;
        bridgeReady = bridgeStage = bridgeAttempts = bridgeFailures = 0;
        bridgeReconnects = bridgeReadyUs = ticks = retries = 0;
        pending = true;
        latest.mode = test == ResetWithDisarm ? DISARM : 1;
        try { bridgeTask(nullptr, nullptr, nullptr); }
        catch (Finished &) { continue; }
        return 2;
    }
}
'''


@unittest.skipUnless(shutil.which('c++'), 'A host C++ compiler is required')
class BridgeRoutesTest(unittest.TestCase):
    def test_session_invalidation_and_route_recovery(self):
        source = (ROOT / 'sketch/sketch.ino').read_text()
        gate = source[source.index('static bool bridgeSessionReady()'):source.index('static void receiveCommand(')]
        supervisor = source[source.index('static bool routeRegistered('):source.index('\nvoid setup()')]
        with tempfile.TemporaryDirectory(prefix='duck-bridge-routes-') as directory:
            path = Path(directory)
            (path / 'test.cpp').write_text(HARNESS + gate + supervisor + MAIN)
            subprocess.run(['c++', '-std=c++17', str(path / 'test.cpp'), '-o', str(path / 'test')], check=True)
            subprocess.run([str(path / 'test')], check=True, timeout=5)


if __name__ == '__main__':
    unittest.main()
