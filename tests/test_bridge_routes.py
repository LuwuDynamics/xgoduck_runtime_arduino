"""Run the real MCU Bridge supervisor against Routers that lose or keep their registry.

Router 0.11.0 treats a second `$/register` from the owner of a route as a no-op.
Routers 0.5.4 through 0.10.x answer it with RouteAlreadyExists (error code 5)
even though the route is intact. The supervisor re-asserts its routes every two
seconds, so it has to accept that answer or it tears the Bridge down forever.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <set>
#include <stdexcept>
#include <string>
namespace MsgPack { using str_t = std::string; }
#define BIND_METHOD "$/register"
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
bool strictRouter;
#define CHECK(condition) do { if (!(condition)) { \
    std::fprintf(stderr, "strict_router=%d scenario=%d: %s\n", \
                 strictRouter, scenario, #condition); std::exit(3); } } while (0)
// Stands for RpcCall: result() is false for any error response or timeout.
struct RouterCall {
    bool ok;
    int code;
    bool result(bool &value) { value = ok; return ok; }
    int getErrorCode() { return code; }
};
struct Router {
    std::set<std::string> methods;
    bool failRegister = false, failVersion = false;
    int invalidations = 0;
    // One implementation behind provide() and call("$/register"), like the real Router.
    RouterCall registerRoute(const std::string &name) {
        if (failRegister && name == "duck_cal") {
            failRegister = false;
            return {false, 0xFF};  // RPClite GENERIC_ERR, e.g. a timed-out reply
        }
        if (methods.count(name)) {
            if (strictRouter) return {false, 5};  // ErrCodeRouteAlreadyExists
            return {true, 0};
        }
        methods.insert(name);
        return {true, 0};
    }
    bool begin(int, bool) { methods.clear(); return true; }
    template<typename F> bool provide(const std::string &name, F) {
        bool registered = false;
        return registerRoute(name).result(registered);
    }
    RouterCall call(const std::string &method, const std::string &route) {
        CHECK(method == BIND_METHOD);
        return registerRoute(route);
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
        CHECK(bridgeReady && Bridge.methods == expected);
        if (scenario == 1) Bridge.methods.erase("duck_tof");
        else if (scenario != 4) Bridge.methods.clear();  // scenario 4 keeps the registry intact
        Bridge.failRegister = scenario == 2;
        Bridge.failVersion = scenario == 3;
    } else {
        // A healthy version response is insufficient: all routes must exist.
        if (!bridgeReady || Bridge.methods != expected)
            throw std::runtime_error("Router alive but MCU methods stayed missing");
        if (scenario == 2 || scenario == 3) {
            CHECK(bridgeAttempts == 2 && bridgeFailures == 1);
            CHECK(bridgeReconnects == 1 && Bridge.invalidations == 1);
            CHECK(!pending);
        } else {
            // Healthy links must never be torn down by the supervisor.
            CHECK(bridgeAttempts == 1 && bridgeFailures == 0);
            CHECK(bridgeReconnects == 0 && Bridge.invalidations == 0);
            CHECK(pending);
        }
        throw Finished{};
    }
}
'''

MAIN = r'''
int main() {
    strictRouter = STRICT_ROUTER;
    for (scenario = 0; scenario < 5; ++scenario) {
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


@unittest.skipUnless(shutil.which('c++'), 'A host C++ compiler is required')
class BridgeRoutesTest(unittest.TestCase):
    def run_supervisor(self, strict_router):
        source = (ROOT / 'sketch/sketch.ino').read_text()
        end = source.index('\nvoid setup()')
        # The route re-assertion helper, when present, sits right above the supervisor.
        start = min(index for index in (source.find('static bool routeRegistered('),
                                        source.index('static void bridgeTask('))
                    if index >= 0)
        program = (HARNESS + source[start:end] +
                   MAIN.replace('STRICT_ROUTER', 'true' if strict_router else 'false'))
        with tempfile.TemporaryDirectory(prefix='duck-bridge-routes-') as directory:
            path = Path(directory)
            (path / 'test.cpp').write_text(program)
            compiled = subprocess.run(['c++', '-std=c++17', str(path / 'test.cpp'),
                                       '-o', str(path / 'test')], capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(path / 'test')], capture_output=True, text=True,
                                    timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_registry_loss_is_repaired_even_when_router_version_still_answers(self):
        # Router 0.11.0 and newer: same-owner registration succeeds.
        self.run_supervisor(strict_router=False)

    def test_routers_rejecting_duplicate_registration_neither_reconnect_nor_lose_repairs(self):
        # Router 0.5.4 through 0.10.x: RouteAlreadyExists while the route is intact.
        self.run_supervisor(strict_router=True)


if __name__ == '__main__':
    unittest.main()
