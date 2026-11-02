"""Small product adapter around the production AI Chat Pro SDK."""
import os
from pathlib import Path
from duck_sound import DuckSoundPlayer

WAKE_CUE = Path(__file__).with_name('assets') / 'wake-duck.wav'


def application_type(base, runtime):
    class DuckApplication(base):
        def __init__(self, config):
            super().__init__(config)
            self.aec_enabled = bool(config.get('enable_aec', False))
            self.duck_sounds = DuckSoundPlayer(self, getattr(runtime, 'config', None))

        def _get_volume(self):
            return runtime.config.get()['volume']

        def _wake_word(self):
            return 'Hi Luka' if self._lang() == 'en' else '小鸭小鸭'

        def _play_wake_sound(self):
            self.audio.play_wav(str(WAKE_CUE))

        def _handle_tts(self, data):
            if (data.get('state') == 'stop'
                    and getattr(self, '_is_server_tts_interrupt', lambda _: False)(data)):
                # An abort must not wait behind a local Duck.Quack sound or
                # replay its deferred old answer after the interruption.
                self.duck_sounds.cancel('server interrupted')
            if self.duck_sounds.defer('tts', dict(data)):
                return
            result = super()._handle_tts(data)
            mouth = getattr(runtime, 'mouth', None)
            if (mouth is not None and data.get('state') == 'start' and self.state == 'speaking'
                    and self.config.get('tts_mouth_enabled', True)):
                mouth.begin(self._duck_session)
            return result

        def _on_incoming_audio(self, payload, timestamp):
            if not self.duck_sounds.defer('audio', payload, timestamp):
                return super()._on_incoming_audio(payload, timestamp)

        def _on_incoming_json(self, data):
            if data.get('type') == 'stt' and data.get('text'):
                self.duck_sounds.cancel('new user speech')
            return super()._on_incoming_json(data)

        def _set_state(self, state):
            if state != self.state and hasattr(self, 'duck_sounds'):
                # Rendering/waiting for the previous answer can span normal
                # LISTENING/SPEAKING transitions. No local PCM is owned yet.
                # Active playback and session-ending transitions still cancel.
                player = self.duck_sounds
                if player.playing or state not in ('listening', 'speaking'):
                    player.cancel('chat state changed')
            result = super()._set_state(state)
            if self.state != 'speaking' and getattr(runtime, 'mouth', None) is not None:
                runtime.mouth.stop()
            return result

        async def abort_speaking(self, *args, **kwargs):
            self.duck_sounds.cancel('chat interrupted')
            if getattr(runtime, 'mouth', None) is not None:
                runtime.mouth.stop()
            return await super().abort_speaking(*args, **kwargs)

        async def shutdown(self):
            self.duck_sounds.cancel('chat stopped')
            if getattr(runtime, 'mouth', None) is not None:
                runtime.mouth.stop()
            return await super().shutdown()

        def _luwuos_version(self):
            return Path(os.environ['DUCK_APP_VERSION_FILE']).read_text().strip()

        def _device_sn(self):
            return os.environ.get('DUCK_DEVICE_ID', '').replace(':', '').upper()

        def mcp_get_system_info(self):
            info = super().mcp_get_system_info()
            info.update(mac_address=os.environ.get('DUCK_DEVICE_ID', ''), chip_model_name='Arduino UNO Q')
            info['application'].update(name='xgoduck-ai', version=self._luwuos_version())
            info['board'] = dict(model='XGO-Duck / Arduino UNO Q', sn=self._device_sn())
            info['screen'] = dict(present=False, width=0, height=0)
            info['audio'] = dict(available=bool(self.config.get('audio_available', True)))
            return info

        def _screen_resolution(self):
            return 0, 0

        def mcp_screen_get_info(self):
            return dict(present=False, width=0, height=0, monochrome=False)

        async def start_chat(self, wake_word=None):
            if not self.config.get('audio_available', True):
                runtime.update(state='waiting_audio', message='MCP service running; waiting for USB microphone and speaker.')
                return
            return await super().start_chat(wake_word)

        async def _refresh_credentials(self):
            if self._closing:
                return None
            settings = await runtime.onboarding.refresh_credentials(self.config.get('ota_url', ''))
            if self._closing:
                return None
            self.bound = runtime.onboarding.bound
            return settings

        def mcp_set_volume(self, volume):
            runtime.volume.apply(volume)
            return True

        def mcp_quack(self, kind):
            return self.duck_sounds.call(kind)

        def mcp_set_language(self, language):
            code = {'zh-CN': 'cn', 'en-US': 'en', 'cn': 'cn', 'en': 'en'}.get(language)
            if code is None:
                raise ValueError('language must be zh-CN/en-US')
            runtime.config.update({'language': code})
            # Runtime reopens the matching wake-word model. The locally chosen
            # cloud and its credentials remain independent of language.
            return 'Language set to: ' + ('zh-CN' if code == 'cn' else 'en-US')

        def mcp_set_aec(self, enable):
            result = super().mcp_set_aec(enable)
            runtime.config.update({'enable_aec': self.aec_enabled})
            return result

        def mcp_reboot(self):
            return False

    return DuckApplication
