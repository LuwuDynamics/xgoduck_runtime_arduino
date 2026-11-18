"""Restore the motion client's Router session from outside the control loop."""
import threading
import time

from tof_status import probe_stm32


class BridgeRecovery:
    def __init__(self, controller, handlers, probe=probe_stm32):
        self.controller = controller
        self.handlers = handlers
        self.probe = probe
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name='bridge-recovery', daemon=True)

    def _connect(self, timeout):
        bridge = self.controller.bridge
        # Re-declare handlers that the SDK may have dropped after a route conflict.
        for name, handler in self.handlers.items():
            bridge.provide(name, handler)
        bridge.connect(timeout=timeout)

    def _attempt(self, fault):
        bridge = self.controller.bridge
        try:
            self.probe(bridge.address)
        except Exception as exc:
            print(f'[bridge-recovery] {fault}: STM32 probe failed ({exc}); waiting for MCU routes', flush=True)
            return
        # Recheck after the RPC. A command-only failure needs the firmware's
        # route repair; the Linux feedback connection is still delivering state.
        if self.stop.is_set() or self.controller.bridge_stall() != fault:
            return
        if fault == 'command':
            print('[bridge-recovery] STM32 replies but command acceptance is stalled; waiting for MCU route repair',
                  flush=True)
            return
        print('[bridge-recovery] STM32 replies but state feedback is stalled; reconnecting motion client',
              flush=True)
        bridge.disconnect()
        self._connect(timeout=.5)

    def _run(self):
        next_attempt = 0.
        while not self.stop.wait(.1):
            fault = self.controller.bridge_stall()
            if not fault or time.monotonic() < next_attempt:
                continue
            try:
                self._attempt(fault)
            except Exception as exc:
                print(f'[bridge-recovery] Motion client reconnect failed: {exc}', flush=True)
            next_attempt = time.monotonic() + 2.

    def start(self):
        self._connect(timeout=0)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join()
