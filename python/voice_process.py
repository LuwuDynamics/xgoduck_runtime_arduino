"""Second App process: headless AI Chat Pro plus the unchanged media HTTP API."""
import asyncio
import contextlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from voice_application import application_type
from app_paths import DATA, PRIVATE, VENDOR, private_directory
from voice_config import VoiceConfig
from local_ipc import JsonServer, request
from audio_devices import CaptureGain, Volume, device_identity, prepare_capture, prepare_portaudio, select_audio, usb_audio_cards
from onboarding import Onboarding, ota_url
from speech_mouth import SpeechMouthPublisher
from mouth_ipc import MouthSender
from connection_sounds import ConnectionSounds
from capture_channels import CaptureChannels, hardware_channels
from audio_devices import capture_capabilities, aec_playback_transform


class Runtime:
    def __init__(self, token, motion_port, config=None, onboarding=None):
        self.token, self.motion_port = token, motion_port
        self.config = config or VoiceConfig()
        self.onboarding = onboarding or Onboarding(self.config)
        self.volume = Volume(self.config)
        self.mouth_sender = MouthSender(os.environ.get('DUCK_MOUTH_SOCKET', str(PRIVATE / 'mouth.sock')), token)
        self.mouth = SpeechMouthPublisher(self.mouth_sender.send)
        self.loop = None
        self.session = None
        self.app = None
        self.gate = self.bench = None
        self.transition = asyncio.Lock()
        self.stop = asyncio.Event()
        self.info_lock = threading.Lock()
        self.info = dict(state='starting', message='', bound=None)
        self.next_attempt = 0.
        self.session_settings = None
        self.audio_ready = False
        self.capture_channels = CaptureChannels()
        self.connection_sounds = ConnectionSounds(self.play_connection_sound)

    def update(self, **values):
        with self.info_lock:
            changed = any(self.info.get(k) != v for k, v in values.items())
            self.info.update(values)
        if changed:
            print('[voice]', values.get('state', ''), values.get('message', ''), flush=True)

    def status(self):
        audio = getattr(self.app, 'audio', None)
        playback_status = getattr(audio, 'playback_status', None)
        playback = playback_status() if callable(playback_status) else {}
        if not isinstance(playback, dict):
            playback = {}
        with self.info_lock:
            return dict(self.info, pid=os.getpid(), enabled=self.config.get()['enabled'],
                        volume=self.config.get()['volume'], media_busy=bool(self.gate and self.gate.busy()),
                        input_auto_gain=self.config.get().get('input_auto_gain'),
                        input_gain_db=self.config.get()['input_gain_db'],
                        output_gain_db=self.config.get()['output_gain_db'],
                        aec_output_headroom_db=self.config.get()['aec_output_headroom_db'],
                        tts_mouth_enabled=self.config.get()['tts_mouth_enabled'],
                        tts_mouth=self.mouth.status(),
                        duck_sound=dict(getattr(getattr(self.app, 'duck_sounds', None), 'last', {'state': 'idle'})),
                        connection_sounds=self.connection_sounds.status(),
                        audio_playback=playback,
                        microphone=self.capture_channels.status(),
                        wake_capture=dict(getattr(getattr(self.app, 'wakeword', None), '_capture_stats', {})),
                        device_id=os.environ.get('DUCK_DEVICE_ID', ''), audio_ready=self.audio_ready,
                        cloud_connected=bool(getattr(getattr(self.app, 'protocol', None), '_mqtt_connected', False)),
                        session_cloud_region=(self.session_settings[0].get('cloud_region')
                                              if self.app is not None and self.session_settings else None),
                        onboarding=self.onboarding.snapshot())

    async def play_connection_sound(self, event, kind, gain):
        if self.gate is None or self.gate.busy() or self.stop.is_set():
            return False
        app = self.app
        if app is not None:
            player = getattr(app, 'duck_sounds', None)
            if (not self.audio_ready or player is None or getattr(app, '_closing', False)
                    or getattr(app, 'state', None) not in ('idle', 'listening')):
                return False
            await player.play(kind, software_gain=gain)
            return True
        if event != 'network': return False
        # First-install networking feedback must work before account binding and
        # cloud startup. Reuse Quack's PCM renderer and the existing audio gate;
        # release ALSA completely before the chat codec starts.
        def offline():
            from duck_sound import sound_pcm, amplify_sound
            cfg = self.config.get()
            output = select_audio(usb_audio_cards(), 'output', cfg['output_device'])
            pcm = sound_pcm(kind, os.environ.get('DUCK_DEVICE_ID', 'XGO-Duck'), 0, 16000, cfg['output_gain_db'])
            pcm = amplify_sound(pcm, gain, cfg['output_gain_db'])
            token = self.gate.acquire()
            try:
                with self.bench.lock:
                    if self.stop.is_set() or self.bench.closed or self.bench.audio or self.bench.tts_busy:
                        return False
                    self.volume.apply()
                    subprocess.run(['aplay', '-q', '-D', f'plughw:{output["card"]},0',
                                    '-t', 'raw', '-f', 'S16_LE', '-r', '16000', '-c', '1', '-'],
                                   input=self.volume.pcm(pcm), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE, check=True, timeout=4)
                return True
            finally:
                self.gate.release(token)
        return await asyncio.to_thread(offline)

    async def stop_session(self):
        self.mouth.stop()
        async with self.transition:
            task, self.session = self.session, None
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def pause_for_media(self):
        await self.withdraw_motion()
        await self.stop_session()
        self.next_attempt = 0
        self.update(state='media', message='Web audio owns the USB sound devices; chat will resume afterwards.')

    async def withdraw_motion(self):
        try:
            await asyncio.to_thread(request, self.motion_port, self.token, '/heartbeat', {'session': None}, .5)
        except (OSError, ValueError):
            pass

    def build_application(self, cards):
        cfg = self.config.get()
        capture_gain = CaptureGain(cfg['input_gain_db'])
        session_id = time.monotonic_ns()
        input_index = output_index = None
        channels = 1
        audio_options = {}
        if self.audio_ready:
            input_card = select_audio(cards, 'input', cfg['input_device'])
            output_card = select_audio(cards, 'output', cfg['output_device'])
            prepare_capture(input_card, cfg.get('input_auto_gain'))
            os.environ['ALSA_CONFIG_PATH'] = str(prepare_portaudio(input_card, output_card))
            # Refresh enumeration only while all PortAudio streams are closed.
            already_imported = 'sounddevice' in sys.modules
            import sounddevice as sd
            if already_imported:
                sd._terminate()
                sd._initialize()
            devices = sd.query_devices()
            channels = hardware_channels(devices, input_card)
            capabilities = capture_capabilities(input_card)
            if capabilities.get('channels'):
                channels = min(2, max(capabilities['channels']))
            # This UAC webcam advertises 16 kHz, but produces USB capture errors
            # on the tested dual-mic unit. Acquire at its supported 48 kHz and
            # let ALSA resample both channels before the product selects mono.
            capture_rate = cfg['input_sample_rate']
            if (capture_rate is None and input_card['key'].startswith('usb:0c45:636b:')
                    and 48000 in capabilities.get('rates', [])):
                capture_rate = 48000
            prepare_portaudio(input_card, output_card, capture_rate, channels)
            sd._terminate()
            sd._initialize()
            devices = sd.query_devices()
            def audio_index(name, direction):
                choices = [i for i, d in enumerate(devices) if name in d['name'] and d['max_' + direction + '_channels'] > 0]
                if not choices:
                    raise ValueError('ALSA plug device unavailable: ' + name)
                return choices[0]
            input_index, output_index = audio_index('duck_input', 'input'), audio_index('duck_output', 'output')
            self.volume.apply()
            print(f'[capture] channels={channels} mode={cfg["input_channel_mode"]} '
                  f'hardware_rate={capture_rate or "automatic"} app_rate=16000', flush=True)
        else:
            from no_audio import NoAudio
            audio_options['audio_factory'] = NoAudio
        from application import Application
        from duck_thing import DuckThing
        from voice_media import vision_type
        DuckApplication = application_type(Application, self)
        USBVision = vision_type()
        (private_directory() / 'language.ini').write_text(cfg['language'])
        self.capture_channels = CaptureChannels(channels, cfg['input_channel_mode'])
        wakeword = dict(device=input_index, input_channels=channels, input_latency=.32,
                        input_transform=lambda x: capture_gain.floats(self.capture_channels.floats(x)))
        if cfg['language'] == 'cn':
            wakeword.update(model_path=str(Path(__file__).with_name('models') / 'xiaoya.onnx'),
                            threshold=.8, input_scale=1., stride_duration=.08, cooldown_sec=2.)
        app = DuckApplication(dict(ota_url=ota_url(cfg), enable_wakeword=cfg['enable_wakeword'] and self.audio_ready,
            enable_aec=cfg['enable_aec'], enable_camera=cfg['enable_camera'], enable_robot=True,
            audio_available=self.audio_ready, provisioned_mqtt=dict(self.onboarding.mqtt),
            provisioned_bound=self.onboarding.bound, **audio_options,
            send_wake_word_data=False, auto_start_chat=False, ambient_sound={'enabled': False},
            audio=dict(output_sample_rate=16000, input_device=input_index, output_device=output_index,
                       input_channels=channels, input_latency=.32, output_latency=.5, prebuffer_ms=1000, aec_ref_delay_ms=500),
            wakeword=wakeword,
            input_transform=capture_gain.pcm,
            output_transform=aec_playback_transform(self.volume, cfg['aec_output_headroom_db'],
                lambda: (app.audio is not None and app.audio.aec_enabled
                         and app.audio._capture_enabled and app.audio.input_stream is not None)),
            input_downmix=self.capture_channels.pcm,
            tts_mouth_enabled=cfg['tts_mouth_enabled'], output_observer=self.mouth.observe,
            output_reset_observer=self.mouth.stop,
            robot_factory=lambda: DuckThing(self.motion_port, self.token, self.config, session=session_id,
                                            sound_player=lambda kind: app.mcp_quack(kind)),
            vision_factory=lambda device, client: USBVision(device, client, self.bench)))
        app.status_callback = lambda state, text: self.update(state=state, message=text, bound=app.bound)
        app._duck_session = session_id
        return app

    async def chat_session(self):
        app = None
        try:
            self.update(state='connecting', message='Opening USB audio and connecting AI Chat Pro.')
            cards = usb_audio_cards()
            before = ([(c['key'], c['card']) for c in cards], self.bench.camera_path())
            self.audio_ready = any(c['input'] for c in cards) and any(c['output'] for c in cards)
            if self.audio_ready:
                cfg = self.config.get()
                try:
                    select_audio(cards, 'input', cfg['input_device'])
                    select_audio(cards, 'output', cfg['output_device'])
                except ValueError:
                    self.audio_ready = False  # A removed selected device must not take MCP down.
            app = self.build_application(cards)
            self.app = app
            if not await app.start():
                self.update(state='unconfigured', message='Check device binding and server credentials in App logs.')
                return
            if app.bound is not True:
                self.update(state='awaiting_binding', message='Waiting for account binding.', bound=app.bound)
                return
            self.update(state='idle' if self.audio_ready else 'waiting_audio',
                message='Listening for the wake word.' if self.audio_ready else
                        'MCP service running; waiting for USB microphone and speaker.', bound=app.bound)
            while True:
                await asyncio.sleep(2)
                if before != ([(c['key'], c['card']) for c in usb_audio_cards()], self.bench.camera_path()):
                    self.update(state='waiting_audio', message='USB audio/video changed; reopening devices.')
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Network provider exceptions may contain credentials. Keep detailed
            # provider diagnostics in the SDK's own redacted logging paths.
            self.update(state='waiting_audio', message='Chat unavailable: ' + type(exc).__name__)
        finally:
            await self.withdraw_motion()
            if app is not None:
                try:
                    await app.shutdown()
                except Exception as exc:
                    print('[voice] cleanup:', type(exc).__name__, flush=True)
            self.app = None
            self.audio_ready = False
            self.next_attempt = time.monotonic() + 5

    def dispatch(self, method, path, body):
        if method == 'GET' and path == '/status':
            return self.status()
        if method == 'POST' and path == '/config':
            return self.config.update(body)
        if method == 'POST' and path == '/region':
            if not isinstance(body, dict) or set(body) != {'cloud_region'}:
                raise ValueError('Select cn or global.')
            return self.config.select_region(body['cloud_region'])
        if method == 'POST' and path == '/battery-warning':
            self.bench.play_battery_warning()
            return {'ok': True}
        if method == 'POST' and path == '/stop':
            self.loop.call_soon_threadsafe(self.stop.set)
            return {'ok': True}
        raise ValueError('Unknown voice IPC route')

    async def run(self):
        self.loop = asyncio.get_running_loop()
        from voice_media import AudioGate, MediaBench, start_media
        self.gate = AudioGate(self)
        self.bench = MediaBench(self.config, self.volume, self.gate)
        media, media_thread = start_media(self.bench, int(os.environ.get('DUCK_MEDIA_PORT', 9528)))
        ipc = JsonServer(int(os.environ.get('DUCK_VOICE_PORT', 9530)), self.token, self.dispatch)
        ipc.start()
        self.mouth.start()
        onboarding_task = asyncio.create_task(self.onboarding.run())
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.loop.add_signal_handler(sig, self.stop.set)
        try:
            while not self.stop.is_set():
                cfg = self.config.get()
                if onboarding_task.done():
                    onboarding_task.result()
                    raise RuntimeError('Onboarding lifecycle stopped unexpectedly.')
                settings = ({k: v for k, v in cfg.items()
                             if k not in ('volume', 'voice_motion_enabled', 'remote_motion_enabled')},
                            self.onboarding.generation)
                if settings != self.session_settings:
                    await self.stop_session()
                    self.session_settings = settings
                    self.next_attempt = 0
                setup = self.onboarding.snapshot()
                cloud_connected = bool(self.app is not None and self.app.bound is True
                    and getattr(getattr(self.app, 'protocol', None), '_mqtt_connected', False))
                if cfg['enabled']:
                    self.connection_sounds.poll(setup.get('wifi_connected'), cloud_connected)
                ready = (cfg['enabled'] and not self.gate.busy() and self.onboarding.ready
                         and not (self.app is None and self.connection_sounds.network_busy))
                if not ready and self.session is not None:
                    await self.stop_session()
                if ready and (self.session is None or self.session.done()) and time.monotonic() >= self.next_attempt:
                    self.session = asyncio.create_task(self.chat_session())
                if not cfg['enabled']:
                    self.update(state='disabled', message='Chat disabled; original web media remains available.')
                elif not self.onboarding.ready:
                    setup = self.onboarding.snapshot()
                    self.update(state=setup['state'], bound=self.onboarding.bound,
                                message=setup.get('hardware_error') or
                                        ('Select China/International in the cockpit AI binding panel.'
                                         if setup['state'] == 'awaiting_cloud_config' else '') or
                                        'Waiting for Wi-Fi and phone account binding; USB audio is optional.')
                try:
                    session_id = (self.app._duck_session if self.app is not None and ready
                                  and not getattr(self.app, '_closing', False) else None)
                    await asyncio.to_thread(request, self.motion_port, self.token, '/heartbeat', {'session': session_id}, .3)
                except (OSError, ValueError):
                    pass
                parent = os.environ.get('DUCK_PARENT_PID')
                if parent and os.getppid() != int(parent):
                    self.stop.set()
                try:
                    await asyncio.wait_for(self.stop.wait(), .2)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.connection_sounds.close()
            await self.stop_session()
            await asyncio.to_thread(self.mouth.close)
            self.mouth_sender.close()
            self.onboarding.stop.set()
            onboarding_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await onboarding_task
            await asyncio.to_thread(self.bench.close)
            await asyncio.to_thread(media.shutdown)
            media.server_close()
            media_thread.join(timeout=2)
            await asyncio.to_thread(ipc.close)


def main():
    os.umask(0o077)
    private_directory()
    os.environ.update(LUWU_ROOT=str(VENDOR), DUCK_VOICE_STATE_DIR=str(PRIVATE),
        DUCK_DEVICE_ID=device_identity(), DUCK_BOARD_TYPE='XGO-Duck',
        DUCK_LANGUAGE_FILE=str(PRIVATE / 'language.ini'), DUCK_DOUBAO_CONFIG=str(PRIVATE / 'doubao.json'),
        DUCK_APP_VERSION_FILE=str(Path(__file__).resolve().parents[1] / 'version.txt'))
    sys.path.insert(0, str(VENDOR))
    sys.path.insert(0, str(VENDOR / 'apps/voice_chat'))
    token = os.environ['DUCK_IPC_TOKEN']
    print('[voice] device identity:', os.environ['DUCK_DEVICE_ID'], flush=True)
    asyncio.run(Runtime(token, int(os.environ.get('DUCK_MOTION_PORT', 9531))).run())


if __name__ == '__main__':
    main()
