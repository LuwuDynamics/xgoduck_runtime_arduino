#!/usr/bin/env python3
"""Linux ARM64 smoke check: real native SDK, Opus, AEC and wake-word graphs.

Does not open hardware, contact a backend, arm motors or provision a device.
"""
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'python/vendor/luwu_os'
sys.path[:0] = [str(ROOT / 'python'), str(VENDOR), str(VENDOR / 'apps/voice_chat'), str(VENDOR / 'apps/ai')]


def main():
    with tempfile.TemporaryDirectory() as directory:
        os.environ.update(LUWU_ROOT=str(VENDOR), DUCK_VOICE_STATE_DIR=directory,
            DUCK_LANGUAGE_FILE=directory + '/language.ini', DUCK_DEVICE_ID='02:00:00:00:00:01')
        import numpy as np
        import opuslib
        from application import Application
        from audio_codec import LocalAEC
        from duck_thing import DuckThing
        from wakeword_manager import WakeWordManager
        from voice_media import vision_type
        codec = opuslib.Encoder(16000, 1, opuslib.APPLICATION_VOIP)
        packet = codec.encode(b'\0' * 1920, 960)
        assert len(opuslib.Decoder(16000, 1).decode(packet, 960)) == 1920
        wake = WakeWordManager()
        assert wake._load_model()
        mel = wake._compute_mel(np.zeros(32000, dtype=np.float32))
        embeddings = wake._compute_embeddings(mel)
        assert np.isfinite(mel).all() and np.isfinite(embeddings).all()
        assert 0 <= wake._predict(np.zeros(32000, dtype=np.float32)) <= 1
        duck_wake = WakeWordManager(model_path=str(ROOT / 'python/models/xiaoya.onnx'),
                                    input_scale=1., threshold=.8, stride_duration=.08, cooldown_sec=2.)
        assert duck_wake._load_model()
        assert 0 <= duck_wake._predict(np.zeros(32000, dtype=np.float32)) < .8
        for path in (VENDOR / 'model').glob('*qwen.onnx'):
            import onnxruntime as ort
            session = ort.InferenceSession(str(path), providers=['CPUExecutionProvider'])
            assert session.get_inputs()
        aec = LocalAEC()
        print('AEC:', aec.available, aec.backend)
        assert aec.available, 'Bundled ARM64 AEC must load in the target Linux userspace.'
        aec.close()
        vision = vision_type()('test', 'test', type('Bench', (), {'camera_path': lambda _: None})())
        assert not vision.camera_available
        print('SDK import, Opus round trip, ARM64 AEC, wake-word graphs and USB vision adapter: PASS')


if __name__ == '__main__':
    main()
