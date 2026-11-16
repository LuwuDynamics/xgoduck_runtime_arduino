"""Keep downloadable browser examples aligned with the actual startup recipes."""
from pathlib import Path
import array
import sys
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from connection_sounds import KINDS, SOFTWARE_GAIN
from duck_sound import amplify_sound, sound_pcm


class StartupPreviewTest(unittest.TestCase):
    def test_both_wavs_are_complete_actual_connection_recipes(self):
        for event, name in [('network', 'startup-network.wav'), ('voice_service', 'startup-voice.wav')]:
            with self.subTest(event=event), wave.open(str(ROOT / 'assets' / name), 'rb') as source:
                self.assertEqual((source.getnchannels(), source.getsampwidth(), source.getframerate()), (1, 2, 16000))
                expected = amplify_sound(sound_pcm(KINDS[event], 'XGO-Duck', 0, 16000, 0), SOFTWARE_GAIN, 0)
                actual = source.readframes(source.getnframes())
                self.assertEqual(len(actual), len(expected), 'Keep the complete synthesized clip.')
                left, right = array.array('h'), array.array('h')
                left.frombytes(actual)
                right.frombytes(expected)
                # Permit one PCM step for cross-platform floating-point rounding.
                self.assertLessEqual(max(abs(a - b) for a, b in zip(left, right)), 1,
                                     'Regenerate the example if the real startup recipe changes.')
                self.assertGreater(source.getnframes(), 1600)
                self.assertLess(source.getnframes(), 32000)
                self.assertTrue(any(actual))


if __name__ == '__main__':
    unittest.main()
