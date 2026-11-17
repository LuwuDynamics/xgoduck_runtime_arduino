"""Check that the watchdog only reopens the App's own connection when needed."""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from bridge_recovery import BridgeRecovery


class BridgeRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.controller = Mock()
        self.controller.bridge.address = 'unix:///test-router.sock'
        self.probe = Mock()
        self.watchdog = BridgeRecovery(self.controller, {'duck_state': self.controller.receive}, probe=self.probe)

    def test_state_loss_with_a_live_mcu_reopens_the_motion_client(self):
        self.controller.bridge_stall.return_value = 'feedback'
        self.watchdog._attempt('feedback')
        self.probe.assert_called_once_with(self.controller.bridge.address)
        self.assertEqual(self.controller.bridge.method_calls, [
            call.disconnect(), call.provide('duck_state', self.controller.receive), call.connect(timeout=.5)])

    def test_command_route_loss_is_left_to_the_stm32_supervisor(self):
        self.controller.bridge_stall.return_value = 'command'
        self.watchdog._attempt('command')
        self.probe.assert_called_once()
        self.assertEqual(self.controller.bridge.method_calls, [])

    def test_unreachable_stm32_or_feedback_return_does_not_reopen(self):
        self.probe.side_effect = TimeoutError('STM32 did not answer')
        self.watchdog._attempt('feedback')
        self.probe.side_effect = None
        self.controller.bridge_stall.return_value = None
        self.watchdog._attempt('feedback')
        self.assertEqual(self.controller.bridge.method_calls, [])
