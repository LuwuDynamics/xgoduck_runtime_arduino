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
from speech_mouth import MIN_DEGREES, MAX_DEGREES, MouthEnvelope, SpeechMouthPublisher
from local_ipc import JsonServer, request
from voice_application import application_type
import test_voice_motion as motion_fixture


def tone(amplitude=6000, seconds=.6, frequency=300, rate=16000):
    return array.array('h', (int(amplitude * math.sin(2 * math.pi * frequency * n / rate))
                             for n in range(int(seconds * rate)))).tobytes()


class MouthEnvelopeTest(unittest.TestCase):
    def test_silence_dc_and_low_noise_stay_closed(self):
        for pcm in (bytes(1920), array.array('h', [12000] * 960).tobytes(), tone(100)):
            self.assertTrue(all(angle == MIN_DEGREES for _, angle in MouthEnvelope().analyse(pcm, 16000)))

    def test_loudness_and_slew_are_bounded_without_artificial_frequency_motion(self):
        quiet = MouthEnvelope().analyse(tone(500), 16000)
        loud = MouthEnvelope().analyse(tone(20000), 16000)
        bright = MouthEnvelope().analyse(tone(20000, frequency=3000), 16000)
        self.assertGreater(loud[-1][1], quiet[-1][1])
        self.assertAlmostEqual(bright[-1][1], loud[-1][1], delta=.05)
        angles = [MIN_DEGREES] + [angle for _, angle in loud]
        self.assertTrue(all(MIN_DEGREES <= v <= MAX_DEGREES for v in angles))
        changes = [abs(b-a) for a,b in zip(angles, angles[1:])]
        self.assertAlmostEqual(max(changes), 3., places=3)
        self.assertLessEqual(max(changes), 3.001)

    def test_silence_releases_smoothly_to_closed(self):
        envelope = MouthEnvelope()
        before = envelope.analyse(tone(), 16000)[-1][1]
        decay = [v for _, v in envelope.analyse(bytes(32000), 16000)]
        self.assertLessEqual(decay[0], before + .001)
        self.assertEqual(decay[-1], MIN_DEGREES)
        self.assertEqual(decay[2:], sorted(decay[2:], reverse=True))

    def test_sustained_voice_returns_to_35_percent_without_a_fixed_rhythm(self):
        for amplitude in (6000, 20000):
            with self.subTest(amplitude=amplitude):
                points = MouthEnvelope().analyse(tone(amplitude, seconds=2.), 16000)
                steady = [v for t, v in points if t >= .5]
                self.assertLess(max(steady) - min(steady), .05)
                self.assertAlmostEqual(sum(steady)/len(steady), 8.55, delta=.05)

    def test_syllable_onset_opens_more_and_silence_does_not_use_long_window_energy(self):
        envelope = MouthEnvelope()
        envelope.analyse(tone(1500, seconds=.32), 16000)
        baseline = envelope.angle
        envelope.analyse(tone(12000, seconds=.03), 16000)
        short, medium, long = envelope.windows
        self.assertGreater(short, medium)
        self.assertGreater(medium, long)
        self.assertGreater(envelope.onset, 0)
        self.assertGreater(envelope.target, baseline)
        self.assertEqual(envelope.status()['windows_ms'], [25, 120, 300])
        envelope.analyse(bytes(1280), 16000)
        self.assertEqual(envelope.windows[0], 0)
        self.assertGreater(envelope.windows[2], 0)
        self.assertEqual(envelope.target, MIN_DEGREES)
        closed = envelope.analyse(bytes(32000), 16000)
        self.assertEqual(closed[-1][1], MIN_DEGREES)
        self.assertLessEqual(len(envelope.energy), 61)

    def test_arbitrary_pcm_splits_preserve_exact_angles_and_dac_offsets(self):
        pcm = tone(6000, seconds=1.)
        whole = MouthEnvelope().analyse(pcm, 16000)
        envelope = MouthEnvelope()
        streamed, offset, index = [], 0, 0
        sizes = [34, 286, 802, 38, 1920, 126]
        while offset < len(pcm):
            part = pcm[offset:offset + sizes[index % len(sizes)]]
            streamed.extend((offset/32000+t, v) for t,v in envelope.analyse(part, 16000))
            offset += len(part); index += 1
        self.assertEqual([v for _,v in streamed], [v for _,v in whole])
        for (a,_),(b,_) in zip(whole,streamed): self.assertAlmostEqual(a,b,places=10)
        self.assertEqual(envelope.analysis_updates, 100)
        self.assertEqual(envelope.output_points, 50)
        self.assertEqual(len(envelope.pending), 0)

    def test_supported_pcm_rates_analyse_at_100hz_and_output_at_50hz(self):
        shapes = []
        for rate in (8000,16000,24000,44100,48000):
            envelope = MouthEnvelope()
            points = envelope.analyse(tone(6000,seconds=2.,rate=rate),rate)
            self.assertEqual(len(points),100)
            self.assertEqual(envelope.analysis_updates,200)
            self.assertAlmostEqual(points[0][0],.02)
            self.assertAlmostEqual(points[-1][0],2.)
            self.assertTrue(all(MIN_DEGREES<=v<=MAX_DEGREES for _,v in points))
            shapes.append([v for _,v in points])
        for other in shapes[1:]:
            self.assertLess(max(abs(a-b) for a,b in zip(shapes[0],other)),.05)

    def test_windows_match_sample_accurate_dc_removed_rms(self):
        rate=44100
        values=array.array('h',(int(500+(.2 if n<11025 else .5)*9000*math.sin(2*math.pi*237*n/rate)) for n in range(22050)))
        envelope=MouthEnvelope()
        for start in range(0,len(values),441):
            block=values[start:start+441]; envelope.analyse(block.tobytes(),rate)
            for ms,actual in zip((25,120,300),envelope.windows):
                tail=values[max(0,start+len(block)-int(rate*ms/1000)):start+len(block)]
                mean=sum(tail)/len(tail)
                expected=math.sqrt(sum((v-mean)**2 for v in tail)/len(tail))/32768
                self.assertAlmostEqual(actual,expected,places=12)

    def test_normalized_voice_gate_and_base_coefficients(self):
        for rms in (.004,.012,.024):
            sample=round(rms*32768)
            pcm=array.array('h',[sample,-sample]*8000).tobytes()
            envelope=MouthEnvelope();envelope.analyse(pcm,16000)
            energy=sample/32768
            voice=max(0,min(1,(energy-.006)/.012))
            base=min(1,energy/max(energy,.015))
            expected=MIN_DEGREES+(MAX_DEGREES-MIN_DEGREES)*voice*(.08+.27*base)
            self.assertAlmostEqual(envelope.target,expected,places=10)
            self.assertAlmostEqual(envelope.angle,expected,delta=.02)
            self.assertEqual(envelope.onset,0)

    def test_manual_negative_close_target_is_allowed_only_for_the_mouth(self):
        f=motion_fixture.VoiceMotionTest();f.setUp()
        before=list(f.c.head),list(f.c.twist)
        f.c.command(mouth=-3.)
        self.assertEqual(f.c.mouth,-3.)
        self.assertEqual((f.c.head,f.c.twist),before)
        with self.assertRaises(ValueError):f.c.command(mouth=-3.01)

    def test_sample_rate_change_discards_partial_pcm_without_angle_jumps(self):
        envelope=MouthEnvelope();envelope.analyse(tone(seconds=.061),16000)
        previous=envelope.angle
        points=envelope.analyse(tone(seconds=.02,rate=48000),48000)
        self.assertEqual(len(points),1)
        self.assertLessEqual(abs(points[0][1]-previous),3.001)


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
        self.assertEqual([round(t, 2) for t, _ in body['points']], [100.46, 100.48, 100.50])
        self.assertGreater(body['points'][-1][1], 0)

    def test_large_audio_callbacks_split_ipc_packets_without_discarding_points(self):
        self.p.begin(101)
        self.p.observe(tone(seconds=.18),16000,100.54)
        self.p.process_once()
        bodies=[call.args[0] for call in self.send.call_args_list]
        self.assertEqual([len(b['points']) for b in bodies],[8,1])
        points=[point for b in bodies for point in b['points']]
        self.assertEqual(len(points),9)
        self.assertEqual([round(t,2) for t,_ in points],[round(100.46+i*.02,2) for i in range(9)])

    def test_partial_pcm_callbacks_share_the_same_output_clock(self):
        self.p.begin(101)
        pcm=tone(seconds=.06)
        self.p.observe(pcm[:224],16000,100.54)
        self.p.process_once();self.send.assert_not_called()
        self.p.observe(pcm[224:],16000,100.547)
        self.p.process_once()
        self.assertEqual([round(t,3) for t,_ in self.send.call_args.args[0]['points']],[100.46,100.48,100.50])

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
        previous_sequence = self.p.sequence
        self.p.begin(101)
        self.p.observe(tone(seconds=.06), 16000, 101.)
        self.p.process_once()
        self.assertEqual(self.send.call_args.args[0]['points'], [])
        self.p.process_once()
        body = self.send.call_args.args[0]
        self.assertEqual(body['utterance'], 2)
        self.assertEqual(body['sequence'], previous_sequence + 2)
        self.assertLessEqual(body['points'][0][1], 3.0)

    def test_failed_ipc_does_not_escape_audio_path(self):
        self.p.begin(101)
        self.send.side_effect = OSError('motion unavailable')
        self.p.observe(tone(seconds=.06), 16000, 100.2)
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
        self.assertAlmostEqual(body['points'][0][0], 100.28)
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

    def test_50hz_consumer_compensates_its_processing_time(self):
        self.access.tick=Mock()
        self.access.tick_speech=Mock()
        self.access.stop_event=Mock()
        self.access.stop_event.is_set.return_value=False
        self.access.stop_event.wait.side_effect=[False,False,True]
        with patch('motion_access.threading.Thread') as thread, patch('motion_access.time.monotonic',side_effect=[100.,100.003,100.023,100.043]):
            self.access.start()
            thread.call_args.kwargs['target']()
        self.assertEqual(self.access.tick_speech.call_count,3)
        self.access.tick.assert_not_called()
        for call in self.access.stop_event.wait.call_args_list:self.assertAlmostEqual(call.args[0],.017,places=9)

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
                   dict(mode='servo_debug'), dict(enabled=False),
                   dict(phase='getup'), dict(phase='default_pose'), dict(pick=True),
                   dict(roll=1000), dict(sitstand='sit'), dict(kick='left'), dict(kick='right'),
                   dict(ready=False), dict(watchdog=True)]
        for change in changes:
            with self.subTest(change=change):
                self.setUp()
                self.c.mode = change.get('mode', 'policy')
                self.c.feedback.enabled = change.get('enabled', True)
                self.c.input_source = change.get('source', 'web')
                self.c.recovery.phase = change.get('phase', 'walk')
                self.c.pick_active = change.get('pick', False)
                self.c.roulade_until = change.get('roll', 0)
                self.c.sitstand_phase = change.get('sitstand', 'idle')
                self.c.kick_until = 1000 if 'kick' in change else 0
                self.c.kick_side = change.get('kick')
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
        self.assertEqual(self.c.mouth, MIN_DEGREES)
        self.assertEqual(self.c.twist, [.2, 0, 0])
        self.assertEqual(before, (self.access.action, self.access.pending))

    def test_talking_uses_mouth_while_gamepad_keeps_leg_control(self):
        self.c.input_source = 'gamepad'
        self.c.command(twist=[.1, 0., 0.], source='gamepad')
        self.submit()
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 6.)
        self.assertEqual(self.c.twist, [.1, 0., 0.])
        self.assertEqual(self.c.input_source, 'gamepad')

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
        self.assertEqual(self.c.mouth, MIN_DEGREES)
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
        self.assertEqual(self.c.mouth, MIN_DEGREES)
        self.assertFalse(self.submit()['ok'])
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.access.speech(101, 1, 1, [[self.f.now, 12.]])
        self.f.advance(.1)
        self.assertEqual(self.c.mouth, MIN_DEGREES)

    def test_missing_heartbeat_and_new_session_restore_mouth_and_reject_old_audio(self):
        self.submit()
        self.f.advance(.15)
        self.f.now += 1.
        self.access.tick()
        self.assertEqual(self.c.mouth, MIN_DEGREES)
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
        self.assertEqual(self.c.mouth, MIN_DEGREES)

    def test_bad_point_packets_are_rejected_atomically(self):
        now = self.f.now
        invalid = [None, {}, [[now, True]], [[now, MIN_DEGREES-.01]], [[now, MAX_DEGREES + .01]], [[now, float('nan')]],
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

    def test_full_speech_mouth_range_applies_without_enabling_the_body(self):
        for angle in (MIN_DEGREES, 0., 15., 30.):
            self.submit([[self.f.now + .05, angle]])
            self.f.advance(.06)
            self.assertEqual(self.c.mouth, angle)
            self.assertFalse(self.c.arm_pending)

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
        self.assertEqual(self.c.mouth, MIN_DEGREES)
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
