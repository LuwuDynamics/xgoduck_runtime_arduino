"""Pure input mapping + control handoff/disconnect tests; no motors needed."""
import ast
import json
import tempfile
from pathlib import Path
import sys
import threading
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'python'), str(ROOT/'tools/gamepad')]
from input_reader import normalize, is_gamepad
# Avoid FastAPI/httpx dependencies for local tests; exercise actual control code.
source = ast.parse((ROOT/'python/gamepad.py').read_text())
nodes = [n for n in source.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in ('motion_from_input','GamepadControl')]
nodes += [n for n in source.body if isinstance(n, ast.Assign)]
ns = dict(time=time, threading=threading, json=json, Path=Path)
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'gamepad.py', 'exec'), ns)
GamepadControl, motion = ns['GamepadControl'], ns['motion_from_input']


class GamepadTest(unittest.TestCase):
    def setUp(self):
        self.c = Mock(lock=threading.RLock(), mode='off', input_source='gamepad')
        self.g = GamepadControl.__new__(GamepadControl)
        self.g.controller = self.c
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.g.bindings_path = Path(self.temp.name)/'bindings.json'
        self.g.bindings = dict(ns['DEFAULT_BINDINGS'])
        self.g.learning = None
        self.g.binding_message = ''
        self.g.enabled, self.g.identity, self.g.buttons, self.g.error = True, 'pad1', {}, ''
        self.data = dict(connected=True, stamp=time.monotonic(), id='pad1',
            axes=dict(lx=0, ly=-1, rx=.5, ry=0), buttons=dict(lb=False))

    def test_web_cannot_overwrite_gamepad_commands(self):
        source = ast.parse((ROOT/'python/controller.py').read_text())
        cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Controller')
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'command')
        import math
        ns = dict(math=math, time=time, EXCLUSIVE_MODES=('calibrate','servo_debug'))
        exec(compile(ast.Module(body=[fn], type_ignores=[]), 'controller.py', 'exec'), ns)
        self.c.pick_active = False
        self.c.twist = [.2,0,0]
        result = ns['command'](self.c, twist=[0,0,0])
        self.assertIn('ignored',result)
        self.assertEqual(self.c.twist,[.2,0,0])
        ns['command'](self.c, twist=[.1,0,0], source='gamepad')
        self.assertEqual(self.c.twist,[.1,0,0])

    def test_deadzone_and_bounded_motion(self):
        self.assertEqual(normalize(0,-32768,32767),0)
        self.assertEqual(normalize(128,0,255),0)
        self.assertEqual(normalize(-999,-100,100),-1)
        self.assertEqual(normalize(999,-100,100),1)
        self.assertEqual(motion(self.data),[.4,0.,-.5])
        self.data['buttons']['lb']=False
        self.assertEqual(motion(self.data),[.4,0.,-.5])
        self.data['axes'] = dict(lx=0,ly=0,rx=0,ry=0)
        self.assertEqual(motion(self.data),[0.,0.,0.])

    def test_keyboard_and_partial_devices_rejected(self):
        self.assertFalse(is_gamepad({1:[30,31],3:[0,1]}))
        self.assertTrue(is_gamepad({1:[304,305],3:[0,1,3,4]}))

    def test_arm_requires_new_button_press_without_lb(self):
        self.data['buttons']['start']=True
        self.g.consume(self.data)
        self.c.set_mode.assert_called_once_with('policy')
        self.c.set_mode.reset_mock()
        self.g.consume(self.data)
        self.c.set_mode.assert_not_called()
        self.c.command.assert_called_with(twist=[.4,0.,-.5],source='gamepad')

    def test_stop_wins_over_start(self):
        self.data['buttons'].update(east=True,start=True)
        self.g.consume(self.data)
        self.c.set_mode.assert_called_once_with('off')

    def test_disconnect_and_stale_snapshot_disarm(self):
        for patch in ({'connected':False},{'stamp':time.monotonic()-1},{'id':'replacement'}):
            with self.subTest(patch=patch):
                self.g.enabled=True;self.c.set_mode.reset_mock()
                self.g.consume(dict(self.data,**patch))
                self.assertFalse(self.g.enabled)
                self.c.set_mode.assert_called_once_with('off')
                self.assertEqual(self.c.input_source,'web')

    def test_calibration_releases_source_without_changing_its_mode(self):
        self.c.mode='calibrate'
        self.g.consume(self.data)
        self.assertFalse(self.g.enabled)
        self.c.set_mode.assert_not_called()

    def test_enabling_requires_neutral_and_does_not_arm(self):
        self.g.client=Mock()
        self.g.client.get.return_value.json.return_value={'input':self.data}
        with self.assertRaisesRegex(ValueError,'Center'):
            self.g.enable(True)
        self.data['axes']={k:0 for k in self.data['axes']};self.data['buttons']={}
        self.g.enable(True)
        self.c.set_mode.assert_called_once_with('off')

    def learn(self, action='pick'):
        self.g.enabled = False
        self.g.client = Mock()
        self.g.client.get.return_value.json.return_value = {'input': self.data}
        self.g.configure(dict(operation='learn', action=action))

    def test_learning_short_press_persists_without_motion(self):
        self.learn()
        self.data['button_presses'] = {'west': 1}
        self.g.consume(self.data)
        self.assertIsNone(self.g.learning)
        self.assertEqual(self.g.bindings['pick'], 'west')
        self.assertEqual(json.loads(self.g.bindings_path.read_text())['pick'], 'west')
        self.c.set_mode.assert_not_called()
        self.c.command.assert_not_called()
        with unittest.mock.patch.object(threading.Thread, 'start'):
            loaded = GamepadControl(self.c, Mock(), self.g.bindings_path)
        self.assertEqual(loaded.bindings['pick'], 'west')

    def test_learning_waits_for_release_and_rejects_duplicate(self):
        self.data['buttons']['east'] = True
        self.learn()
        self.g.consume(self.data)
        self.assertIsNotNone(self.g.learning)
        self.data['buttons']['east'] = False
        self.g.consume(self.data)
        self.data['buttons']['east'] = True
        self.g.consume(self.data)
        self.assertIsNone(self.g.learning)
        self.assertIsNone(self.g.bindings['pick'])
        self.assertIn('already assigned', self.g.binding_message)

    def test_binding_timeout_disconnect_cancel_and_multiple(self):
        for patch in ({'connected':False}, {'id':'other'}, {'buttons':{'west':True,'north':True}}):
            self.learn()
            self.g.consume(dict(self.data, **patch))
            self.assertIsNone(self.g.learning)
            self.assertIsNone(self.g.bindings['pick'])
        self.learn()
        self.g.learning['until'] = time.monotonic()-1
        self.g.consume(self.data)
        self.assertIsNone(self.g.learning)
        self.learn()
        self.g.configure(dict(operation='cancel'))
        self.assertIsNone(self.g.learning)

    def test_binding_requires_disabled_and_keeps_stop(self):
        with self.assertRaisesRegex(ValueError, 'Disable'):
            self.g.configure(dict(operation='learn',action='pick'))
        self.g.enabled = False
        with self.assertRaisesRegex(ValueError, 'Torque off'):
            self.g.configure(dict(operation='clear',action='off'))
        self.learn()
        self.data['axes'] = dict(lx=0,ly=0,rx=0,ry=0)
        with self.assertRaisesRegex(ValueError, 'cancel'):
            self.g.enable(True)

    def test_custom_actions_and_stop_priority(self):
        self.g.bindings.update(pick='west',roulade='north',off='rb')
        self.data['buttons'] = {'west':True}
        self.g.consume(self.data)
        self.c.start_pick.assert_called_once()
        self.g.consume(self.data)
        self.c.start_pick.assert_called_once()
        self.data['buttons'] = {'north':True}
        self.g.consume(self.data)
        self.c.start_roulade.assert_called_once()
        self.data['buttons'] = {'rb':True,'start':True}
        self.g.consume(self.data)
        self.c.set_mode.assert_called_once_with('off')


if __name__=='__main__': unittest.main()
