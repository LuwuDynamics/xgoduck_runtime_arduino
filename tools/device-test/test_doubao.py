import base64
import io
import json
import threading
import unittest
import urllib.error
import wave

import doubao


class SynthesisTest(unittest.TestCase):
    config = dict(api_key='test-secret', speaker='S_test', resource_id='seed-icl-2.0',
                  model='seed-tts-2.0-standard')

    def response(self, items):
        return lambda *args, **kwargs: io.BytesIO(b'\n'.join(json.dumps(item).encode() for item in items))

    def test_stream_assembled_as_valid_wav(self):
        pcm = b'\x01\x00' * 240
        opener = self.response([{'code': 0, 'data': base64.b64encode(pcm).decode()},
                                {'code': 0, 'data': None}, {'code': 20000000}])
        data = doubao.synthesize(self.config, '测试', threading.Event(), opener)
        with wave.open(io.BytesIO(data)) as w:
            self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 24000))
            self.assertEqual(w.readframes(240), pcm)

    def test_truncated_stream_rejected(self):
        with self.assertRaisesRegex(ValueError, '不完整'):
            doubao.synthesize(self.config, '测试', threading.Event(),
                              self.response([{'code': 0, 'data': 'AAA='}]))

    def test_empty_audio_rejected(self):
        with self.assertRaisesRegex(ValueError, '不完整'):
            doubao.synthesize(self.config, '测试', threading.Event(), self.response([{'code': 20000000}]))

    def test_cancel_prevents_request(self):
        cancel = threading.Event()
        cancel.set()
        def opener(*args, **kwargs):
            self.fail('Cancelled request must not contact provider')
        with self.assertRaises(doubao.Cancelled):
            doubao.synthesize(self.config, '测试', cancel, opener)

    def test_error_redacts_key(self):
        with self.assertRaises(ValueError) as result:
            doubao.synthesize(self.config, '测试', threading.Event(),
                self.response([{'code': 123, 'message': 'problem test-secret'}]))
        self.assertNotIn('test-secret', str(result.exception))

    def test_http_error_does_not_reflect_response(self):
        def opener(*args, **kwargs):
            raise urllib.error.HTTPError(doubao.ENDPOINT, 403, 'private provider body', {}, None)
        with self.assertRaisesRegex(ValueError, '权限不足') as result:
            doubao.synthesize(self.config, '测试', threading.Event(), opener)
        self.assertNotIn('private provider body', str(result.exception))


if __name__ == '__main__':
    unittest.main()
