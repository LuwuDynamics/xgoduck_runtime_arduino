"""PCM-to-jaw timing, cancellation and coexistence with the existing body owner."""
import array
import asyncio
import copy
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from speech_mouth import MAX_DEGREES, MouthEnvelope, SpeechMouthPublisher
from local_ipc import JsonServer, request
from voice_application import application_type
import test_voice_motion as motion_fixture


def tone(amplitude=6000, seconds=.6, frequency=300, rate=16000):
    return array.array('h', (int(amplitude * math.sin(2 * math.pi * frequency * n / rate))
                             for n in range(int(seconds * rate)))).tobytes()


class MouthEnvelopeTest(unittest.TestCase):
    def test_silence_dc_and_low_noise_stay_closed(self):
        for pcm in (bytes(1920), array.array('h', [12000] * 960).tobytes(), tone(100)):
            self.assertTrue(all(angle == 0 for _, angle in MouthEnvelope().analyse(pcm, 16000)))

    def test_loudness_brightness_and_slew_are_bounded(self):
        quiet = MouthEnvelope().analyse(tone(500), 16000)
        loud = MouthEnvelope().analyse(tone(20000), 16000)
        bright = MouthEnvelope().analyse(tone(20000, frequency=3000), 16000)
        self.assertGreater(loud[-1][1], quiet[-1][1])
        self.assertGreater(bright[-1][1], loud[-1][1])
        angles = [0.] + [angle for _, angle in bright]
        self.assertTrue(all(0 <= v <= MAX_DEGREES for v in angles))
        self.assertLessEqual(max(abs(b - a) for a, b in zip(angles, angles[1:])), 1.801)

    def test_silence_releases_smoothly_to_closed(self):
        envelope = MouthEnvelope()
        before = envelope.analyse(tone(), 16000)[-1][1]
        decay = [v for _, v in envelope.analyse(bytes(32000), 16000)]
        self.assertLess(decay[0], before)
        self.assertEqual(decay[-1], 0)
        self.assertEqual(decay, sorted(decay, reverse=True))

    def test_sustained_loud_speech_keeps_visible_open_close_motion_after_attack(self):
        for amplitude in (6000, 20000):
            with self.subTest(amplitude=amplitude):
                points = MouthEnvelope().analyse(tone(amplitude, seconds=5.), 16000)
                steady = [v for t, v in points if t >= .5]
                self.assertGreater(max(steady) - min(steady), 4.)
                self.assertLess(min(steady), 7.5)
                self.assertGreater(sum(steady) / len(steady), 7.5)
                self.assertGreater(max(steady), 10.)
                self.assertLessEqual(max(steady), MAX_DEGREES)
                self.assertLessEqual(max(abs(b-a) for a,b in zip(steady, steady[1:])), 1.801)

    def test_short_medium_long_energy_detect_syllable_edges_but_silence_closes(self):
        envelope = MouthEnvelope()
        envelope.analyse(tone(1500, seconds=.32), 16000)
        envelope.analyse(tone(12000, seconds=.02), 16000)
        short, medium, long = envelope.windows
        self.assertGreater(short, medium)
        self.assertGreater(medium, long)
        self.assertEqual(envelope.status()['windows_ms'], [20, 80, 320])
        envelope.analyse(bytes(640), 16000)
        self.assertEqual(envelope.windows[0], 0)
        self.assertGreater(envelope.windows[2], 0)
        self.assertEqual(envelope.target, 0)  # Long energy cannot keep a pause open.
        closed = envelope.analyse(bytes(32000), 16000)
        self.assertEqual(closed[-1][1], 0)
        self.assertLessEqual(len(envelope.energy), 17)

    def test_audio_frame_boundaries_preserve_energy_and_rhythm_continuity(self):
        pcm = tone(6000, seconds=3.)
        whole = [v for _,v in MouthEnvelope().analyse(pcm, 16000)]
        envelope = MouthEnvelope()
        streamed = []
        for offset in range(0,len(pcm),1920):
            streamed.extend(v for _,v in envelope.analyse(pcm[offset:offset+1920],16000))
        self.assertEqual(streamed, whole)

    def test_supported_pcm_rates_have_same_speech_timing_and_bounded_motion(self):
        shapes = []
        for rate in (8000,16000,24000,48000):
            points = MouthEnvelope().analyse(tone(6000,seconds=2.,rate=rate),rate)
            self.assertEqual(len(points),100)
            self.assertTrue(all(0<=v<=MAX_DEGREES for _,v in points))
            shapes.append([v for _,v in points])
        for other in shapes[1:]:
            self.assertLess(max(abs(a-b) for a,b in zip(shapes[0],other)),.25)


class MouthPublisherTest(unittest.TestCase):
    def setUp(self):
        self.send = Mock(return_value={'ok': True})
        self.p = SpeechMouthPublisher(self.send, clock=lambda: 100.)

    def test_audio_observer_only_enqueues_and_worker_uses_dac_time(self):
        self.p.begin(101)
        with patch.object(self.p.envelope, 'analyse', side_effect=AssertionError('callback analysis')):
            self.p.observe(tone(seconds=.06), 16000, 100.54)
        self.send.assert_not_called()
        self.p.process_once()
        body = self.send.call_args.args[0]
        self.assertEqual((body['session'], body['utterance']), (101, 1))
        self.assertEqual([round(t, 2) for t, _ in body['points']], [100.54, 100.56, 100.58])
        self.assertGreater(body['points'][-1][1], 0)

    def test_idle_audio_is_ignored_and_pending_audio_is_bounded(self):
        self.p.observe(tone(), 16000, 100.)
        self.assertFalse(self.p.frames)
        self.p.begin(101)
        for n in range(100):
            self.p.observe(bytes(1920), 16000, 100 + n * .06)
        self.assertEqual(len(self.p.frames), 24)

    def test_stop_has_priority_and_late_old_frame_cannot_revive(self):
        self.p.begin(101)
        old = self.p.context
        self.p.observe(tone(seconds=.06), 16000, 100.)
        self.p.stop()
        self.p.frames.append((old, tone(seconds=.06), 16000, 100.1))
        self.p.process_once()
        self.p.process_once()
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.send.call_args.args[0]['points'], [])
        self.assertFalse(self.p.status()['speaking'])

    def test_new_utterance_closes_previous_before_new_audio_and_resets_envelope(self):
        self.p.begin(101)
        self.p.observe(tone(), 16000, 100.)
        self.p.process_once()
        self.p.begin(101)
        self.p.observe(tone(seconds=.06), 16000, 101.)
        self.p.process_once()
        self.assertEqual(self.send.call_args.args[0]['points'], [])
        self.p.process_once()
        body = self.send.call_args.args[0]
        self.assertEqual(body['utterance'], 2)
        self.assertEqual(body['sequence'], 3)
        self.assertLessEqual(body['points'][0][1], 1.8)

    def test_failed_ipc_does_not_escape_audio_path(self):
        self.p.begin(101)
        self.send.side_effect = OSError('motion unavailable')
        self.p.observe(tone(seconds=.06), 16000, 100.)
        self.p.process_once()
        self.assertEqual(self.p.status()['ipc_errors'], 1)
        self.assertEqual(self.p.status()['last_error'], 'motion unavailable')
        self.p.stop()
        self.p.process_once()
        self.assertEqual(self.p.status()['ipc_errors'], 2)

    def test_backlog_keeps_two_recent_frames_and_discards_expired_audio(self):
        self.p.begin(101)
        for n in range(8):
            self.p.observe(tone(seconds=.06), 16000, 100. + n * .06)
        self.p.process_once()
        body = self.send.call_args.args[0]
        self.assertEqual(len(body['points']), 6)
        self.assertAlmostEqual(body['points'][0][0], 100.36)
        self.assertEqual(self.p.dropped, 6)
        self.p.observe(tone(seconds=.06), 16000, 99.9)
        self.p.process_once()
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.p.dropped, 7)

    def test_ten_seconds_of_speech_stay_current_when_ipc_is_slower_than_audio_frames(self):
        now, next_audio = 100., 100.
        pcm = tone(seconds=.06)
        leads, stamps, sizes = [], [], []
        def callbacks(until):
            nonlocal now, next_audio
            while next_audio <= until:
                p.observe(pcm, 16000, next_audio + .54)
                next_audio += .06
            now = until
        def slow_send(body):
            callbacks(now + .09)  # Observed board IPC mean ~80 ms, versus 60 ms audio.
            leads.append(body['points'][0][0] - now)
            stamps.extend(t for t, _ in body['points'])
            sizes.append(len(body['points']))
            return {'ok':True}
        p = SpeechMouthPublisher(slow_send, clock=lambda: now)
        p.begin(101)
        callbacks(now)
        while now < 110:
            p.process_once()
            callbacks(now + .01)
        self.assertGreater(len(stamps), 400)
        self.assertLessEqual(max(sizes), 6)
        self.assertGreater(min(leads), .2)
        self.assertLess(max(b-a for a,b in zip(stamps, stamps[1:])), .041)
        self.assertEqual(p.errors, 0)


class MouthMotionTest(unittest.TestCase):
    def setUp(self):
        self.f = motion_fixture.VoiceMotionTest()
        self.f.setUp()
        self.c, self.access = self.f.c, self.f.access
        self.sequence = 0

    def submit(self, points=None, utterance=1, session=101):
        self.sequence += 1
        return self.access.speech(session, utterance, self.sequence,
                                  [[self.f.now + .1, 6.]] if points is None else points)

    def test_future_dac_points_apply_at_due_time_and_restore_previous_target(self):
        self.c.mouth = 2.
        self.submit([[self.f.now + .54, 6.], [self.f.now + .56, 9.]])
        self.f.advance(.5)
        self.assertEqual(self.c.mouth, 2.)
        self.f.advance(.05)
        self.assertEqual(self.c.mouth, 6.)
        self.f.advance(.05)
        self.assertEqual(self.c.mouth, 9.)
        self.f.advance(.2)
        self.assertEqual(self.c.mouth, 2.)
        self.assertFalse(self.access.state()['tts_mouth']['active'])

    def test_mouth_is_independent_of_voice_locomotion_permission(self):
        self.f.allowed = False
        self.submit()
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 6.)
        self.assertEqual(self.c.input_source, 'web')
        self.assertFalse(self.c.arm_pending)
        self.assertEqual(self.c.mode, 'policy')

    def test_original_controller_emits_only_changed_jaw_target_on_bridge(self):
        self.c.step()
        wire = motion_fixture.fixture.wire
        before = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])
        self.submit()
        self.f.advance(.15)
        self.c.step()
        after = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])
        self.assertEqual(after[4], wire.POLICY)
        self.assertEqual(after[7:21], before[7:21])
        self.assertEqual(after[21], 6.)
        self.assertFalse(self.c.arm_pending)

    def test_speech_never_arms_or_takes_over_disabled_or_special_body_states(self):
        changes = [dict(mode='shadow'), dict(mode='off'), dict(mode='calibrate'),
                   dict(mode='servo_debug'), dict(enabled=False), dict(source='gamepad'),
                   dict(phase='getup'), dict(phase='default_pose'), dict(pick=True),
                   dict(roll=1000), dict(ready=False), dict(watchdog=True)]
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.c.mode = change.get('mode', 'policy')
                self.c.feedback.enabled = change.get('enabled', True)
                self.c.input_source = change.get('source', 'web')
                self.c.recovery.phase = change.get('phase', 'walk')
                self.c.pick_active = change.get('pick', False)
                self.c.roulade_until = change.get('roll', 0)
                self.f.motion_status.update(full_robot_ready=change.get('ready', True),
                                            mcu_watchdog=change.get('watchdog', False))
                before = self.c.mode, self.c.input_source, self.c.bridge.notify.call_count
                self.submit()
                self.f.advance(.15)
                self.assertEqual(self.c.mouth, 0)
                self.assertFalse(self.c.arm_pending)
                self.assertEqual(before, (self.c.mode, self.c.input_source, self.c.bridge.notify.call_count))

    def test_talking_does_not_replace_a_running_walk_or_its_queue(self):
        self.f.move(twist=[.2, 0, 0], duration=10)
        self.f.move(twist=[0, 0, 1], kind='turn', duration=2)
        before = copy.deepcopy((self.access.action, self.access.pending))
        self.c.head = [.1, .2, .3, .4]
        self.submit()
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 6.)
        self.assertEqual(self.c.twist, [.2, 0, 0])
        self.assertEqual(self.c.head, [.1, .2, .3, .4])
        self.assertEqual(self.c.input_source, 'voice')
        self.assertEqual(before, (self.access.action, self.access.pending))
        self.submit([])
        self.assertEqual(self.c.mouth, 0.)
        self.assertEqual(self.c.twist, [.2, 0, 0])
        self.assertEqual(before, (self.access.action, self.access.pending))

    def test_tts_mouth_ignores_other_joint_failure_and_stops_only_if_34_feedback_is_lost(self):
        self.f.motion_status.update(full_robot_ready=False, imu_ok=True,
                                    feedback_age_ms=5., missing_joint_ids=[33])
        before = self.c.bridge.notify.call_count
        self.submit()
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 6.)
        self.assertEqual(self.c.bridge.notify.call_count, before)
        self.assertFalse(self.c.arm_pending)
        self.f.motion_status['missing_joint_ids'] = [34]
        self.f.advance(.05)
        self.assertEqual(self.c.mouth, 0.)
        self.assertIn('34 号舵机', self.access.speech_mouth.status()['reason'])
        self.assertFalse(self.access.speech_mouth.status()['active'])

    def test_manual_mouth_wins_for_this_response_and_next_response_can_animate(self):
        self.submit()
        self.f.advance(.15)
        self.access.web_command(mouth=18.)
        self.assertEqual(self.c.mouth, 18.)
        self.assertFalse(self.submit()['ok'])
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 18.)
        self.assertTrue(self.submit(utterance=2)['ok'])
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 6.)
        self.submit([], utterance=2)
        self.assertEqual(self.c.mouth, 18.)

    def test_explicit_voice_mouth_and_roll_override_tts(self):
        for kind in ('mouth', 'roulade', 'pick', 'stand'):
            with self.subTest(kind=kind):
                self.setUp()
                self.submit()
                self.f.advance(.15)
                self.access.start_action(kind=kind, session=101,
                                         **({'mouth': 20.} if kind == 'mouth' else {}))
                chosen = self.c.mouth
                self.assertFalse(self.submit()['ok'])
                self.f.advance(.15)
                self.assertEqual(self.c.mouth, chosen)

    def test_mode_takeover_does_not_restore_an_old_mouth_target(self):
        self.c.mouth = 15.
        self.submit()
        self.f.advance(.15)
        self.access.web_mode('shadow')
        self.assertEqual(self.c.mouth, 0.)
        self.submit([])
        self.assertEqual(self.c.mouth, 0.)

    def test_new_tts_cannot_override_an_explicit_mouth_action_still_running(self):
        self.access.start_action(kind='mouth', mouth=20., duration=2., session=101)
        self.submit(utterance=2)
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 20.)
        self.assertFalse(self.submit(utterance=2)['ok'])
        self.f.advance(2.)
        self.assertEqual(self.access.action['state'], 'completed')
        self.submit(utterance=3)
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 6.)

    def test_stop_retires_same_utterance_and_old_sequences(self):
        self.submit()
        self.f.advance(.15)
        self.submit([])
        self.assertEqual(self.c.mouth, 0.)
        self.assertFalse(self.submit()['ok'])
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.access.speech(101, 1, 1, [[self.f.now, 12.]])
        self.f.advance(.1)
        self.assertEqual(self.c.mouth, 0.)

    def test_missing_heartbeat_and_new_session_restore_mouth_and_reject_old_audio(self):
        self.submit()
        self.f.advance(.15)
        self.f.now += 1.
        self.access.tick()
        self.assertEqual(self.c.mouth, 0.)
        with self.assertRaisesRegex(ValueError, 'heartbeat'):
            self.submit()
        self.access.heartbeat(102)
        with self.assertRaisesRegex(ValueError, 'session'):
            self.submit()
        self.assertTrue(self.submit(utterance=1, session=102)['ok'])
        self.f.now += .15
        self.access.tick()
        self.assertEqual(self.c.mouth, 6.)
        self.access.close()
        self.assertEqual(self.c.mouth, 0.)

    def test_bad_point_packets_are_rejected_atomically(self):
        now = self.f.now
        invalid = [None, {}, [[now, True]], [[now, -1]], [[now, 12.01]], [[now, float('nan')]],
                   [[float('inf'), 1]], [[now + 1.21, 1]], [[now - .21, 1]],
                   [[now, 1, 2]], [[now + .1, 2], [now, 1]], [[now, 1]] * 9]
        for points in invalid:
            with self.subTest(points=points):
                with self.assertRaises(ValueError):
                    self.access.speech(101, 1, 1, points)
                self.assertEqual(self.access.speech_mouth.sequence, 0)
                self.assertEqual(self.c.mouth, 0)
        for utterance, sequence in ((True, 1), (1, True), (0, 1), (1, 0), (1., 1), (1, 2**53)):
            with self.assertRaises(ValueError):
                self.access.speech(101, utterance, sequence, [[now, 1]])

    def test_authenticated_ipc_drives_timing_without_body_action(self):
        server = JsonServer(0, 'fixture', lambda method, path, body: self.access.speech(**body))
        server.start()
        self.addCleanup(server.close)
        port = server.server.server_port
        body = dict(session=101, utterance=1, sequence=1, points=[[self.f.now + .1, 7.]])
        with self.assertRaisesRegex(ValueError, 'authentication'):
            request(port, 'wrong-token', '/speech-mouth', body)
        self.assertTrue(request(port, 'fixture', '/speech-mouth', body)['ok'])
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 7.)
        body.update(sequence=2, points=[])
        request(port, 'fixture', '/speech-mouth', body)
        self.assertEqual(self.c.mouth, 0.)
        self.assertIsNone(self.access.action)


class MouthApplicationTest(unittest.TestCase):
    def app(self, enabled=True):
        class SDK:
            def __init__(self, config):
                self.config, self.state, self._duck_session = config, 'idle', 101
            def _set_state(self, state):
                self.state = state
            def _handle_tts(self, data):
                if data['state'] == 'start': self._set_state('speaking')
            async def abort_speaking(self, *args, **kwargs):
                assert publisher.context is None
            async def shutdown(self):
                assert publisher.context is None
        publisher = SpeechMouthPublisher(Mock())
        return application_type(SDK, SimpleNamespace(mouth=publisher))({'tts_mouth_enabled': enabled}), publisher

    def test_tts_stop_keeps_animation_until_actual_audio_finishes(self):
        app, p = self.app()
        app._handle_tts({'state': 'start'})
        self.assertEqual(p.context, (101, 1))
        app._handle_tts({'state': 'stop'})
        self.assertIsNotNone(p.context)
        app._set_state('listening')
        self.assertIsNone(p.context)

    def test_abort_and_shutdown_stop_before_waiting_on_sdk_network(self):
        app, p = self.app()
        app._handle_tts({'state': 'start'})
        asyncio.run(app.abort_speaking('fixture'))
        self.assertIsNone(p.context)
        app._handle_tts({'state': 'start'})
        asyncio.run(app.shutdown())
        self.assertIsNone(p.context)

    def test_disabled_animation_never_opens_a_publisher_context(self):
        app, p = self.app(False)
        app._handle_tts({'state': 'start'})
        self.assertIsNone(p.context)


if __name__ == '__main__':
    unittest.main()
